"""The worker: leases tasks, runs agents, records what happened.

Effects belong to the worker, not to agents. An agent returns intent and the worker
persists it, which is what keeps a crashed agent from leaving half-finished side effects.

Each task moves through three separate transactions:

1. lease          - durable claim, so a crash cannot silently drop the task
2. start          - the attempt is recorded before the agent runs, so a hang is visible
3. execute/finish - the agent's work and its outcome commit or roll back together

Splitting them matters: if everything shared one transaction, a rollback would erase the
evidence that the attempt ever happened.
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from types import FrameType

from sqlalchemy.orm import Session

from config.settings import get_settings
from core import approval_gate, audit, event_bus, task_queue
from core.agent_base import Agent, AgentContext, AgentOutput
from core.errors import AgentError, PermanentError, RetryableError
from core.observability import configure_logging, get_logger
from core.registry import AGENTS
from core.tool_registry import TOOLS, ToolBelt
from core.tools import register_builtin_tools
from db.enums import RunStatus, TaskStatus
from db.models.runtime import AgentRun, AgentTask
from db.session import session_scope

logger = get_logger(__name__)


class AgentTimeout(RetryableError):
    """The agent exceeded the timeout declared in its manifest."""


@contextmanager
def time_limit(seconds: int) -> Iterator[None]:
    """Hard timeout via SIGALRM.

    Only works on the main thread, which is where the worker runs. Off the main thread it
    degrades to no enforcement and says so, rather than pretending the limit is in force.
    """
    if threading.current_thread() is not threading.main_thread():
        logger.warning("timeout not enforced off the main thread", extra={"timeout": seconds})
        yield
        return

    def _raise(signum: int, frame: FrameType | None) -> None:
        raise AgentTimeout(f"agent exceeded its {seconds}s timeout")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _start_attempt(session: Session, task: AgentTask, agent: Agent) -> AgentRun:
    """Record the attempt before running anything, so a hang leaves a trace."""
    manifest = agent.MANIFEST
    # The manifest owns retry policy; a task created with a generic default defers to it.
    task.max_attempts = manifest.max_attempts
    task_queue.mark_running(session, task)

    run = AgentRun(
        task_id=task.id,
        agent=manifest.name,
        agent_version=manifest.version,
        status=RunStatus.RUNNING,
        attempt=task.attempts,
        dry_run=task.dry_run,
    )
    session.add(run)
    session.flush()
    return run


def _apply_output(
    session: Session,
    *,
    task: AgentTask,
    run: AgentRun,
    agent: Agent,
    output: AgentOutput,
    tools: ToolBelt,
) -> None:
    """Persist everything the agent asked for. The agent itself performed none of this."""
    run.tokens_in = output.usage.tokens_in
    run.tokens_out = output.usage.tokens_out
    run.cost_usd = output.usage.cost_usd
    run.tool_call_count = len(tools.calls)
    run.tool_calls = [call.as_dict() for call in tools.calls]
    run.output = output.result

    for event in output.events:
        event_bus.emit(
            session,
            event_type=event.event_type,
            payload=event.payload,
            subject_type=event.subject_type,
            subject_id=event.subject_id,
            emitted_by=agent.MANIFEST.name,
            task_id=task.id,
            correlation_id=task.correlation_id,
        )

    for follow_up in output.follow_up_tasks:
        run_after = None
        if follow_up.delay_seconds:
            run_after = task_queue.db_now(session) + timedelta(seconds=follow_up.delay_seconds)
        task_queue.enqueue(
            session,
            agent=follow_up.agent,
            task_input=follow_up.task_input,
            action_type=follow_up.action_type,
            priority=follow_up.priority,
            max_attempts=follow_up.max_attempts,
            run_after=run_after,
            dedupe_key=follow_up.dedupe_key,
            parent_task_id=task.id,
            correlation_id=task.correlation_id,
            # A task spawned in dry run stays in dry run. Otherwise a dry-run experiment
            # could quietly produce a child task that sends for real.
            dry_run=task.dry_run,
            created_by_actor=agent.MANIFEST.name,
        )

    if output.status == "needs_approval":
        request = output.approval_request
        if request is None:
            raise PermanentError("agent returned needs_approval without an approval request")
        approval = approval_gate.request(
            session,
            action_type=request.action_type,
            agent=agent.MANIFEST.name,
            summary=request.summary,
            payload=request.payload,
            risk=request.risk,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            task=task,
            policy_reason=request.policy_reason,
            expires_in_hours=request.expires_in_hours,
        )
        run.status = RunStatus.NEEDS_APPROVAL
        run.finished_at = task_queue.db_now(session)
        logger.info(
            "task parked for approval",
            extra={
                "agent": agent.MANIFEST.name,
                "task_id": str(task.id),
                "approval_id": str(approval.id),
            },
        )
        return

    if output.status == "failed":
        error = output.error or AgentError(type="UnknownError", message="agent reported failure")
        run.status = RunStatus.FAILED
        run.error = error.model_dump()
        run.finished_at = task_queue.db_now(session)
        task_queue.fail(session, task, error=error.message, retryable=error.retryable)
        return

    run.status = RunStatus.SUCCEEDED
    run.finished_at = task_queue.db_now(session)
    task_queue.complete(session, task, output.result)


def _finalize_run_failure(run_id: uuid.UUID, task_id: uuid.UUID, exc: BaseException) -> None:
    """Record a crash in a fresh transaction, after the agent's writes were rolled back."""
    error = AgentError.from_exception(exc)
    with session_scope() as session:
        run = session.get(AgentRun, run_id)
        task = session.get(AgentTask, task_id)
        if run is not None:
            run.status = (
                RunStatus.TIMED_OUT if isinstance(exc, AgentTimeout) else RunStatus.FAILED
            )
            run.error = error.model_dump()
            run.finished_at = task_queue.db_now(session)
        if task is not None:
            status = task_queue.fail(
                session, task, error=f"{error.type}: {error.message}", retryable=error.retryable
            )
            if status is TaskStatus.DEAD_LETTER:
                audit.record_system(
                    session,
                    action="task.escalated",
                    subject_type="agent_task",
                    subject_id=task.id,
                    reason="retries exhausted; needs CEO attention",
                    task_id=task.id,
                    correlation_id=task.correlation_id,
                )


