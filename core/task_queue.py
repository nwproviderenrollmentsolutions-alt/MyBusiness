"""Postgres-backed task queue.

Leasing uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple workers can drain the queue
without ever handing the same task to two of them. Leases expire, so a worker that dies
mid-task does not strand the work.

All timestamps come from the database clock, not the worker's, so lease expiry stays
correct even if a worker's clock drifts.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.settings import get_settings
from core import audit
from core.errors import PermanentError
from db.enums import ActorType, TaskStatus
from db.models.runtime import AgentTask

_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 300.0

_AUDIT_FIELDS = ("status", "attempts", "run_after", "leased_by", "last_error")


def db_now(session: Session) -> datetime:
    """The database's clock. The single source of time for scheduling decisions."""
    return session.execute(select(func.now())).scalar_one()


def backoff_seconds(attempts: int) -> float:
    """Exponential backoff with jitter.

    Jitter matters even for a one-person company: without it, a provider outage causes
    every failed task to retry in the same instant, repeatedly.
    """
    base = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * float(2 ** max(0, attempts - 1)))
    jitter: float = random.uniform(0.5, 1.5)
    return base * jitter


def enqueue(
    session: Session,
    *,
    agent: str,
    task_input: dict[str, Any] | None = None,
    action_type: str | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    parent_task_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    dry_run: bool | None = None,
    created_by_actor_type: ActorType = ActorType.SYSTEM,
    created_by_actor: str = "system",
) -> AgentTask:
    """Create a task, or return the existing one when ``dedupe_key`` already exists.

    Callers can therefore enqueue the same logical work repeatedly without creating
    duplicates — which is what makes event dispatch and CEO command retries safe.
    """
    if dedupe_key is not None:
        existing = session.scalar(select(AgentTask).where(AgentTask.dedupe_key == dedupe_key))
        if existing is not None:
            return existing

    task = AgentTask(
        agent=agent,
        action_type=action_type,
        input=task_input or {},
        status=TaskStatus.PENDING,
        priority=priority,
        max_attempts=max_attempts,
        run_after=run_after or db_now(session),
        dedupe_key=dedupe_key,
        parent_task_id=parent_task_id,
        dry_run=get_settings().dry_run if dry_run is None else dry_run,
        created_by_actor_type=created_by_actor_type,
        created_by_actor=created_by_actor,
    )
    if correlation_id is not None:
        task.correlation_id = correlation_id

    try:
        with session.begin_nested():
            session.add(task)
            session.flush()
    except IntegrityError:
        # Another writer won the race on dedupe_key. Their task is the one that counts.
        if dedupe_key is None:
            raise
        existing = session.scalar(select(AgentTask).where(AgentTask.dedupe_key == dedupe_key))
        if existing is None:
            raise
        return existing

    audit.record(
        session,
        actor_type=created_by_actor_type,
        actor=created_by_actor,
        action="task.created",
        subject_type="agent_task",
        subject_id=task.id,
        after={"agent": agent, "action_type": action_type, "dry_run": task.dry_run},
        task_id=task.id,
        correlation_id=task.correlation_id,
    )
    return task


