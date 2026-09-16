"""config/agents.yaml is the real wiring path — prove it loads what it claims to."""

from __future__ import annotations

import pytest

from core.registry import AGENTS

pytestmark = pytest.mark.integration


def test_chief_of_staff_loads_from_the_shipped_config(db):
    loaded = AGENTS.load_from_config()

    assert "chief_of_staff" in loaded
    agent = AGENTS.get("chief_of_staff")
    assert agent.MANIFEST.name == "chief_of_staff"
    assert agent.MANIFEST.version


def test_no_domain_agents_are_wired_yet(db):
    """Milestone 2 ships zero business agents. Wiring one in without building it would be
    a fake capability."""
    loaded = AGENTS.load_from_config()
    assert loaded == ["chief_of_staff"]
