"""Append-only audit log.

This module has no update or delete function, by design. Writes join the caller's
transaction so an audit entry and the change it describes commit or roll back together —
there is no window in which a change exists without its audit record.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from core.observability import redact
from db.enums import ActorType
from db.models.runtime import AuditLog


def record(
    session: Session,
    *,
    actor_type: ActorType,
    actor: str,
    action: str,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    task_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_type=actor_type,
        actor=actor,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        before=redact(before) if before is not None else None,
        after=redact(after) if after is not None else None,
        reason=reason,
        task_id=task_id,
        run_id=run_id,
        correlation_id=correlation_id,
    )
    session.add(entry)
    session.flush()
    return entry


def record_agent(
    session: Session,
    *,
    agent: str,
    action: str,
    **kwargs: Any,
) -> AuditLog:
    return record(session, actor_type=ActorType.AGENT, actor=agent, action=action, **kwargs)


def record_human(
    session: Session,
    *,
    who: str,
    action: str,
    **kwargs: Any,
) -> AuditLog:
    return record(session, actor_type=ActorType.HUMAN, actor=who, action=action, **kwargs)


def record_system(session: Session, *, action: str, **kwargs: Any) -> AuditLog:
    return record(session, actor_type=ActorType.SYSTEM, actor="system", action=action, **kwargs)


def snapshot(obj: object, fields: tuple[str, ...]) -> dict[str, Any]:
    """Capture named fields of a model row for a before/after comparison."""
    return {field: _jsonable(getattr(obj, field, None)) for field in fields}


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
