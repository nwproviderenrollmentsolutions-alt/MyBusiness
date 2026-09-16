"""No path from an agent to the outside world may skip the allowlist or the policy engine."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from core.errors import PolicyViolation, ToolAccessDenied
from core.policy import Decision
from core.tool_registry import TOOLS, ToolBelt, ToolSpec
from db.enums import ActionType, PolicyDecision
from db.models.runtime import AuditLog
from providers.factory import get_email_provider

pytestmark = pytest.mark.integration

ALLOW_EMAIL = Decision(
    PolicyDecision.ALLOW, "allowed", "permitted", action_type=ActionType.OUTREACH_SEND_EMAIL
)
SIMULATE_EMAIL = Decision(
    PolicyDecision.SIMULATE, "dry_run", "simulated", action_type=ActionType.OUTREACH_SEND_EMAIL
)
DENY_EMAIL = Decision(
    PolicyDecision.DENY, "suppressed", "on the DNC list", action_type=ActionType.OUTREACH_SEND_EMAIL
)

SEND_KWARGS = {
    "to": "sam@acme.invalid",
    "subject": "MOCK subject",
    "body": "MOCK body",
    "idempotency_key": "key-1",
}


def _belt(db, *, allowed: set[str]) -> ToolBelt:
    return ToolBelt(agent="outreach", allowed=frozenset(allowed), session=db, registry=TOOLS)


def test_agent_cannot_call_a_tool_missing_from_its_manifest(db):
    belt = _belt(db, allowed={"llm.complete"})

    with pytest.raises(ToolAccessDenied, match="may not call tool"):
        belt.call("email.send", decision=ALLOW_EMAIL, **SEND_KWARGS)

    assert get_email_provider().sent == []


def test_denied_tool_access_is_audited_as_a_security_event(db):
    belt = _belt(db, allowed={"llm.complete"})
    with pytest.raises(ToolAccessDenied):
        belt.call("email.send", decision=ALLOW_EMAIL, **SEND_KWARGS)
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "tool.denied"))
    assert entry.after["tool"] == "email.send"
    assert "allowlist" in entry.reason


def test_unknown_tool_is_refused(db):
    with pytest.raises(ToolAccessDenied, match="does not exist"):
        _belt(db, allowed={"anything"}).call("no.such.tool")


def test_gated_tool_cannot_be_called_without_a_policy_decision(db):
    belt = _belt(db, allowed={"email.send"})

    with pytest.raises(PolicyViolation, match="requires a policy decision"):
        belt.call("email.send", **SEND_KWARGS)

    assert get_email_provider().sent == []


def test_a_denial_blocks_the_call(db):
    belt = _belt(db, allowed={"email.send"})

    with pytest.raises(PolicyViolation) as exc:
        belt.call("email.send", decision=DENY_EMAIL, **SEND_KWARGS)

    assert exc.value.rule == "suppressed"
    assert get_email_provider().sent == []


def test_authorization_for_one_action_cannot_be_reused_for_another(db):
    """A decision permitting lead discovery must not unlock sending email."""
    belt = _belt(db, allowed={"email.send"})
    discovery_decision = Decision(
        PolicyDecision.ALLOW, "allowed", "permitted", action_type=ActionType.LEAD_DISCOVER
    )

    with pytest.raises(PolicyViolation, match="does not authorize"):
        belt.call("email.send", decision=discovery_decision, **SEND_KWARGS)

    assert get_email_provider().sent == []


def test_allowed_call_reaches_the_provider(db):
    belt = _belt(db, allowed={"email.send"})

    receipt = belt.call("email.send", decision=ALLOW_EMAIL, **SEND_KWARGS)

    assert receipt.accepted is True
    assert len(get_email_provider().sent) == 1
    assert belt.calls[0].tool == "email.send"
    assert belt.calls[0].ok is True
    assert belt.calls[0].simulated is False


def test_dry_run_never_reaches_the_provider(db):
    """Simulation is enforced here, not left to the provider's good manners."""
    belt = _belt(db, allowed={"email.send"})

    receipt = belt.call("email.send", decision=SIMULATE_EMAIL, **SEND_KWARGS)

    assert receipt.accepted is True
    assert receipt.provider == "simulated"
    assert get_email_provider().sent == []
    assert belt.calls[0].simulated is True


def test_a_tool_with_no_simulation_refuses_to_run_in_dry_run(db, tools_registry):
    TOOLS.register(
        ToolSpec(
            name="test.unsimulatable",
            fn=lambda: "executed for real",
            description="",
            requires_policy=True,
            allowed_action_types=frozenset({ActionType.CRM_SYNC}),
        )
    )
    belt = _belt(db, allowed={"test.unsimulatable"})
    decision = Decision(
        PolicyDecision.SIMULATE, "dry_run", "simulated", action_type=ActionType.CRM_SYNC
    )

    with pytest.raises(PolicyViolation, match="no simulation"):
        belt.call("test.unsimulatable", decision=decision)


def test_ungated_tools_need_no_decision(db):
    """Reading our own inbox contacts nobody, so it carries no policy gate."""
    result = _belt(db, allowed={"email.fetch_replies"}).call("email.fetch_replies")
    assert result.messages == []


def test_tool_failures_are_recorded_and_reraised(db, tools_registry):
    def _boom() -> None:
        raise RuntimeError("provider exploded")

    TOOLS.register(ToolSpec(name="test.boom", fn=_boom, description=""))
    belt = _belt(db, allowed={"test.boom"})

    with pytest.raises(RuntimeError, match="provider exploded"):
        belt.call("test.boom")

    assert belt.calls[0].ok is False
    assert "provider exploded" in belt.calls[0].error
