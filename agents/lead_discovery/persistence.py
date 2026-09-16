"""Dedupe-safe writes for companies, prospects, and leads.

The same segment can be searched more than once (a retried task, a second CEO command,
an opportunity re-validated), and provider results can overlap. These helpers make that
idempotent: re-running discovery finds the same rows again rather than duplicating them.

Follows the same get-or-create-with-IntegrityError-fallback idiom as
``core.task_queue.enqueue``: check first, insert in a savepoint, and if a concurrent
writer won the race, fall back to the row they created.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models.market import Company, Prospect
from db.models.pipeline import Lead
from providers.base import CompanyRecord, ProspectRecord


def get_or_create_company(
    session: Session,
    record: CompanyRecord,
    *,
    opportunity_id: uuid.UUID | None,
    is_mock: bool,
) -> Company:
    if record.domain:
        existing = session.scalar(select(Company).where(Company.domain == record.domain))
        if existing is not None:
            return existing

    company = Company(
        name=record.name,
        domain=record.domain,
        industry=record.industry,
        size_band=record.size_band,
        location=record.location,
        website=record.website,
        opportunity_id=opportunity_id,
        source="lead_discovery",
        attributes=record.attributes,
        is_mock=is_mock,
    )
    try:
        with session.begin_nested():
            session.add(company)
            session.flush()
    except IntegrityError:
        if not record.domain:
            raise
        existing = session.scalar(select(Company).where(Company.domain == record.domain))
        if existing is None:
            raise
        return existing
    return company


def get_or_create_prospect(
    session: Session, company: Company, record: ProspectRecord, *, is_mock: bool
) -> Prospect:
    if record.email:
        existing = session.scalar(select(Prospect).where(Prospect.email == record.email))
        if existing is not None:
            return existing

    prospect = Prospect(
        company_id=company.id,
        full_name=record.full_name,
        title=record.title,
        email=record.email,
        phone=record.phone,
        linkedin_url=record.linkedin_url,
        source="lead_discovery",
        is_mock=is_mock,
    )
    try:
        with session.begin_nested():
            session.add(prospect)
            session.flush()
    except IntegrityError:
        if not record.email:
            raise
        existing = session.scalar(select(Prospect).where(Prospect.email == record.email))
        if existing is None:
            raise
        return existing
    return prospect


def get_or_create_lead(
    session: Session,
    *,
    prospect: Prospect,
    company: Company,
    opportunity_id: uuid.UUID | None,
    is_mock: bool,
) -> tuple[Lead, bool]:
    """Dedupe by (prospect, opportunity) at the application level.

    Returns ``(lead, created)``. The caller must gate on ``created`` — not on the lead's
    current status — before emitting ``lead.discovered``: a pre-existing lead that
    happens to still be in ``discovered`` status (an earlier run that hasn't been picked
    up by enrichment yet) is not a new discovery, and re-emitting for it would create a
    second enrichment task for the same lead.

    Not backed by a database constraint — the schema's unique index is on
    (prospect_id, campaign_id), and NULL campaign_ids don't collide under standard SQL
    uniqueness semantics. Acceptable for a single-writer background job; if concurrent
    lead_discovery runs for overlapping segments becomes a real scenario, this needs a
    proper partial unique index.
    """
    query = select(Lead).where(Lead.prospect_id == prospect.id)
    query = (
        query.where(Lead.opportunity_id == opportunity_id)
        if opportunity_id is not None
        else query.where(Lead.opportunity_id.is_(None))
    )
    existing = session.scalar(query)
    if existing is not None:
        return existing, False

    lead = Lead(
        prospect_id=prospect.id,
        company_id=company.id,
        opportunity_id=opportunity_id,
        is_mock=is_mock,
    )
    session.add(lead)
    session.flush()
    return lead, True
