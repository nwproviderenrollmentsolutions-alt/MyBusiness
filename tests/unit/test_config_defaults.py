from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import (
    AutonomyMode,
    PolicyConfig,
    get_agents_config,
    get_policy_config,
    get_settings,
)
from db.enums import ActionType


def test_unknown_action_is_approval_required():
    """Adding an action without a policy decision must fail closed, not default to send."""
    policy = PolicyConfig()
    assert policy.autonomy_for("some.brand.new.action") is AutonomyMode.APPROVAL_REQUIRED


def test_shipped_policy_requires_approval_for_every_contact_action():
    policy = get_policy_config()
    for action in (
        ActionType.OUTREACH_SEND_EMAIL,
        ActionType.OUTREACH_SEND_FOLLOWUP,
        ActionType.CONVERSATION_REPLY,
        ActionType.PROPOSAL_SEND,
    ):
        assert policy.autonomy_for(action) is AutonomyMode.APPROVAL_REQUIRED


def test_shipped_policy_never_auto_approves_on_expiry():
    assert get_policy_config().approval.on_expiry == "reject"


def test_approval_expiry_cannot_be_set_to_approve():
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate({"approval": {"on_expiry": "approve"}})


def test_dry_run_defaults_on():
    assert get_settings().dry_run is True


def test_every_provider_defaults_to_mock():
    settings = get_settings()
    assert {
        settings.llm_provider,
        settings.email_provider,
        settings.lead_discovery_provider,
        settings.web_research_provider,
        settings.crm_provider,
        settings.calendar_provider,
        settings.payment_provider,
    } == {"mock"}


def test_only_the_revenue_slice_agents_are_wired_so_far():
    """No Outreach, Sales, or Proposal agent yet; claiming one exists would be fake."""
    assert set(get_agents_config().agents) == {
        "chief_of_staff",
        "opportunity_discovery",
        "lead_discovery",
        "lead_enrichment",
        "lead_scoring",
    }
