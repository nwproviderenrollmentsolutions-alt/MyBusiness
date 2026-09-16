"""Transactional outbox.

An event is written in the same transaction as the state change that produced it, so an
event never announces something that was rolled back, and a committed change never loses
its event. Dispatch then turns events into tasks for whichever agents subscribe.

Emitters do not know their consumers. That is what lets a Fulfillment agent start
reacting to ``deal.won`` later without touching the Sales agent.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.settings import get_agents_config
from core import audit, task_queue
from core.observability import get_logger
from db.enums import ActorType
from db.models.runtime import OutboxEvent

logger = get_logger(__name__)


def emit(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    emitted_by: str = "system",
    task_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        payload=payload or {},
        subject_type=subject_type,
        subject_id=subject_id,
        emitted_by=emitted_by,
        task_id=task_id,
        correlation_id=correlation_id,
    )
    session.add(event)
    session.flush()
    return event


def dispatch_pending(session: Session, *, limit: int = 50) -> int:
    """Turn unprocessed events into tasks for their subscribers.

    Returns the number of events processed. Rows are locked with SKIP LOCKED so several
    dispatchers can run at once, and each event/agent pair gets a deterministic dedupe key
    so a redelivery cannot enqueue the same work twice.
    """
    agents_config = get_agents_config()
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.processed_at.is_(None))
        .order_by(OutboxEvent.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    events = list(session.scalars(stmt))

    for event in events:
        subscribers = agents_config.subscribers_of(event.event_type)
        for agent_name in subscribers:
            task_queue.enqueue(
                session,
                agent=agent_name,
                task_input={
                    "event_type": event.event_type,
                    "event_id": str(event.id),
                    "subject_type": event.subject_type,
                    "subject_id": str(event.subject_id) if event.subject_id else None,
                    "payload": event.payload,
                },
                dedupe_key=f"event:{event.id}:{agent_name}",
                correlation_id=event.correlation_id,
                parent_task_id=event.task_id,
                created_by_actor_type=ActorType.SYSTEM,
                created_by_actor="event_bus",
            )

        event.processed_at = func.now()
        event.attempts += 1
        session.flush()

        audit.record_system(
            session,
            action="event.dispatched",
            subject_type="outbox_event",
            subject_id=event.id,
            after={"event_type": event.event_type, "subscribers": subscribers},
            task_id=event.task_id,
            correlation_id=event.correlation_id,
        )
        if not subscribers:
            # Not an error — an event with no listener yet is normal while the system is
            # being built out. Logged so a missing subscription is visible.
            logger.info(
                "event dispatched with no subscribers",
                extra={"event_type": event.event_type, "event_id": str(event.id)},
            )

    return len(events)


def unprocessed_count(session: Session) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.processed_at.is_(None))
        )
        or 0
    )
