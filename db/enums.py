"""Single source of truth for every explicit state in the system.

Three separate lifecycles, deliberately not merged into one enum (ARCHITECTURE.md §12):

* ``ActionState``  - artifacts with an external-world effect (messages, proposals)
* ``TaskStatus``   - queue lifecycle of a unit of agent work
* ``LeadStatus``   - where a person stands in the funnel, including do-not-contact

This module imports nothing from the project so anything may depend on it.
"""

from __future__ import annotations

from enum import StrEnum


class ActionType(StrEnum):
    """Every action an agent can propose.

    Adding an external action requires adding it here *and* giving it a policy in
    config/policies.yaml. Unlisted actions are approval-required by default.
    """

    OPPORTUNITY_DISCOVER = "opportunity.discover"
    LEAD_DISCOVER = "lead.discover"
    LEAD_ENRICH = "lead.enrich"
    LEAD_SCORE = "lead.score"
    RESEARCH_WEB_FETCH = "research.web_fetch"
    CRM_SYNC = "crm.sync"

    OUTREACH_SEND_EMAIL = "outreach.send_email"
    OUTREACH_SEND_FOLLOWUP = "outreach.send_followup"
    CONVERSATION_REPLY = "conversation.reply"
    MEETING_SCHEDULE = "meeting.schedule"

    PROPOSAL_DRAFT = "proposal.draft"
    PROPOSAL_SEND = "proposal.send"

    DEAL_UPDATE_STAGE = "deal.update_stage"
    DEAL_APPLY_DISCOUNT = "deal.apply_discount"

    CUSTOMER_CREATE = "customer.create"
    PAYMENT_CHARGE = "payment.charge"


#: Actions that reach the outside world. Subject to dry-run, suppression, and limits.
EXTERNAL_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.RESEARCH_WEB_FETCH,
        ActionType.LEAD_DISCOVER,
        ActionType.LEAD_ENRICH,
        ActionType.CRM_SYNC,
        ActionType.OUTREACH_SEND_EMAIL,
        ActionType.OUTREACH_SEND_FOLLOWUP,
        ActionType.CONVERSATION_REPLY,
        ActionType.MEETING_SCHEDULE,
        ActionType.PROPOSAL_SEND,
        ActionType.PAYMENT_CHARGE,
    }
)

#: Actions that contact a person. Always suppression- and rate-limit-checked.
CONTACT_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.OUTREACH_SEND_EMAIL,
        ActionType.OUTREACH_SEND_FOLLOWUP,
        ActionType.CONVERSATION_REPLY,
        ActionType.PROPOSAL_SEND,
    }
)

#: Actions that move money or commit the company financially. These always require
#: human approval; no autonomy setting can override this (enforced in core/policy.py).
ALWAYS_APPROVAL_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.PAYMENT_CHARGE,
        ActionType.DEAL_APPLY_DISCOUNT,
    }
)


class ActionState(StrEnum):
    """Lifecycle of an artifact with an external-world effect."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    SUPPRESSED = "suppressed"


#: Legal transitions. Enforced by core.states.assert_transition.
ACTION_STATE_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.DRAFT: frozenset(
        {ActionState.PENDING_APPROVAL, ActionState.APPROVED, ActionState.SUPPRESSED}
    ),
    ActionState.PENDING_APPROVAL: frozenset(
        {ActionState.APPROVED, ActionState.REJECTED, ActionState.SUPPRESSED}
    ),
    ActionState.APPROVED: frozenset(
        {ActionState.EXECUTING, ActionState.SUPPRESSED, ActionState.REJECTED}
    ),
    ActionState.EXECUTING: frozenset({ActionState.COMPLETED, ActionState.FAILED}),
    ActionState.FAILED: frozenset({ActionState.EXECUTING}),  # retry
    ActionState.COMPLETED: frozenset(),
    ActionState.REJECTED: frozenset(),
    ActionState.SUPPRESSED: frozenset(),
}

TERMINAL_ACTION_STATES: frozenset[ActionState] = frozenset(
    {ActionState.COMPLETED, ActionState.REJECTED, ActionState.SUPPRESSED}
)


class TaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED, TaskStatus.DEAD_LETTER}
)


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    NEEDS_APPROVAL = "needs_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OpportunityStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    APPROVED = "approved"
    ACTIVE = "active"
    SHELVED = "shelved"


class LeadStatus(StrEnum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    SCORED = "scored"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    DO_NOT_CONTACT = "do_not_contact"


#: do_not_contact and disqualified are reachable from anywhere; a person can opt out
#: at any point in the funnel and that must always win.
LEAD_STATUS_TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    LeadStatus.DISCOVERED: frozenset({LeadStatus.ENRICHED}),
    LeadStatus.ENRICHED: frozenset({LeadStatus.SCORED}),
    LeadStatus.SCORED: frozenset({LeadStatus.CONTACTED}),
    LeadStatus.CONTACTED: frozenset({LeadStatus.ENGAGED}),
    LeadStatus.ENGAGED: frozenset({LeadStatus.QUALIFIED}),
    LeadStatus.QUALIFIED: frozenset(),
    LeadStatus.DISQUALIFIED: frozenset(),
    LeadStatus.DO_NOT_CONTACT: frozenset(),
}

ALWAYS_REACHABLE_LEAD_STATUSES: frozenset[LeadStatus] = frozenset(
    {LeadStatus.DISQUALIFIED, LeadStatus.DO_NOT_CONTACT}
)


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    KILLED = "killed"  # kill switch; terminal, cannot be resumed
    COMPLETED = "completed"


class ConversationStatus(StrEnum):
    OPEN = "open"
    AWAITING_REPLY = "awaiting_reply"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    CLOSED = "closed"


class Channel(StrEnum):
    EMAIL = "email"
    LINKEDIN = "linkedin"
    PHONE = "phone"
    SMS = "sms"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class DealStage(StrEnum):
    QUALIFICATION = "qualification"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


OPEN_DEAL_STAGES: frozenset[DealStage] = frozenset(
    {DealStage.QUALIFICATION, DealStage.PROPOSAL, DealStage.NEGOTIATION}
)


class CustomerStatus(StrEnum):
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    CHURNED = "churned"


class ActorType(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"


class SuppressionScope(StrEnum):
    EMAIL = "email"
    DOMAIN = "domain"
    PHONE = "phone"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    #: Authorized, but dry run is on: simulate instead of executing. Distinct from ALLOW
    #: so no caller can treat "we would have been allowed" as permission to act for real.
    SIMULATE = "simulate"
