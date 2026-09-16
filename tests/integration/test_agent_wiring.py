"""config/agents.yaml is the real wiring path — prove it loads what it claims to."""

from __future__ import annotations

import pytest

from core.registry import AGENTS

pytestmark = pytest.mark.integration

_EXPECTED_AGENTS = {
    "chief_of_staff",
    "opportunity_discovery",
    "lead_discovery",
    "lead_enrichment",
    "lead_scoring",
    "outreach",
    "conversation_management",
}


def test_all_shipped_agents_load_from_config(db):
    loaded = AGENTS.load_from_config()

    assert set(loaded) == _EXPECTED_AGENTS
    for name in _EXPECTED_AGENTS:
        agent = AGENTS.get(name)
        assert agent.MANIFEST.name == name
        assert agent.MANIFEST.version


def test_no_agents_beyond_the_revenue_slice_are_wired_yet():
    """Qualification, Sales, and Proposal don't exist yet. Wiring one in without
    building it would be a fake capability."""
    from config.settings import get_agents_config

    assert set(get_agents_config().agents) == _EXPECTED_AGENTS


def test_discovery_and_outreach_subscriptions_match_the_events_each_agent_emits():
    """The event/payload contract (ARCHITECTURE.md): a subscriber's declared input schema
    must match what the emitting agent puts in the event payload — spot-check the wiring
    that makes the chain actually connect end to end."""
    from config.settings import get_agents_config

    agents_config = get_agents_config()
    assert agents_config.subscribers_of("opportunity.discovered") == ["lead_discovery"]
    assert agents_config.subscribers_of("lead.discovered") == ["lead_enrichment"]
    assert agents_config.subscribers_of("lead.enriched") == ["lead_scoring"]
    assert agents_config.subscribers_of("lead.scored") == ["outreach"]
    # Conversation Management has no subscription — see config/agents.yaml's comment on
    # why (no periodic-polling mechanism exists yet).
    assert agents_config.subscribers_of("outreach.sent") == []
