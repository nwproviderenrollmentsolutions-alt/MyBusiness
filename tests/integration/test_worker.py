from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from core import approval_gate, task_queue
from core.registry import AGENTS
from db.enums import ApprovalStatus, RunStatus, TaskStatus
from db.models.market import Company
from db.models.runtime import AgentRun, AgentTask, Approval, AuditLog, OutboxEvent
from db.session import session_scope
from tests.conftest import make_policy
from tests.fixtures.agents import (
    ApprovalAgent,
    CrashingAgent,
    EchoAgent,
    ExpensiveAgent,
    FlakyAgent,
    ForbiddenToolAgent,
    SlowAgent,
    WritingAgent,
)
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered_agents(db):
    for agent in (
        EchoAgent(),
        ApprovalAgent(),
        FlakyAgent(),
        CrashingAgent(),
        SlowAgent(),
        WritingAgent(),
        ExpensiveAgent(),
        ForbiddenToolAgent(),
    ):
        AGENTS.register(agent)


def _enqueue(agent: str, **task_input) -> AgentTask:
    with session_scope() as session:
        task = task_queue.enqueue(session, agent=agent, task_input=task_input, dry_run=True)
        return task


def _reload(task_id) -> AgentTask:
    with session_scope() as session:
        task = session.get(AgentTask, task_id)
        assert task is not None
        return task


def _runs(task_id) -> list[AgentRun]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.attempt)
            )
        )


def test_successful_task_records_a_run(db):
    task = _enqueue("echo", message="hello")

    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    assert _reload(task.id).status is TaskStatus.SUCCEEDED
    run = _runs(task.id)[0]
    assert run.agent_version == "1.0.0"
    assert run.duration_ms is not None
    assert run.output["echoed"] == "hello"


def test_run_records_tool_calls_and_cost(db):
    task = _enqueue("echo", message="hello")
    runner.execute_task(task.id, worker_id="w1")

    run = _runs(task.id)[0]
    assert run.tool_call_count == 1
    assert run.tool_calls[0]["tool"] == "llm.complete"
    # The mock LLM spends nothing, so a nonzero cost here would be fabricated.
    assert run.cost_usd == Decimal("0")
    assert run.tokens_in > 0


def test_cost_is_persisted_for_reporting(db):
    task = _enqueue("expensive")
    runner.execute_task(task.id, worker_id="w1")

    run = _runs(task.id)[0]
    assert run.tokens_in == 1200
    assert run.cost_usd == Decimal("0.018500")


def test_agent_events_are_emitted_by_the_worker(db):
    task = _enqueue("echo", message="hello", emit_event=True)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "echo.completed")
        )
    assert event.emitted_by == "echo"
    assert event.correlation_id == task.correlation_id


def test_follow_up_tasks_inherit_dry_run(db):
    """A dry-run experiment must not spawn a child task that sends for real."""
    task = _enqueue("echo", message="hello", spawn_follow_up=True)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        child = session.scalar(select(AgentTask).where(AgentTask.parent_task_id == task.id))
    assert child.dry_run is True
    assert child.correlation_id == task.correlation_id


def test_agent_cannot_use_a_tool_outside_its_manifest(db):
    task = _enqueue("forbidden_tool")

    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.FAILED
    # ToolAccessDenied is permanent: retrying would fail identically.
    assert _reload(task.id).status is TaskStatus.FAILED
    assert _runs(task.id)[0].error["type"] == "ToolAccessDenied"


def test_approval_pauses_then_resumes_the_same_task(db, policy):
    policy(make_policy())
    task = _enqueue("needs_approval", body="MOCK draft body")

    assert runner.execute_task(task.id, worker_id="w1") is RunStatus.NEEDS_APPROVAL
    assert _reload(task.id).status is TaskStatus.AWAITING_APPROVAL

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(
            session, approval.id, decided_by="ceo", edited_payload={"body": "CEO rewrote this"}
        )

    assert _reload(task.id).status is TaskStatus.PENDING
    assert runner.execute_task(task.id, worker_id="w1") is RunStatus.SUCCEEDED

    final = _reload(task.id)
    assert final.status is TaskStatus.SUCCEEDED
    # The CEO's edit is what executed, not the agent's draft.
    assert final.result["sent_body"] == "CEO rewrote this"
    assert len(_runs(task.id)) == 2


