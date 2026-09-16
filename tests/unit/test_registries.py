from __future__ import annotations

import pytest

from core.agent_base import Agent, AgentContext, AgentInput, AgentManifest, AgentOutput
from core.errors import AgentNotRegistered, ToolAccessDenied
from core.registry import AgentRegistry
from core.task_queue import backoff_seconds
from core.tool_registry import ToolRegistry, ToolSpec
from db.enums import ActionType


class _Input(AgentInput):
    value: str = "x"


def _agent(name: str = "demo", **manifest_overrides) -> Agent:
    manifest_kwargs = {
        "name": name,
        "version": "1.0.0",
        "description": "test agent",
        "input_model": _Input,
        **manifest_overrides,
    }

    class _Demo(Agent):
        MANIFEST = AgentManifest(**manifest_kwargs)

        def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
            return AgentOutput.success()

    return _Demo()


def test_registry_rejects_unknown_agent():
    with pytest.raises(AgentNotRegistered):
        AgentRegistry().get("nope")


def test_registry_rejects_duplicate_registration():
    registry = AgentRegistry()
    registry.register(_agent())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_agent())


def test_agent_must_declare_a_positive_timeout():
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="positive timeout"):
        registry.register(_agent(timeout_seconds=0))


def test_agent_cannot_both_allow_and_gate_the_same_action():
    """An action listed as free and as approval-required is a contradiction, not a default."""
    registry = AgentRegistry()
    with pytest.raises(ValueError, match="both freely allowed"):
        registry.register(
            _agent(
                allowed_actions=[ActionType.OUTREACH_SEND_EMAIL],
                approval_required_actions=[ActionType.OUTREACH_SEND_EMAIL],
            )
        )


def test_tool_registry_rejects_unknown_tool():
    with pytest.raises(ToolAccessDenied):
        ToolRegistry().get("nope")


def test_policy_gated_tool_must_declare_action_types():
    """Otherwise any decision at all would authorize it."""
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no allowed action types"):
        registry.register(
            ToolSpec(name="x.y", fn=lambda: None, description="", requires_policy=True)
        )


def test_backoff_grows_and_is_capped():
    first = [backoff_seconds(1) for _ in range(50)]
    later = [backoff_seconds(4) for _ in range(50)]
    assert max(first) < min(later)
    # Capped so a long-failing task keeps retrying on a sane interval instead of hours out.
    assert max(backoff_seconds(50) for _ in range(50)) <= 300.0 * 1.5
