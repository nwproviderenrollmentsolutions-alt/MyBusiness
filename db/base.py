"""Declarative base, shared column conventions, and mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, MetaData, Numeric, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names so Alembic can autogenerate reversible migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

#: Money. Numeric, never float — rounding errors in revenue figures are unacceptable.
Money = Numeric(14, 2)
#: Token costs are fractions of a cent, so they need more precision than Money.
Cost = Numeric(12, 6)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[dict[str, Any]]: JSONB,
        datetime: DateTime(timezone=True),
        Decimal: Money,
        uuid.UUID: PGUUID(as_uuid=True),
    }


def state_column(enum_cls: type[StrEnum], **kwargs: Any) -> Any:
    """A state column stored as VARCHAR + CHECK rather than a native PG enum.

    Native enums require ALTER TYPE to add a value, which is awkward in a system whose
    state machines are expected to grow. The CHECK constraint gives the same database-level
    guarantee and is trivial to migrate.
    """
    return mapped_column(
        SAEnum(enum_cls, native_enum=False, length=32, validate_strings=True), **kwargs
    )


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class MockFlagMixin:
    """Marks rows produced by a mock provider.

    Requirement 22: simulated data must never be mistakable for real data. KPI queries
    exclude ``is_mock`` rows from revenue metrics and the dashboard labels them.
    """

    is_mock: Mapped[bool] = mapped_column(default=False, server_default=text("false"), index=True)
