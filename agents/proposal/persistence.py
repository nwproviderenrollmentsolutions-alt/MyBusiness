"""Dedupe-safe persistence for the proposal document and the message that sends it.

Same idiom as agents.outreach.persistence: check first, insert in a savepoint, and fall
back to whichever row a concurrent writer created if the unique constraint fires.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.enums import ActionState, Channel, MessageDirection
from db.models.pipeline import Conversation, Lead, Message
from db.models.revenue import Deal, Proposal


def existing_conversation_for(session: Session, lead: Lead) -> Conversation | None:
    """The conversation Outreach already started with this lead.

    A proposal is sent as a continuation of that thread, not a new channel — there is
    always exactly one conversation per lead (agents.outreach.persistence creates it).
    """
    return session.scalar(select(Conversation).where(Conversation.lead_id == lead.id))


def get_or_create_proposal(
    session: Session,
    *,
    deal: Deal,
    version: int,
    title: str,
    content: dict[str, Any],
    total_value_usd: Decimal,
) -> tuple[Proposal, bool]:
    """Returns ``(proposal, created)``. Keyed by ``uq_proposals_deal_version``."""
    existing = session.scalar(
        select(Proposal).where(Proposal.deal_id == deal.id, Proposal.version == version)
    )
    if existing is not None:
        return existing, False

    proposal = Proposal(
        deal_id=deal.id,
        version=version,
        title=title,
        content=content,
        total_value_usd=total_value_usd,
        is_mock=deal.is_mock,
    )
    try:
        with session.begin_nested():
            session.add(proposal)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(Proposal).where(Proposal.deal_id == deal.id, Proposal.version == version)
        )
        if existing is None:
            raise
        return existing, False
    return proposal, True


def get_or_create_proposal_message(
    session: Session,
    *,
    conversation: Conversation,
    lead: Lead,
    proposal: Proposal,
    idempotency_key: str,
    subject: str,
    body: str,
    is_mock: bool,
) -> tuple[Message, bool]:
    """Returns ``(message, created)``. Keyed by the unique ``idempotency_key`` index."""
    existing = session.scalar(select(Message).where(Message.idempotency_key == idempotency_key))
    if existing is not None:
        return existing, False

    message = Message(
        conversation_id=conversation.id,
        lead_id=lead.id,
        proposal_id=proposal.id,
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
