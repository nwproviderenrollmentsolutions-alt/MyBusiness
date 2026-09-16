"""Close and beyond: deals, proposals, customers."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, MockFlagMixin, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import ActionState, CustomerStatus, DealStage


class Deal(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    __tablename__ = "deals"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), default=None, index=True
    )
    stage: Mapped[DealStage] = state_column(DealStage, default=DealStage.QUALIFICATION, index=True)
    value_usd: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    probability: Mapped[int] = mapped_column(Integer, default=0)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))
    expected_close_date: Mapped[date | None] = mapped_column(Date, default=None)
    won_at: Mapped[datetime | None] = mapped_column(default=None)
    lost_at: Mapped[datetime | None] = mapped_column(default=None)
    stage_reason: Mapped[str | None] = mapped_column(Text, default=None)
    qualification: Mapped[dict[str, Any]] = mapped_column(default=dict)

    proposals: Mapped[list[Proposal]] = relationship(
        back_populates="deal", cascade="all, delete-orphan"
    )


class Proposal(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    """A versioned proposal. Sending one is an external action, so it has an ActionState."""

    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("deal_id", "version", name="uq_proposals_deal_version"),)

    deal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deals.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[ActionState] = state_column(ActionState, default=ActionState.DRAFT, index=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[dict[str, Any]] = mapped_column(default=dict)
    total_value_usd: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"))

    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), default=None
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    accepted_at: Mapped[datetime | None] = mapped_column(default=None)
    rejected_at: Mapped[datetime | None] = mapped_column(default=None)
    state_reason: Mapped[str | None] = mapped_column(Text, default=None)

    deal: Mapped[Deal] = relationship(back_populates="proposals")


class Customer(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    __tablename__ = "customers"

    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), index=True
    )
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deals.id", ondelete="SET NULL"), default=None, index=True
    )
    status: Mapped[CustomerStatus] = state_column(
        CustomerStatus, default=CustomerStatus.ONBOARDING, index=True
    )
    plan: Mapped[str | None] = mapped_column(String(120), default=None)
    mrr_usd: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    contract_value_usd: Mapped[Decimal] = mapped_column(default=Decimal("0"))
    onboarded_at: Mapped[datetime | None] = mapped_column(default=None)
    churned_at: Mapped[datetime | None] = mapped_column(default=None)
