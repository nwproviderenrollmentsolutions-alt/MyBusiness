"""Dedupe-safe persistence for the conversation and draft message outreach creates.

Same idiom as agents.lead_discovery.persistence: check first, insert in a savepoint, and
fall back to whichever row a concurrent writer created if the unique constraint fires.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.enums import ActionState, Channel, MessageDirection
from db.models.pipeline import Conversation, Lead, Message


def get_or_create_conversation(session: Session, lead: Lead) -> Conversation:
    existing = session.scalar(select(Conversation).where(Conversation.lead_id == lead.id))
    if existing is not None:
        return existing

    conversation = Conversation(
        lead_id=lead.id, campaign_id=lead.campaign_id, channel=Channel.EMAIL
    )
    session.add(conversation)
    session.flush()
    return conversation


def get_or_create_draft_message(
    session: Session,
    *,
    conversation: Conversation,
    lead: Lead,
    idempotency_key: str,
    subject: str,
    body: str,
    is_mock: bool,
) -> tuple[Message, bool]:
    """Returns ``(message, created)``. The caller must gate on ``created`` — not on the
    message's current state — before requesting approval or counting a touch: a
    pre-existing draft from a redelivered event is not a new attempt.
    """
    existing = session.scalar(select(Message).where(Message.idempotency_key == idempotency_key))
    if existing is not None:
        return existing, False

    message = Message(
        conversation_id=conversation.id,
        lead_id=lead.id,
        direction=MessageDirection.OUTBOUND,
        channel=Channel.EMAIL,
        state=ActionState.DRAFT,
        subject=subject,
        body=body,
        idempotency_key=idempotency_key,
        is_mock=is_mock,
    )
    try:
        with session.begin_nested():
            session.add(message)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Message).where(Message.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing, False
    return message, True
