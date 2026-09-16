"""Runtime switches, including the global emergency stop.

Flags live in the database rather than config so the CEO can stop the system from the
dashboard and have it take effect immediately for every worker, with no deploy and no
restart. Every change is audited with who did it and why.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core import audit
from db.enums import ActorType
from db.models.runtime import SystemFlag

EMERGENCY_STOP = "emergency_stop"


def get_flag(session: Session, key: str) -> dict[str, Any] | None:
    flag = session.get(SystemFlag, key)
    return flag.value if flag else None


def set_flag(
    session: Session,
    key: str,
    value: dict[str, Any],
    *,
    updated_by: str,
    actor_type: ActorType = ActorType.HUMAN,
    reason: str | None = None,
) -> SystemFlag:
    flag = session.get(SystemFlag, key)
    before = dict(flag.value) if flag else None
    if flag is None:
        flag = SystemFlag(key=key, value=value, updated_by=updated_by, reason=reason)
        session.add(flag)
    else:
        flag.value = value
        flag.updated_by = updated_by
        flag.reason = reason
    session.flush()
    audit.record(
        session,
        actor_type=actor_type,
        actor=updated_by,
        action=f"flag.{key}.set",
        subject_type="system_flag",
        subject_id=None,
        before=before,
        after=value,
        reason=reason,
    )
    return flag


def is_emergency_stopped(session: Session) -> bool:
    value = get_flag(session, EMERGENCY_STOP)
    return bool(value and value.get("enabled"))


def engage_emergency_stop(session: Session, *, engaged_by: str, reason: str) -> None:
    """Halt every external-world action immediately."""
    set_flag(
        session,
        EMERGENCY_STOP,
        {"enabled": True},
        updated_by=engaged_by,
        reason=reason,
    )


def release_emergency_stop(session: Session, *, released_by: str, reason: str) -> None:
    set_flag(
        session,
        EMERGENCY_STOP,
        {"enabled": False},
        updated_by=released_by,
        reason=reason,
    )
