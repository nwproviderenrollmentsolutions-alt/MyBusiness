"""The policy engine: the single gate every external-world action passes through.

Deliberately deterministic code, not an LLM. An LLM must not be the thing that decides
whether an LLM's action is allowed (ARCHITECTURE.md §12, conflict 4).

Rules are evaluated in a fixed order and the first match wins. Denials come before
approvals so that a suppressed recipient or a tripped kill switch can never be overridden
by a human clicking approve on a queue item.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import AutonomyMode, get_policy_config
from core import audit, flags, suppression
from core.task_queue import db_now
from db.enums import (
    ALWAYS_APPROVAL_ACTIONS,
    CONTACT_ACTIONS,
    EXTERNAL_ACTIONS,
    WORLD_CHANGING_ACTIONS,
    ActionState,
    ActionType,
    ActorType,
    CampaignStatus,
    MessageDirection,
    PolicyDecision,
)
from db.models.pipeline import Campaign, Lead, Message
from db.models.runtime import AgentRun

#: States in which a send is already committed and must count against daily limits,
#: even though it has not physically left yet.
_COMMITTED_SEND_STATES = (ActionState.APPROVED, ActionState.EXECUTING, ActionState.COMPLETED)


@dataclass(frozen=True)
class PolicyContext:
    """Everything the engine needs to decide. Assembled by the caller, never by an LLM."""

    action_type: ActionType
    agent: str
    dry_run: bool = True
    campaign_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    recipient_email: str | None = None
    recipient_phone: str | None = None
    value_usd: Decimal | None = None
    discount_pct: Decimal | None = None
    idempotency_key: str | None = None
    #: False when an agent wrote custom copy instead of using an approved template.
    template_approved: bool = True
    task_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    decision: PolicyDecision
    rule: str
    reason: str
    #: Which action this decision authorizes. The tool registry checks it so an
    #: authorization for a harmless action cannot be reused for a dangerous one.
    action_type: ActionType | None = None

    @property
    def allowed(self) -> bool:
        """True only for a real execution. Simulation is not execution."""
        return self.decision is PolicyDecision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision is PolicyDecision.REQUIRE_APPROVAL

    @property
    def denied(self) -> bool:
        return self.decision is PolicyDecision.DENY

    @property
    def simulate(self) -> bool:
        return self.decision is PolicyDecision.SIMULATE


def evaluate(session: Session, ctx: PolicyContext, *, record_audit: bool = True) -> Decision:
    """Authorize one proposed action.

    Every evaluation is written to the audit log, including allows, so the CEO can always
    answer "why did this go out?" from the record alone.
    """
    decision = replace(_evaluate(session, ctx), action_type=ctx.action_type)
    if record_audit:
        audit.record(
            session,
            actor_type=ActorType.AGENT,
            actor=ctx.agent,
            action="policy.evaluated",
            subject_type="policy",
            subject_id=ctx.lead_id or ctx.campaign_id,
            after={
                "action_type": str(ctx.action_type),
                "decision": str(decision.decision),
                "rule": decision.rule,
                "dry_run": ctx.dry_run,
            },
            reason=decision.reason,
            task_id=ctx.task_id,
            correlation_id=ctx.correlation_id,
        )
    return decision


def evaluate_after_approval(
    session: Session, ctx: PolicyContext, *, record_audit: bool = True
) -> Decision:
    """Re-authorize an action a human already approved, immediately before executing it.

    Approval satisfies the *require-approval* tier this context would otherwise hit —
    autonomy off, an unapproved template, a value/discount threshold. That is what the
    human was asked to judge, and this function does not ask it again. It does not
    satisfy the *deny* tier: if the recipient has been suppressed, a kill switch engaged,
    or the campaign killed since the approval was requested, execution is still blocked.
    An approval granted an hour ago is not permission to ignore what changed since.

    Deliberately narrower than a full re-``evaluate``: the duplicate check and volume
    limits are excluded on purpose. By the time this runs, the message row the approval
    covers already exists (and, once ``core.action_state_sync`` has processed the
    approval, is already in the ``approved`` state) — re-running the duplicate check
    would find that exact row and reject the action as a duplicate of itself, and
    re-running the volume-limit count would count that same row against itself,
    denying a send that is precisely at, not over, the limit. Both checks already ran
    correctly once, before the row existed, at draft time.
    """
    ctx_without_idempotency = replace(ctx, idempotency_key=None)
    gate = _safety_gates(session, ctx_without_idempotency)
    if gate is not None:
        decision = replace(gate, action_type=ctx.action_type)
    elif ctx.dry_run and ctx.action_type in WORLD_CHANGING_ACTIONS:
        decision = Decision(
            PolicyDecision.SIMULATE,
            "ceo_approved_dry_run",
            "approved by the CEO; simulated because dry run is on",
            action_type=ctx.action_type,
        )
    else:
        decision = Decision(
            PolicyDecision.ALLOW,
            "ceo_approved",
            "approved by the CEO",
            action_type=ctx.action_type,
        )

    if record_audit:
        audit.record(
            session,
            actor_type=ActorType.AGENT,
            actor=ctx.agent,
            action="policy.evaluated",
            subject_type="policy",
            subject_id=ctx.lead_id or ctx.campaign_id,
            after={
                "action_type": str(ctx.action_type),
                "decision": str(decision.decision),
                "rule": decision.rule,
                "dry_run": ctx.dry_run,
                "post_approval": True,
            },
            reason=decision.reason,
            task_id=ctx.task_id,
            correlation_id=ctx.correlation_id,
        )
    return decision


def _safety_gates(session: Session, ctx: PolicyContext) -> Decision | None:
    """Rules 1-3 and 5: the ones that must hold even for an action a human already
    approved. A recipient can ask to be suppressed, or the CEO can hit the kill switch,
    at any moment — an approval granted before that moment is not permission to ignore
    it. ``evaluate_after_approval`` re-runs only this subset; volume limits and the
    duplicate check are deliberately excluded (see that function's docstring).
    """
    policy = get_policy_config()
    is_external = ctx.action_type in EXTERNAL_ACTIONS
    is_contact = ctx.action_type in CONTACT_ACTIONS

    # 1. Global emergency stop. Halts anything that reaches the outside world; internal
    #    work like scoring and drafting stays available so the CEO can keep investigating.
    if is_external and flags.is_emergency_stopped(session):
        return Decision(
            PolicyDecision.DENY,
            "emergency_stop",
            "global emergency stop is engaged",
        )

    # 2. Action switched off entirely.
    autonomy = policy.autonomy_for(ctx.action_type)
    if autonomy is AutonomyMode.DISABLED:
        return Decision(
            PolicyDecision.DENY,
            "action_disabled",
            f"{ctx.action_type} is disabled in policy",
        )

    # 3. Do-not-contact. Outranks everything, including an explicit approval.
    if is_contact:
        entry = suppression.find_suppression(
            session, email=ctx.recipient_email, phone=ctx.recipient_phone
        )
        if entry is not None:
            return Decision(
                PolicyDecision.DENY,
                "suppressed",
                f"recipient is on the do-not-contact list ({entry.scope}: {entry.reason})",
            )

    # 5. Campaign kill switch.
    if is_contact and ctx.campaign_id is not None:
        campaign = session.get(Campaign, ctx.campaign_id)
        if campaign is None:
            return Decision(PolicyDecision.DENY, "campaign_missing", "campaign does not exist")
        if campaign.status is CampaignStatus.KILLED:
            return Decision(
                PolicyDecision.DENY,
                "campaign_killed",
                f"campaign kill switch engaged: {campaign.kill_reason or 'no reason given'}",
            )
        if campaign.status is not CampaignStatus.ACTIVE:
            return Decision(
                PolicyDecision.DENY,
                "campaign_not_active",
                f"campaign is {campaign.status}",
            )

    return None


def _evaluate(session: Session, ctx: PolicyContext) -> Decision:
    policy = get_policy_config()
    is_external = ctx.action_type in EXTERNAL_ACTIONS
    is_contact = ctx.action_type in CONTACT_ACTIONS
    autonomy = policy.autonomy_for(ctx.action_type)

    gate = _safety_gates(session, ctx)
    if gate is not None:
        return gate

    # 4. Duplicate. The same logical send must never happen twice.
    if ctx.idempotency_key:
        existing = suppression.message_exists(session, ctx.idempotency_key)
        if existing is not None:
            return Decision(
                PolicyDecision.DENY,
                "duplicate",
                f"message {existing.id} already exists for this idempotency key",
            )

    # 6. Volume limits. Safety rails, not targets.
    if is_contact:
        limit_decision = _check_send_limits(session, ctx)
        if limit_decision is not None:
            return limit_decision

    # 7. Spend cap.
    if is_external:
        spend_decision = _check_cost_cap(session)
        if spend_decision is not None:
            return spend_decision

    # 8. Money always needs a human, whatever the autonomy setting says.
    if ctx.action_type in ALWAYS_APPROVAL_ACTIONS:
        return Decision(
            PolicyDecision.REQUIRE_APPROVAL,
            "always_approval",
            f"{ctx.action_type} always requires human approval",
        )

    # 9. Value and discount thresholds.
    thresholds = policy.thresholds
    if ctx.discount_pct is not None and float(ctx.discount_pct) > (
        thresholds.discount_pct_requires_approval
    ):
        return Decision(
            PolicyDecision.REQUIRE_APPROVAL,
            "discount_threshold",
            f"discount {ctx.discount_pct}% exceeds "
            f"{thresholds.discount_pct_requires_approval}%",
        )
    if ctx.value_usd is not None:
        cap = (
            thresholds.proposal_value_requires_approval_usd
            if ctx.action_type is ActionType.PROPOSAL_SEND
            else thresholds.deal_value_requires_approval_usd
        )
        if float(ctx.value_usd) > cap:
            return Decision(
                PolicyDecision.REQUIRE_APPROVAL,
                "value_threshold",
                f"value ${ctx.value_usd} exceeds ${cap}",
            )

    # 10. Copy the CEO has not pre-approved.
    if is_contact and not ctx.template_approved:
        return Decision(
            PolicyDecision.REQUIRE_APPROVAL,
            "unapproved_template",
            "message does not use an approved template",
        )

    # 11. Autonomy switch for this action type.
    if autonomy is AutonomyMode.APPROVAL_REQUIRED:
        return Decision(
            PolicyDecision.REQUIRE_APPROVAL,
            "autonomy_off",
            f"{ctx.action_type} is set to approval_required",
        )

    # 12. Authorized — but in dry run, anything that would change the outside world is
    #     simulated. Research still executes: dry run means nobody is contacted, not that
    #     the system stops thinking.
    if ctx.dry_run and ctx.action_type in WORLD_CHANGING_ACTIONS:
        return Decision(
            PolicyDecision.SIMULATE,
            "dry_run",
            "dry run: action is authorized but will be simulated, not executed",
        )

    return Decision(PolicyDecision.ALLOW, "allowed", "permitted by policy")


def _check_send_limits(session: Session, ctx: PolicyContext) -> Decision | None:
    policy = get_policy_config()
    limits = policy.limits
    now = db_now(session)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    sent_today = (
        session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.direction == MessageDirection.OUTBOUND,
                Message.state.in_(_COMMITTED_SEND_STATES),
                Message.created_at >= day_start,
            )
        )
        or 0
    )
    if sent_today >= limits.global_daily_outbound_max:
        return Decision(
            PolicyDecision.DENY,
            "global_daily_limit",
            f"global daily send limit reached ({sent_today}/{limits.global_daily_outbound_max})",
        )

    if ctx.campaign_id is not None:
        campaign = session.get(Campaign, ctx.campaign_id)
        campaign_cap = min(
            limits.campaign_daily_outbound_max,
            campaign.daily_send_limit if campaign else limits.campaign_daily_outbound_max,
        )
        campaign_sent = (
            session.scalar(
                select(func.count())
                .select_from(Message)
                .join(Lead, Lead.id == Message.lead_id)
                .where(
                    Lead.campaign_id == ctx.campaign_id,
                    Message.direction == MessageDirection.OUTBOUND,
                    Message.state.in_(_COMMITTED_SEND_STATES),
                    Message.created_at >= day_start,
                )
            )
            or 0
        )
        if campaign_sent >= campaign_cap:
            return Decision(
                PolicyDecision.DENY,
                "campaign_daily_limit",
                f"campaign daily send limit reached ({campaign_sent}/{campaign_cap})",
            )

    if ctx.lead_id is not None:
        lead = session.get(Lead, ctx.lead_id)
        if lead is not None:
            if lead.touch_count >= limits.per_prospect_max_touches:
                return Decision(
                    PolicyDecision.DENY,
                    "per_prospect_max_touches",
                    f"prospect already contacted {lead.touch_count} times "
                    f"(max {limits.per_prospect_max_touches})",
                )
            if lead.last_touch_at is not None:
                cooldown_ends = lead.last_touch_at + timedelta(
                    hours=limits.per_prospect_cooldown_hours
                )
                if now < cooldown_ends:
                    return Decision(
                        PolicyDecision.DENY,
                        "per_prospect_cooldown",
                        f"prospect contacted recently; cooldown ends {cooldown_ends.isoformat()}",
                    )
    return None


def _check_cost_cap(session: Session) -> Decision | None:
    policy = get_policy_config()
    now = db_now(session)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spent = session.scalar(
        select(func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
            AgentRun.started_at >= day_start
        )
    ) or Decimal("0")
    if float(spent) >= policy.cost_caps.global_daily_usd:
        return Decision(
            PolicyDecision.DENY,
            "daily_cost_cap",
            f"daily spend cap reached (${spent} / ${policy.cost_caps.global_daily_usd})",
        )
    return None
