"""Dedupe-safe creation of the deal a qualified lead becomes.

Same idiom as agents.outreach.persistence: check first, insert in a savepoint, and fall
back to whichever row a concurrent writer created if the unique constraint fires.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.enums import DealStage
from db.models.pipeline import Lead
from db.models.revenue import Deal


def get_or_create_deal(
    session: Session, *, lead: Lead, qualification: dict[str, Any]
) -> tuple[Deal, bool]:
    """Returns ``(deal, created)``. One deal per lead (``uq_deals_lead``)."""
    existing = session.scalar(select(Deal).where(Deal.lead_id == lead.id))
    if existing is not None:
        return existing, False

    deal = Deal(
        lead_id=lead.id,
        opportunity_id=lead.opportunity_id,
        stage=DealStage.QUALIFICATION,
        qualification=qualification,
        is_mock=lead.is_mock,
    )
    try:
        with session.begin_nested():
            session.add(deal)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Deal).where(Deal.lead_id == lead.id))
        if existing is None:
            raise
        return existing, False
    return deal, True
