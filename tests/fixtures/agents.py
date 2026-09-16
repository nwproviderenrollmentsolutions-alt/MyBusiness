"""Fixture agents used to exercise the runtime.

These are test doubles for the worker, not business agents. Real agents arrive in
Milestone 2 and later.
"""

from __future__ import annotations

import time
from decimal import Decimal

from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    ApprovalRequest,
    EventRequest,
    TaskRequest,
)
from core.errors import AgentError
from db.enums import ActionType, RiskLevel


class EchoInput(AgentInput):
    message: str
    spawn_follow_up: bool = False
    emit_event: bool = False


class NoInput(AgentInput):
    """For fixture agents whose behavior does not depend on input."""


class EchoAgent(Agent):
    """Succeeds, optionally emitting an event and a follow-up task."""

    MANIFEST = AgentManifest(
        name="echo",
        version="1.0.0",
        description="Returns its input.",
        input_model=EchoInput,
        allowed_tools=["llm.complete"],
        timeout_seconds=10,
        max_attempts=3,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, EchoInput)
        response = ctx.tools.call("llm.complete", prompt=agent_input.message)
        output = AgentOutput.success(result={"echoed": agent_input.message, "llm": response.text})
        output.usage.add_llm(
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
        )
        if agent_input.emit_event:
            output.events.append(
                EventRequest(event_type="echo.completed", payload={"message": agent_input.message})
            )
        if agent_input.spawn_follow_up:
            output.follow_up_tasks.append(
                TaskRequest(agent="echo", task_input={"message": "follow-up"})
            )
        return output


class ForbiddenToolAgent(Agent):
    """Tries to use a tool it never declared. The runtime must stop it."""

    MANIFEST = AgentManifest(
        name="forbidden_tool",
        version="1.0.0",
        description="Attempts an undeclared tool.",
        input_model=NoInput,
        allowed_tools=["llm.complete"],
        timeout_seconds=10,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        ctx.tools.call(
            "email.send",
            to="sam@acme.invalid",
            subject="MOCK",
            body="MOCK",
            idempotency_key="nope",
        )
        return AgentOutput.success()


class ApprovalInput(AgentInput):
    body: str = "MOCK draft body"


class ApprovalAgent(Agent):
    """Asks for approval on the first run, then acts on the CEO's decision."""

    MANIFEST = AgentManifest(
        name="needs_approval",
        version="2.1.0",
        description="Requests human approval before acting.",
        input_model=ApprovalInput,
        allowed_actions=[],
        approval_required_actions=[ActionType.OUTREACH_SEND_EMAIL],
        timeout_seconds=10,
        max_attempts=3,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, ApprovalInput)
        if ctx.approval is None:
            return AgentOutput.needs_approval(
                ApprovalRequest(
                    action_type=ActionType.OUTREACH_SEND_EMAIL,
                    summary="Send intro email to MOCK Sam Rivera",
                    payload={"body": agent_input.body},
                    risk=RiskLevel.MEDIUM,
                    policy_reason="autonomy_off",
                )
            )
        # Second run: what executes is the CEO's version, not the agent's draft.
        approved = ctx.approved_payload or {}
        return AgentOutput.success(result={"sent_body": approved["body"]})


class FlakyAgent(Agent):
    """Fails in a way that deserves a retry."""

    MANIFEST = AgentManifest(
        name="flaky",
        version="1.0.0",
        description="Always fails, retryably.",
        input_model=NoInput,
        timeout_seconds=10,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        return AgentOutput.failure(
            AgentError(type="ProviderTimeout", message="upstream timed out", retryable=True)
        )


class CrashingAgent(Agent):
    MANIFEST = AgentManifest(
        name="crashing",
        version="1.0.0",
        description="Raises.",
        input_model=NoInput,
        timeout_seconds=10,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        raise RuntimeError("unhandled explosion")


class SlowAgent(Agent):
    MANIFEST = AgentManifest(
        name="slow",
        version="1.0.0",
        description="Exceeds its timeout.",
        input_model=NoInput,
        timeout_seconds=1,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        time.sleep(5)
        return AgentOutput.success()


class WritingAgent(Agent):
    """Writes to the database, then fails, to prove the rollback boundary."""

    MANIFEST = AgentManifest(
        name="writer",
        version="1.0.0",
        description="Writes then explodes.",
        input_model=NoInput,
        timeout_seconds=10,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        from db.models.market import Company

        ctx.session.add(Company(name="MOCK Should Not Persist", domain="ghost.invalid"))
        ctx.session.flush()
        raise RuntimeError("failing after a write")


class ExpensiveAgent(Agent):
    MANIFEST = AgentManifest(
        name="expensive",
        version="1.0.0",
        description="Reports token usage and cost.",
        input_model=NoInput,
        timeout_seconds=10,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        output = AgentOutput.success(result={})
        output.usage.add_llm(tokens_in=1200, tokens_out=340, cost_usd=Decimal("0.0185"))
        return output