def lease(
    session: Session,
    *,
    worker_id: str,
    limit: int = 1,
    agents: list[str] | None = None,
    lease_seconds: int | None = None,
) -> list[AgentTask]:
    """Claim up to ``limit`` due tasks. Skips rows locked by other workers."""
    settings = get_settings()
    now = db_now(session)
    expires = now + timedelta(seconds=lease_seconds or settings.task_lease_seconds)

    conditions = [AgentTask.status == TaskStatus.PENDING, AgentTask.run_after <= now]
    if agents:
        conditions.append(AgentTask.agent.in_(agents))

    stmt = (
        select(AgentTask)
        .where(and_(*conditions))
        .order_by(AgentTask.priority.desc(), AgentTask.run_after.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    tasks = list(session.scalars(stmt))
    for task in tasks:
        task.status = TaskStatus.LEASED
        task.leased_by = worker_id
        task.lease_expires_at = expires
    session.flush()
    return tasks


def mark_running(session: Session, task: AgentTask) -> None:
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.status = TaskStatus.RUNNING
    task.attempts += 1
    task.started_at = db_now(session)
    session.flush()
    audit.record_system(
        session,
        action="task.started",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        task_id=task.id,
        correlation_id=task.correlation_id,
    )


def complete(session: Session, task: AgentTask, result: dict[str, Any] | None = None) -> None:
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.status = TaskStatus.SUCCEEDED
    task.result = result
    task.finished_at = db_now(session)
    task.lease_expires_at = None
    task.leased_by = None
    session.flush()
    audit.record_system(
        session,
        action="task.succeeded",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        task_id=task.id,
        correlation_id=task.correlation_id,
    )


def fail(
    session: Session,
    task: AgentTask,
    *,
    error: str,
    retryable: bool = True,
) -> TaskStatus:
    """Record a failed attempt.

    Retryable with budget left  -> back to pending with backoff.
    Retryable with budget spent -> dead_letter, so the CEO sees it rather than it
                                   retrying forever.
    Not retryable               -> failed, terminal. Retrying a permanent error just
                                   burns money and delays the escalation.
    """
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.last_error = error
    task.lease_expires_at = None
    task.leased_by = None

    if not retryable:
        task.status = TaskStatus.FAILED
        task.finished_at = db_now(session)
    elif task.attempts >= task.max_attempts:
        task.status = TaskStatus.DEAD_LETTER
        task.finished_at = db_now(session)
    else:
        task.status = TaskStatus.PENDING
        task.run_after = db_now(session) + timedelta(seconds=backoff_seconds(task.attempts))

    session.flush()
    audit.record_system(
        session,
        action=f"task.{task.status}",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        reason=error,
        task_id=task.id,
        correlation_id=task.correlation_id,
    )
    return task.status


def pause_for_approval(session: Session, task: AgentTask, *, approval_id: uuid.UUID) -> None:
    """Park a task while a human decides. It stops consuming worker capacity."""
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.status = TaskStatus.AWAITING_APPROVAL
    task.lease_expires_at = None
    task.leased_by = None
    session.flush()
    audit.record_system(
        session,
        action="task.awaiting_approval",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        reason=f"approval {approval_id}",
        task_id=task.id,
        correlation_id=task.correlation_id,
    )


def resume(session: Session, task: AgentTask, *, reason: str) -> None:
    if task.status is not TaskStatus.AWAITING_APPROVAL:
        raise PermanentError(f"cannot resume task in status {task.status}")
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.status = TaskStatus.PENDING
    task.run_after = db_now(session)
    session.flush()
    audit.record_system(
        session,
        action="task.resumed",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        reason=reason,
        task_id=task.id,
        correlation_id=task.correlation_id,
    )


def cancel(session: Session, task: AgentTask, *, reason: str) -> None:
    before = audit.snapshot(task, _AUDIT_FIELDS)
    task.status = TaskStatus.CANCELLED
    task.finished_at = db_now(session)
    task.lease_expires_at = None
    task.leased_by = None
    session.flush()
    audit.record_system(
        session,
        action="task.cancelled",
        subject_type="agent_task",
        subject_id=task.id,
        before=before,
        after=audit.snapshot(task, _AUDIT_FIELDS),
        reason=reason,
        task_id=task.id,
        correlation_id=task.correlation_id,
    )


def reclaim_expired_leases(session: Session, *, limit: int = 50) -> list[AgentTask]:
    """Return tasks whose worker died to the queue.

    Without this, a crashed worker's in-flight task would sit in ``leased`` forever and
    the work would silently never happen.
    """
    now = db_now(session)
    stmt = (
        select(AgentTask)
        .where(
            or_(AgentTask.status == TaskStatus.LEASED, AgentTask.status == TaskStatus.RUNNING),
            AgentTask.lease_expires_at.is_not(None),
            AgentTask.lease_expires_at < now,
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    reclaimed = list(session.scalars(stmt))
    for task in reclaimed:
        before = audit.snapshot(task, _AUDIT_FIELDS)
        previous_worker = task.leased_by
        task.lease_expires_at = None
        task.leased_by = None
        if task.attempts >= task.max_attempts:
            task.status = TaskStatus.DEAD_LETTER
            task.finished_at = now
        else:
            task.status = TaskStatus.PENDING
            task.run_after = now + timedelta(seconds=backoff_seconds(task.attempts))
        session.flush()
        audit.record_system(
            session,
            action="task.lease_reclaimed",
            subject_type="agent_task",
            subject_id=task.id,
            before=before,
            after=audit.snapshot(task, _AUDIT_FIELDS),
            reason=f"lease expired (worker {previous_worker})",
            task_id=task.id,
            correlation_id=task.correlation_id,
        )
    return reclaimed