def test_rejected_approval_stops_the_work(db, policy):
    policy(make_policy())
    task = _enqueue("needs_approval")
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.reject(session, approval.id, decided_by="ceo", notes="not now")

    assert _reload(task.id).status is TaskStatus.CANCELLED
    with session_scope() as session:
        assert session.scalar(select(Approval)).status is ApprovalStatus.REJECTED


def test_retryable_failure_is_retried_then_dead_lettered(db):
    task = _enqueue("flaky")

    runner.execute_task(task.id, worker_id="w1")
    assert _reload(task.id).status is TaskStatus.PENDING

    runner.execute_task(task.id, worker_id="w1")
    final = _reload(task.id)

    assert final.status is TaskStatus.DEAD_LETTER
    assert final.attempts == 2
    assert len(_runs(task.id)) == 2


@pytest.mark.parametrize(
    ("agent", "runs", "expected_status"),
    [("crashing", 1, TaskStatus.FAILED), ("flaky", 2, TaskStatus.DEAD_LETTER)],
)
def test_terminal_failures_are_escalated_for_the_ceo(db, agent, runs, expected_status):
    """Both permanent failure and exhausted retries mean work is silently not happening."""
    task = _enqueue(agent)
    for _ in range(runs):
        runner.execute_task(task.id, worker_id="w1")

    assert _reload(task.id).status is expected_status
    with session_scope() as session:
        escalation = session.scalar(
            select(AuditLog).where(AuditLog.action == "task.escalated", AuditLog.task_id == task.id)
        )
    assert escalation is not None
    assert "CEO attention" in escalation.reason


def test_crash_is_recorded_on_the_run(db):
    task = _enqueue("crashing")
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.FAILED
    run = _runs(task.id)[0]
    assert run.status is RunStatus.FAILED
    assert run.error["message"] == "unhandled explosion"
    assert run.finished_at is not None


def test_agent_writes_roll_back_when_it_fails(db):
    """The failure record survives; the half-finished work does not."""
    task = _enqueue("writer")
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        ghosts = session.scalar(
            select(func.count()).select_from(Company).where(Company.domain == "ghost.invalid")
        )
        assert ghosts == 0
    assert _runs(task.id)[0].status is RunStatus.FAILED


def test_timeout_is_enforced_and_labeled(db):
    task = _enqueue("slow")
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.TIMED_OUT
    assert _runs(task.id)[0].status is RunStatus.TIMED_OUT


def test_invalid_input_fails_permanently(db):
    """Bad input fails the same way every time; retrying only delays the escalation."""
    task = _enqueue("echo", wrong_field="nope")

    runner.execute_task(task.id, worker_id="w1")

    final = _reload(task.id)
    assert final.status is TaskStatus.FAILED
    assert final.attempts == 1


def test_run_once_drains_the_queue_and_dispatches_events(db, agents_config):
    agents_config({"echo": ["echo.completed"]})
    _enqueue("echo", message="first", emit_event=True)

    executed = runner.run_once(worker_id="w1", batch_size=10)

    assert executed == 1
    with session_scope() as session:
        # The emitted event produced a task for its subscriber.
        spawned = session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.created_by_actor == "event_bus")
        )
    assert spawned == 1


def test_run_once_recovers_a_task_from_a_dead_worker(db):
    task = _enqueue("echo", message="orphan")
    with session_scope() as session:
        task_queue.lease(session, worker_id="doomed", limit=1, lease_seconds=-1)

    runner.run_once(worker_id="w2", batch_size=10)

    # Reclaimed with backoff rather than retried instantly, so a task that kills workers
    # cannot hot-loop through them.
    recovered = _reload(task.id)
    assert recovered.status is TaskStatus.PENDING
    assert recovered.leased_by is None

    with session_scope() as session:
        session.get(AgentTask, task.id).run_after = task_queue.db_now(session)
    runner.run_once(worker_id="w2", batch_size=10)

    assert _reload(task.id).status is TaskStatus.SUCCEEDED
