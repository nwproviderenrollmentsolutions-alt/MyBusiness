from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core import event_bus, task_queue
from db.models.runtime import AgentTask, AuditLog, OutboxEvent

pytestmark = pytest.mark.integration


def test_event_creates_a_task_for_each_subscriber(db, agents_config):
    agents_config({"scorer": ["lead.enriched"], "notifier": ["lead.enriched"]})
    event_bus.emit(db, event_type="lead.enriched", payload={"lead_id": "abc"})
    db.commit()

    processed = event_bus.dispatch_pending(db)
    db.commit()

    assert processed == 1
    agents = sorted(t.agent for t in db.scalars(select(AgentTask)))
    assert agents == ["notifier", "scorer"]


def test_emitter_does_not_need_to_know_its_consumers(db, agents_config):
    """An event with nobody listening is normal, not an error."""
    agents_config({})
    event_bus.emit(db, event_type="deal.won", payload={})
    db.commit()

    assert event_bus.dispatch_pending(db) == 1
    db.commit()
    assert db.scalar(select(func.count()).select_from(AgentTask)) == 0


def test_dispatch_is_exactly_once(db, agents_config):
    agents_config({"scorer": ["lead.enriched"]})
    event_bus.emit(db, event_type="lead.enriched", payload={})
    db.commit()

    event_bus.dispatch_pending(db)
    db.commit()
    second_pass = event_bus.dispatch_pending(db)
    db.commit()

    assert second_pass == 0
    assert db.scalar(select(func.count()).select_from(AgentTask)) == 1


def test_redelivery_of_the_same_event_cannot_duplicate_work(db, agents_config):
    """Belt and braces: even if an event were re-processed, the dedupe key blocks a
    second task."""
    agents_config({"scorer": ["lead.enriched"]})
    event = event_bus.emit(db, event_type="lead.enriched", payload={})
    db.commit()

    event_bus.dispatch_pending(db)
    db.commit()
    event.processed_at = None  # simulate a redelivery
    db.flush()
    event_bus.dispatch_pending(db)
    db.commit()

    assert db.scalar(select(func.count()).select_from(AgentTask)) == 1


def test_event_carries_correlation_into_the_task(db, agents_config):
    agents_config({"scorer": ["lead.enriched"]})
    origin = task_queue.enqueue(db, agent="discovery")
    event_bus.emit(
        db,
        event_type="lead.enriched",
        payload={},
        task_id=origin.id,
        correlation_id=origin.correlation_id,
    )
    db.commit()

    event_bus.dispatch_pending(db)
    db.commit()

    spawned = db.scalar(select(AgentTask).where(AgentTask.agent == "scorer"))
    assert spawned.correlation_id == origin.correlation_id
    assert spawned.parent_task_id == origin.id


def test_dispatch_is_audited(db, agents_config):
    agents_config({"scorer": ["lead.enriched"]})
    event_bus.emit(db, event_type="lead.enriched", payload={})
    db.commit()
    event_bus.dispatch_pending(db)
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "event.dispatched"))
    assert entry.after["subscribers"] == ["scorer"]


def test_unprocessed_count_reflects_the_backlog(db, agents_config):
    agents_config({})
    event_bus.emit(db, event_type="a.b", payload={})
    event_bus.emit(db, event_type="c.d", payload={})
    db.commit()

    assert event_bus.unprocessed_count(db) == 2
    event_bus.dispatch_pending(db)
    db.commit()
    assert event_bus.unprocessed_count(db) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 2
