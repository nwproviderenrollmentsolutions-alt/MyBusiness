from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from core import task_queue
from db.enums import TaskStatus
from db.models.runtime import AgentTask, AuditLog
from db.session import get_session_factory

pytestmark = pytest.mark.integration


def test_enqueue_records_an_audit_entry(db):
    task = task_queue.enqueue(db, agent="demo", task_input={"a": 1})
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "task.created"))
    assert entry is not None
    assert entry.subject_id == task.id


def test_dedupe_key_returns_the_existing_task(db):
    first = task_queue.enqueue(db, agent="demo", dedupe_key="same")
    second = task_queue.enqueue(db, agent="demo", dedupe_key="same")
    db.commit()

    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(AgentTask)) == 1


def test_lease_respects_priority_then_age(db):
    task_queue.enqueue(db, agent="demo", task_input={"n": "low"}, priority=0)
    task_queue.enqueue(db, agent="demo", task_input={"n": "high"}, priority=10)
    db.commit()

    leased = task_queue.lease(db, worker_id="w1", limit=1)
    assert leased[0].input["n"] == "high"
    assert leased[0].status is TaskStatus.LEASED
    assert leased[0].leased_by == "w1"


def test_lease_skips_tasks_that_are_not_due_yet(db):
    future = task_queue.db_now(db) + timedelta(hours=1)
    task_queue.enqueue(db, agent="demo", run_after=future)
    db.commit()

    assert task_queue.lease(db, worker_id="w1", limit=5) == []


def test_two_workers_never_get_the_same_task(db):
    """The whole point of SKIP LOCKED: concurrent workers, no double execution."""
    task_queue.enqueue(db, agent="demo")
    db.commit()

    factory = get_session_factory()
    session_a, session_b = factory(), factory()
    try:
        leased_a = task_queue.lease(session_a, worker_id="a", limit=5)
        leased_b = task_queue.lease(session_b, worker_id="b", limit=5)

        assert len(leased_a) == 1
        assert leased_b == []
        session_a.commit()
        session_b.commit()
    finally:
        session_a.close()
        session_b.close()


def test_retry_schedules_a_later_attempt(db):
    task = task_queue.enqueue(db, agent="demo")
    task_queue.mark_running(db, task)
    before = task_queue.db_now(db)

    status = task_queue.fail(db, task, error="provider timed out", retryable=True)
    db.commit()

    assert status is TaskStatus.PENDING
    assert task.run_after > before
    assert task.leased_by is None


def test_retries_are_exhausted_into_dead_letter(db):
    task = task_queue.enqueue(db, agent="demo", max_attempts=2)
    for _ in range(2):
        task_queue.mark_running(db, task)
        status = task_queue.fail(db, task, error="still failing", retryable=True)
    db.commit()

    assert status is TaskStatus.DEAD_LETTER
    assert task.finished_at is not None


def test_permanent_errors_do_not_consume_the_retry_budget(db):
    task = task_queue.enqueue(db, agent="demo", max_attempts=5)
    task_queue.mark_running(db, task)

    status = task_queue.fail(db, task, error="invalid input", retryable=False)
    db.commit()

    assert status is TaskStatus.FAILED
    assert task.attempts == 1


def test_expired_lease_returns_the_task_to_the_queue(db):
    """A worker that dies mid-task must not strand the work."""
    task = task_queue.enqueue(db, agent="demo")
    task_queue.lease(db, worker_id="doomed", limit=1, lease_seconds=-1)
    db.commit()

    reclaimed = task_queue.reclaim_expired_leases(db)
    db.commit()

    assert [t.id for t in reclaimed] == [task.id]
    assert task.status is TaskStatus.PENDING
    assert task.leased_by is None


def test_reclaim_dead_letters_a_task_that_already_exhausted_its_attempts(db):
    task = task_queue.enqueue(db, agent="demo", max_attempts=1)
    task_queue.mark_running(db, task)
    task_queue.lease(db, worker_id="doomed", limit=0)
    task.lease_expires_at = task_queue.db_now(db) - timedelta(seconds=1)
    task.status = TaskStatus.RUNNING
    db.flush()

    task_queue.reclaim_expired_leases(db)
    db.commit()

    assert task.status is TaskStatus.DEAD_LETTER


def test_resume_only_applies_to_parked_tasks(db):
    task = task_queue.enqueue(db, agent="demo")
    with pytest.raises(Exception, match="cannot resume"):
        task_queue.resume(db, task, reason="nope")
