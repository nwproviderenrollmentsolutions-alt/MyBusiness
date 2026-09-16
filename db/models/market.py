"""Top of funnel: business opportunities, target companies, and the people at them."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, MockFlagMixin, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import OpportunityStatus


class Opportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    """A business opportunity: a market and an offer worth pursuing.

    Distinct from ``Deal``, which is a specific sale to a specific company.
    """

    __tablename__ = "opportunities"

    title: Mapped[str] = mapped_column(String(200))
    segment: Mapped[str] = mapped_column(String(120), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    #: Ideal customer profile used by Lead Discovery to search for companies.
    icp: Mapped[dict[str, Any]] = mapped_column(default=dict)
    offer: Mapped[dict[str, Any]] = mapped_column(default=dict)
    estimated_value_usd: Mapped[Decimal | None] = mapped_column(default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    status: Mapped[OpportunityStatus] = state_column(
        OpportunityStatus, default=OpportunityStatus.PROPOSED, index=True
    )
    source: Mapped[str] = mapped_column(String(80), default="unknown")
    discovered_by_agent: Mapped[str | None] = mapped_column(String(80), default=None)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(default=list)

    companies: Mapped[list[Company]] = relationship(back_populates="opportunity")


class Company(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    __tablename__ = "companies"
    __table_args__ = (Index("ix_companies_domain_unique", "domain", unique=True),)

    name: Mapped[str] = mapped_column(String(200), index=True)
    #: Normalized lowercase hostname. The natural dedupe key for a company.
    domain: Mapped[str | None] = mapped_column(String(255), default=None)
    industry: Mapped[str | None] = mapped_column(String(120), default=None)
    size_band: Mapped[str | None] = mapped_column(String(40), default=None)
    location: Mapped[str | None] = mapped_column(String(160), default=None)
    website: Mapped[str | None] = mapped_column(String(500), default=None)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), default=None, index=True
    )
    source: Mapped[str] = mapped_column(String(80), default="unknown")
    #: Untrusted research output. Never interpreted as instructions (ARCHITECTURE.md §13).
    research: Mapped[dict[str, Any]] = mapped_column(default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)

    opportunity: Mapped[Opportunity | None] = relationship(back_populates="companies")
    prospects: Mapped[list[Prospect]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Prospect(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    """A person. Every column below except role metadata is PII.

    PII is excluded from LLM prompts unless the task genuinely requires it, and access
    is recorded in the audit log.
    """

    __tablename__ = "prospects"
    __table_args__ = (Index("ix_prospects_email_unique", "email", unique=True),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(160))
    title: Mapped[str | None] = mapped_column(String(160), default=None)
    #: Normalized lowercase. Unique so the same person cannot enter the funnel twice.
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    phone: Mapped[str | None] = mapped_column(String(40), default=None)
    linkedin_url: Mapped[str | None] = mapped_column(String(500), default=None)
    timezone: Mapped[str | None] = mapped_column(String(64), default=None)
    source: Mapped[str] = mapped_column(String(80), default="unknown")
    research: Mapped[dict[str, Any]] = mapped_column(default=dict)

    company: Mapped[Company] = relationship(back_populates="prospects")
