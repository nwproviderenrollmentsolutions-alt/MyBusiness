"""Regression coverage for a real bug: event-triggered tasks used to fall back to the
*global* dry-run default instead of inheriting from the task that triggered them, because
OutboxEvent had no dry_run column at all. Caught while building the Outreach end-to-end
test — a command submitted with dry_run=False produced a message that only ever
simulated, because the lead.scored event that triggered Outreach carried no memory of
that flag.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from core import event_bus, task_queue
from db.models.runtime import AgentTask
from tests.conftest import make_policy

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def wired(agents_config):
    agents_config({"downstream": ["some.event"]})


def test_a_live_tasks_event_produces_a_live_downstream_task(db, policy):
    policy(make_policy())
    origin = task_queue.enqueue(db, agent="origin", dry_run=False)
    event_bus.emit(db, event_type="some.event", payload={}, task_id=origin.id, dry_run=False)
    db.commit()

    event_bus.dispatch_pending(db)
    db.commit()

    spawned = db.scalar(select(AgentTask).where(AgentTask.agent == "downstream"))
    assert spawned.dry_run is False


def test_a_dry_run_tasks_event_produces_a_dry_run_downstream_task(db, policy):
    policy(make_policy())
    origin = task_queue.enqueue(db, agent="origin", dry_run=True)
    event_bus.emit(db, event_type="some.event", payload={}, task_id=origin.id, dry_run=True)
    db.commit()

    event_bus.dispatch_pending(db)
    db.commit()

    spawned = db.scalar(select(AgentTask).where(AgentTask.agent == "downstream"))
    assert spawned.dry_run is True


def test_an_event_with_no_originating_task_defaults_to_dry_run(db):
    """The safe direction to fail toward when there is nothing to inherit from."""
    event = event_bus.emit(db, event_type="x", payload={})
    assert event.dry_run is True
