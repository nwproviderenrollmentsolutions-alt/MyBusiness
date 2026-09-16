"""Suppression list and computed KPIs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import SuppressionScope


class Suppression(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Do-not-contact list. Checked before every outbound message, without exception.

    An entry here outranks every other policy setting, including autonomy.
    """

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("scope", "value", name="uq_suppressions_scope_value"),
        Index("ix_suppressions_value", "value"),
    )

    scope: Mapped[SuppressionScope] = state_column(SuppressionScope)
    #: Normalized lowercase: full address for email, bare hostname for domain, E.164 for phone.
    value: Mapped[str] = mapped_column(String(320))
    reason: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    created_by: Mapped[str] = mapped_column(String(120), default="system")


class KpiSnapshot(Base, UUIDPrimaryKeyMixin):
    """A metric computed over a period. Append-only so trends are reconstructable."""

    __tablename__ = "kpi_snapshots"
    __table_args__ = (Index("ix_kpi_snapshots_metric_period", "metric", "period_start"),)

    metric: Mapped[str] = mapped_column(String(80), index=True)
    dimensions: Mapped[dict[str, Any]] = mapped_column(default=dict)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    #: Metrics computed from mock data are flagged so they never read as real revenue.
    includes_mock_data: Mapped[bool] = mapped_column(default=False)
    computed_at: Mapped[datetime] = mapped_column(server_default=func.now())
