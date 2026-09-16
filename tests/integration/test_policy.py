"""Policy engine boundaries. These are the tests that decide whether the system is safe
to point at real people."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from config.settings import AutonomyMode
from core import flags, suppression, task_queue
from core import policy as policy_engine
from core.policy import PolicyContext
from db.enums import (
    ActionState,
    ActionType,
    CampaignStatus,
    Channel,
    LeadStatus,
    MessageDirection,
    PolicyDecision,
    SuppressionScope,
)
from db.models.market import Company, Prospect
from db.models.pipeline import Campaign, Conversation, Lead, Message
from db.models.runtime import AuditLog
from tests.conftest import make_policy

pytestmark = pytest.mark.integration

AUTONOMOUS_EMAIL = {
    ActionType.OUTREACH_SEND_EMAIL: AutonomyMode.AUTONOMOUS,
    ActionType.LEAD_DISCOVER: AutonomyMode.AUTONOMOUS,
    ActionType.RESEARCH_WEB_FETCH: AutonomyMode.AUTONOMOUS,
    ActionType.PROPOSAL_SEND: AutonomyMode.AUTONOMOUS,
}


@pytest.fixture
def campaign(db) -> Campaign:
    record = Campaign(name="Q1 HVAC", status=CampaignStatus.ACTIVE, channel=Channel.EMAIL)
    db.add(record)
    db.flush()
    return record


@pytest.fixture
def lead(db, campaign) -> Lead:
    company = Company(name="MOCK Acme", domain="acme.invalid", is_mock=True)
    db.add(company)
    db.flush()
    prospect = Prospect(
        company_id=company.id, full_name="MOCK Sam Rivera", email="sam@acme.invalid", is_mock=True
    )
    db.add(prospect)
    db.flush()
    record = Lead(
        prospect_id=prospect.id,
        company_id=company.id,
        campaign_id=campaign.id,
        status=LeadStatus.SCORED,
        is_mock=True,
    )
    db.add(record)
    db.flush()
    return record


def _send_ctx(lead: Lead, campaign: Campaign, **overrides) -> PolicyContext:
    defaults: dict[str, object] = {
        "action_type": ActionType.OUTREACH_SEND_EMAIL,
        "agent": "outreach",
        "dry_run": False,
        "campaign_id": campaign.id,
        "lead_id": lead.id,
        "recipient_email": "sam@acme.invalid",
    }
    return PolicyContext(**{**defaults, **overrides})


# --- Kill switches --------------------------------------------------------------------


def test_emergency_stop_blocks_outbound(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="something looks wrong")

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.denied
    assert decision.rule == "emergency_stop"


def test_emergency_stop_still_allows_internal_thinking(db, policy):
    """Stopping the outside world should not stop the system from reasoning about it."""
    policy(make_policy(autonomy={ActionType.LEAD_SCORE: AutonomyMode.AUTONOMOUS}))
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="pause outreach")

    decision = policy_engine.evaluate(
        db, PolicyContext(action_type=ActionType.LEAD_SCORE, agent="scoring", dry_run=False)
    )
    assert decision.allowed


def test_emergency_stop_can_be_released(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="pause")
    flags.release_emergency_stop(db, released_by="ceo", reason="resolved")

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).allowed


def test_campaign_kill_switch_blocks_that_campaign(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    campaign.status = CampaignStatus.KILLED
    campaign.kill_reason = "messaging was off"
    db.flush()

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.denied
    assert decision.rule == "campaign_killed"
    assert "messaging was off" in decision.reason


def test_paused_campaign_does_not_send(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    campaign.status = CampaignStatus.PAUSED
    db.flush()

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "campaign_not_active"


# --- Do not contact -------------------------------------------------------------------


def test_suppressed_email_is_never_contacted(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    suppression.add_suppression(
        db, scope=SuppressionScope.EMAIL, value="Sam@Acme.invalid", reason="asked to stop"
    )

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.denied
    assert decision.rule == "suppressed"


def test_suppressing_a_domain_covers_everyone_at_it(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    suppression.add_suppression(
        db, scope=SuppressionScope.DOMAIN, value="acme.invalid", reason="legal complaint"
    )

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "suppressed"


def test_suppression_outranks_everything_else(db, policy, lead, campaign):
    """Even with autonomy on and every limit clear, an opt-out wins."""
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    suppression.add_suppression(
        db, scope=SuppressionScope.EMAIL, value="sam@acme.invalid", reason="unsubscribed"
    )

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign, value_usd=Decimal("1")))
    assert decision.decision is PolicyDecision.DENY


# --- Duplicates and limits ------------------------------------------------------------


def _make_sent_message(db, lead: Lead, *, key: str, state=ActionState.COMPLETED) -> Message:
    conversation = db.scalar(select(Conversation).where(Conversation.lead_id == lead.id))
    if conversation is None:
        conversation = Conversation(lead_id=lead.id, campaign_id=lead.campaign_id)
        db.add(conversation)
        db.flush()
    message = Message(
        conversation_id=conversation.id,
        lead_id=lead.id,
        direction=MessageDirection.OUTBOUND,
        state=state,
        body="MOCK body",
        idempotency_key=key,
        is_mock=True,
    )
    db.add(message)
    db.flush()
    return message


def test_duplicate_idempotency_key_is_refused(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    _make_sent_message(db, lead, key="already-sent")

    decision = policy_engine.evaluate(
        db, _send_ctx(lead, campaign, idempotency_key="already-sent")
    )
    assert decision.denied
    assert decision.rule == "duplicate"


def test_global_daily_cap_stops_sending(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"global_daily_outbound_max": 2}))
    _make_sent_message(db, lead, key="a")
    _make_sent_message(db, lead, key="b")

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.rule == "global_daily_limit"


def test_approved_but_unsent_messages_count_against_the_cap(db, policy, lead, campaign):
    """Otherwise a burst of approvals could blow through the limit before any of them send."""
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"global_daily_outbound_max": 1}))
    _make_sent_message(db, lead, key="queued", state=ActionState.APPROVED)

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "global_daily_limit"


def test_campaign_cap_applies_per_campaign(db, policy, lead, campaign):
    policy(
        make_policy(
            autonomy=AUTONOMOUS_EMAIL,
            limits={"global_daily_outbound_max": 100, "campaign_daily_outbound_max": 1},
        )
    )
    _make_sent_message(db, lead, key="one")

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "campaign_daily_limit"


def test_a_prospect_is_not_contacted_forever(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"per_prospect_max_touches": 3}))
    lead.touch_count = 3
    db.flush()

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "per_prospect_max_touches"


def test_cooldown_prevents_back_to_back_messages(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"per_prospect_cooldown_hours": 72}))
    lead.last_touch_at = task_queue.db_now(db) - timedelta(hours=1)
    db.flush()

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).rule == "per_prospect_cooldown"


def test_sending_resumes_after_the_cooldown(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"per_prospect_cooldown_hours": 2}))
    lead.last_touch_at = task_queue.db_now(db) - timedelta(hours=3)
    db.flush()

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign)).allowed


# --- Approval requirements ------------------------------------------------------------


def test_default_posture_requires_approval_to_send(db, lead, campaign, policy):
    """Nothing reaches a person without a human saying yes, until the CEO changes that."""
    policy(make_policy())
    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.needs_approval
    assert decision.rule == "autonomy_off"


def test_payments_always_need_a_human_even_if_marked_autonomous(db, policy):
    policy(make_policy(autonomy={ActionType.PAYMENT_CHARGE: AutonomyMode.AUTONOMOUS}))

    decision = policy_engine.evaluate(
        db,
        PolicyContext(action_type=ActionType.PAYMENT_CHARGE, agent="finance", dry_run=False),
    )
    assert decision.needs_approval
    assert decision.rule == "always_approval"


def test_discounts_always_need_a_human(db, policy):
    policy(make_policy(autonomy={ActionType.DEAL_APPLY_DISCOUNT: AutonomyMode.AUTONOMOUS}))

    decision = policy_engine.evaluate(
        db, PolicyContext(action_type=ActionType.DEAL_APPLY_DISCOUNT, agent="sales", dry_run=False)
    )
    assert decision.rule == "always_approval"


@pytest.mark.parametrize(
    ("value", "expects_approval"),
    [(Decimal("999"), False), (Decimal("1000"), False), (Decimal("1001"), True)],
)
def test_value_threshold_boundary(db, policy, lead, campaign, value, expects_approval):
    policy(
        make_policy(
            autonomy=AUTONOMOUS_EMAIL,
            thresholds={"proposal_value_requires_approval_usd": 1000},
        )
    )
    decision = policy_engine.evaluate(
        db,
        _send_ctx(lead, campaign, action_type=ActionType.PROPOSAL_SEND, value_usd=value),
    )
    assert decision.needs_approval is expects_approval


def test_custom_copy_needs_approval_even_when_autonomous(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign, template_approved=False))
    assert decision.rule == "unapproved_template"


def test_disabled_action_is_refused_outright(db, policy, lead, campaign):
    policy(make_policy(autonomy={ActionType.OUTREACH_SEND_EMAIL: AutonomyMode.DISABLED}))

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign))
    assert decision.denied
    assert decision.rule == "action_disabled"


# --- Dry run --------------------------------------------------------------------------


def test_dry_run_simulates_sending(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign, dry_run=True))
    assert decision.decision is PolicyDecision.SIMULATE
    # Simulation is explicitly not permission to execute.
    assert decision.allowed is False


def test_dry_run_still_permits_research(db, policy):
    """Dry run means nobody gets contacted, not that the system stops working."""
    policy(make_policy(autonomy={ActionType.RESEARCH_WEB_FETCH: AutonomyMode.AUTONOMOUS}))

    decision = policy_engine.evaluate(
        db,
        PolicyContext(
            action_type=ActionType.RESEARCH_WEB_FETCH, agent="research", dry_run=True
        ),
    )
    assert decision.allowed


def test_dry_run_still_raises_approvals(db, policy, lead, campaign):
    """So the approval flow can be exercised before anything is live."""
    policy(make_policy())

    decision = policy_engine.evaluate(db, _send_ctx(lead, campaign, dry_run=True))
    assert decision.needs_approval


def test_dry_run_does_not_override_suppression(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    suppression.add_suppression(
        db, scope=SuppressionScope.EMAIL, value="sam@acme.invalid", reason="opted out"
    )

    assert policy_engine.evaluate(db, _send_ctx(lead, campaign, dry_run=True)).denied


# --- Audit ----------------------------------------------------------------------------


def test_every_decision_is_audited_including_allows(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    policy_engine.evaluate(db, _send_ctx(lead, campaign))
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "policy.evaluated"))
    assert entry.after["decision"] == "allow"
    assert entry.after["action_type"] == "outreach.send_email"
    assert entry.reason


def test_denials_record_which_rule_fired(db, policy, lead, campaign):
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="stop")
    policy_engine.evaluate(db, _send_ctx(lead, campaign))
    db.commit()

    entry = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "policy.evaluated")
        .order_by(AuditLog.created_at.desc())
    )
    assert entry.after["rule"] == "emergency_stop"


# --- Post-approval re-authorization -----------------------------------------------


def test_approved_action_executes_without_re_litigating_the_approval_tier(
    db, policy, lead, campaign
):
    """Autonomy-off and unapproved-template are what the human was asked to judge —
    evaluate_after_approval must not ask again."""
    policy(make_policy())  # default: approval_required for everything

    decision = policy_engine.evaluate_after_approval(
        db, _send_ctx(lead, campaign, template_approved=False)
    )
    assert decision.allowed
    assert decision.rule == "ceo_approved"


def test_dry_run_still_simulates_even_after_approval(db, policy, lead, campaign):
    policy(make_policy())

    decision = policy_engine.evaluate_after_approval(
        db, _send_ctx(lead, campaign, dry_run=True, template_approved=False)
    )
    assert decision.simulate
    assert decision.rule == "ceo_approved_dry_run"


def test_suppression_added_after_approval_still_blocks_the_send(db, policy, lead, campaign):
    """An approval from before the suppression is not permission to ignore it."""
    policy(make_policy())
    suppression.add_suppression(
        db, scope=SuppressionScope.EMAIL, value="sam@acme.invalid", reason="unsubscribed since"
    )

    decision = policy_engine.evaluate_after_approval(
        db, _send_ctx(lead, campaign, template_approved=False)
    )
    assert decision.denied
    assert decision.rule == "suppressed"


def test_emergency_stop_engaged_after_approval_still_blocks_the_send(db, policy, lead, campaign):
    policy(make_policy())
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="something looks wrong")

    decision = policy_engine.evaluate_after_approval(
        db, _send_ctx(lead, campaign, template_approved=False)
    )
    assert decision.denied
    assert decision.rule == "emergency_stop"


def test_post_approval_does_not_self_deny_on_the_message_it_is_about_to_send(
    db, policy, lead, campaign
):
    """The message row this approval covers already exists with this exact idempotency
    key — re-running the duplicate check would find itself and refuse the send."""
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL))
    _make_sent_message(db, lead, key="the-approved-message", state=ActionState.APPROVED)

    decision = policy_engine.evaluate_after_approval(
        db, _send_ctx(lead, campaign, idempotency_key="the-approved-message")
    )
    assert decision.allowed


def test_post_approval_does_not_self_deny_on_the_daily_cap_it_already_occupies(
    db, policy, lead, campaign
):
    """The message being sent is already counted as APPROVED against today's cap — the
    limit check that matters ran once already, before this row existed."""
    policy(make_policy(autonomy=AUTONOMOUS_EMAIL, limits={"global_daily_outbound_max": 1}))
    _make_sent_message(db, lead, key="occupies-the-cap", state=ActionState.APPROVED)

    decision = policy_engine.evaluate_after_approval(db, _send_ctx(lead, campaign))
    assert decision.allowed


def test_post_approval_decisions_are_audited(db, policy, lead, campaign):
    policy(make_policy())
    policy_engine.evaluate_after_approval(db, _send_ctx(lead, campaign, template_approved=False))
    db.commit()

    entry = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "policy.evaluated")
        .order_by(AuditLog.created_at.desc())
    )
    assert entry.after["post_approval"] is True
    assert entry.after["rule"] == "ceo_approved"
