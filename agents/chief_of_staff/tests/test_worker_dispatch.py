"""End-to-end: a CEO command goes through the real task queue and worker, not a shortcut.

Also proves the architecture's central claim for Chief of Staff: once a domain agent is
registered, dispatching to it requires no change to this agent's code — only registering
the new agent. Milestone 3 gets to cash that check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agents.chief_of_staff.agent import AGENT as CHIEF_OF_STAFF
from core import task_queue
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
)
from core.registry import AGENTS
from db.enums import CommandStatus, RunStatus, TaskStatus
from db.models.command import CeoCommand
from db.models.runtime import AgentTask
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration


class _LeadDiscoveryInput(AgentInput):
    segment: str
    count: int


class _LeadDiscoveryStandIn(Agent):
    """A minimal stand-in for the real lead_discovery agent Milestone 3 will add.

    Exists only to prove Chief of Staff's dispatch path works once a target agent is
    registered, without waiting for that agent to actually be built.
    """

    MANIFEST = AgentManifest(
        name="lead_discovery",
        version="0.0.1-test-stand-in",
        description="Stand-in for dispatch testing.",
        input_model=_LeadDiscoveryInput,
        timeout_seconds=10,
        max_attempts=1,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        return AgentOutput.success(result={})


@pytest.fixture(autouse=True)
def registered_agents(db):
    AGENTS.register(CHIEF_OF_STAFF)


def _submit(text: str, created_by: str = "ceo@example.com") -> tuple[CeoCommand, AgentTask]:
    with session_scope() as session:
        command = CeoCommand(raw_text=text, created_by=created_by)
        session.add(command)
        session.flush()
        task = task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
        )
        return command, task


def test_command_flows_through_the_real_queue_and_worker(db):
    command, task = _submit("what's our status?")

    status_result = runner.execute_task(task.id, worker_id="w1")

    assert status_result is RunStatus.SUCCEEDED
    with session_scope() as session:
        resolved = session.get(CeoCommand, command.id)
        assert resolved.status is CommandStatus.ANSWERED
        finished_task = session.get(AgentTask, task.id)
        assert finished_task.status is TaskStatus.SUCCEEDED
        assert finished_task.result["status"] == "answered"


def test_dispatch_requires_no_change_once_the_target_agent_exists(db):
    """The forward-compatibility claim: register a 'lead_discovery' agent and the same
    Chief of Staff code routes work to it — no code change needed."""
    AGENTS.register(_LeadDiscoveryStandIn())

    command, task = _submit("find 50 qualified plumbing leads")
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        resolved = session.get(CeoCommand, command.id)
        assert resolved.status is CommandStatus.DISPATCHED
        assert resolved.required_agents == []

        spawned = session.scalar(
            select(AgentTask).where(AgentTask.agent == "lead_discovery")
        )
        assert spawned is not None
        assert spawned.parent_task_id == task.id
        assert spawned.input == {"segment": "plumbing", "count": 50}