def execute_task(task_id: uuid.UUID, *, worker_id: str) -> RunStatus:
    """Run one task end to end. Returns the resulting run status."""
    started = time.perf_counter()

    with session_scope() as session:
        starting_task = session.get(AgentTask, task_id)
        if starting_task is None:
            raise PermanentError(f"task {task_id} disappeared")
        starting_agent = AGENTS.get(starting_task.agent)
        run_id = _start_attempt(session, starting_task, starting_agent).id
        timeout = starting_agent.MANIFEST.timeout_seconds
        agent_name = starting_agent.MANIFEST.name

    try:
        with session_scope() as session:
            task = session.get(AgentTask, task_id)
            run = session.get(AgentRun, run_id)
            if task is None or run is None:
                raise PermanentError(f"task {task_id} or run {run_id} disappeared mid-flight")

            agent = AGENTS.get(task.agent)
            tools = ToolBelt(
                agent=agent_name,
                allowed=frozenset(agent.MANIFEST.allowed_tools),
                session=session,
                registry=TOOLS,
                task_id=task.id,
                correlation_id=task.correlation_id,
            )
            ctx = AgentContext(
                session=session,
                task=task,
                run_id=run.id,
                tools=tools,
                dry_run=task.dry_run,
                correlation_id=task.correlation_id,
                approval=approval_gate.approved_for_task(session, task.id),
            )

            try:
                agent_input = agent.parse_input(task.input)
            except Exception as exc:
                # Bad input will fail identically on every retry.
                raise PermanentError(f"invalid input for agent {agent_name}: {exc}") from exc

            with time_limit(timeout):
                output = agent.run(agent_input, ctx)

            # An agent may report usage on the context instead of the output.
            if output.usage.tokens_in == 0 and ctx.usage.tokens_in:
                output.usage = ctx.usage

            _apply_output(session, task=task, run=run, agent=agent, output=output, tools=tools)
            run.duration_ms = int((time.perf_counter() - started) * 1000)
            status = run.status

        logger.info(
            "task finished",
            extra={
                "agent": agent_name,
                "task_id": str(task_id),
                "run_id": str(run_id),
                "status": str(status),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return status

    except Exception as exc:
        logger.warning(
            "task failed",
            extra={
                "agent": agent_name,
                "task_id": str(task_id),
                "run_id": str(run_id),
                "error": str(exc),
            },
        )
        _finalize_run_failure(run_id, task_id, exc)
        return RunStatus.TIMED_OUT if isinstance(exc, AgentTimeout) else RunStatus.FAILED


def run_once(*, worker_id: str, batch_size: int | None = None) -> int:
    """One pass of the worker loop. Returns how many tasks were executed."""
    settings = get_settings()
    limit = batch_size or settings.worker_batch_size

    with session_scope() as session:
        reclaimed = task_queue.reclaim_expired_leases(session)
        expired = approval_gate.expire_due(session)
    if reclaimed:
        logger.info("reclaimed expired leases", extra={"count": len(reclaimed)})
    if expired:
        logger.info("expired unanswered approvals", extra={"count": len(expired)})

    with session_scope() as session:
        event_bus.dispatch_pending(session)

    with session_scope() as session:
        leased = task_queue.lease(
            session, worker_id=worker_id, limit=limit, agents=AGENTS.names() or None
        )
        task_ids = [task.id for task in leased]

    for task_id in task_ids:
        execute_task(task_id, worker_id=worker_id)

    # Agents emit events; dispatch them promptly rather than waiting a full poll interval.
    with session_scope() as session:
        event_bus.dispatch_pending(session)

    return len(task_ids)


def run_forever(*, worker_id: str | None = None) -> None:
    settings = get_settings()
    wid = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
    register_builtin_tools()
    AGENTS.load_from_config()

    logger.info(
        "worker started",
        extra={
            "worker_id": wid,
            "dry_run": settings.dry_run,
            "agents": AGENTS.names(),
            "environment": settings.environment,
        },
    )
    while True:
        executed = run_once(worker_id=wid)
        if executed == 0:
            time.sleep(settings.worker_poll_interval_seconds)


def main() -> None:
    configure_logging()
    run_forever()


if __name__ == "__main__":
    main()
