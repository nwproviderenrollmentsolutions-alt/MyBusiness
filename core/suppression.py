"""Do-not-contact list, identifier normalization, and duplicate detection.

A suppression entry outranks every other setting in the system, including autonomy and
CEO-set thresholds. If someone asked not to be contacted, nothing may contact them.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import audit
from db.enums import ActorType, SuppressionScope
from db.models.pipeline import Message
from db.models.support import Suppression

_NON_DIGITS = re.compile(r"[^\d+]")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def normalize_domain(domain: str) -> str:
    value = domain.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0]
    return value.removeprefix("www.")


def normalize_phone(phone: str) -> str:
    return _NON_DIGITS.sub("", phone.strip())


def domain_of(email: str) -> str | None:
    _, _, domain = normalize_email(email).partition("@")
    return domain or None


def find_suppression(
    session: Session,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> Suppression | None:
    """Return the matching suppression entry, if any.

    An email matches either exactly or by its domain, so suppressing a whole company after
    a complaint is one row rather than one per person.
    """
    candidates: list[tuple[SuppressionScope, str]] = []
    if email:
        candidates.append((SuppressionScope.EMAIL, normalize_email(email)))
        domain = domain_of(email)
        if domain:
            candidates.append((SuppressionScope.DOMAIN, domain))
    if phone:
        candidates.append((SuppressionScope.PHONE, normalize_phone(phone)))

    for scope, value in candidates:
        found = session.scalar(
            select(Suppression).where(Suppression.scope == scope, Suppression.value == value)
        )
        if found is not None:
            return found
    return None


def is_suppressed(session: Session, *, email: str | None = None, phone: str | None = None) -> bool:
    return find_suppression(session, email=email, phone=phone) is not None


def add_suppression(
    session: Session,
    *,
    scope: SuppressionScope,
    value: str,
    reason: str,
    source: str = "manual",
    created_by: str = "system",
    actor_type: ActorType = ActorType.SYSTEM,
) -> Suppression:
    """Add to the do-not-contact list. Idempotent."""
    normalized = {
        SuppressionScope.EMAIL: normalize_email,
        SuppressionScope.DOMAIN: normalize_domain,
        SuppressionScope.PHONE: normalize_phone,
    }[scope](value)

    existing = session.scalar(
        select(Suppression).where(Suppression.scope == scope, Suppression.value == normalized)
    )
    if existing is not None:
        return existing

    entry = Suppression(
        scope=scope,
        value=normalized,
        reason=reason,
        source=source,
        created_by=created_by,
    )
    session.add(entry)
    session.flush()
    audit.record(
        session,
        actor_type=actor_type,
        actor=created_by,
        action="suppression.added",
        subject_type="suppression",
        subject_id=entry.id,
        after={"scope": scope, "value": normalized, "source": source},
        reason=reason,
    )
    return entry


def build_idempotency_key(*parts: str | uuid.UUID | int | None) -> str:
    """Stable key for one logical outbound action.

    Hashed so the key stays within the column limit regardless of input length, and so it
    carries no PII of its own.
    """
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def message_exists(session: Session, idempotency_key: str) -> Message | None:
    """Has this exact outbound action already been created? Prevents double sends."""
    return session.scalar(select(Message).where(Message.idempotency_key == idempotency_key))
