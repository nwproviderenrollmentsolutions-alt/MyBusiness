"""CEO command intake: the audit trail of every instruction the CEO has issued."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import CommandIntent, CommandStatus


class CeoCommand(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One instruction from the CEO, from raw text to resolution.

    Owned exclusively by the Chief of Staff agent. ``correlation_id`` ties this row to any
    tasks it spawns, so the whole chain from command to outcome is traceable in the audit
    log by one id.
    """

    __tablename__ = "ceo_commands"

    raw_text: Mapped[str] = mapped_column()
    intent: Mapped[CommandIntent | None] = state_column(CommandIntent, default=None)
    status: Mapped[CommandStatus] = state_column(
        CommandStatus, default=CommandStatus.PENDING, index=True
    )
    #: What the interpreter extracted from the text (segment, count, campaign name, ...).
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: What agents this command needs that are not registered yet (only set when BLOCKED).
    required_agents: Mapped[list[str]] = mapped_column(JSONB, default=list)

    #: The plain-language answer shown to the CEO.
    response: Mapped[str | None] = mapped_column(default=None)
    #: Structured data behind the response, for a future dashboard to render.
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    created_by: Mapped[str] = mapped_column(default="ceo")
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(default=None)
