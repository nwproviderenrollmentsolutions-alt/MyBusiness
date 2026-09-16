"""Engagement: leads, scoring, campaigns, conversations, and messages."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, MockFlagMixin, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import (
    ActionState,
    CampaignStatus,
    Channel,
    ConversationStatus,
    LeadStatus,
    MessageDirection,
)


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An outreach program. Carries its own kill switch."""

    __tablename__ = "campaigns"

    name: Mapped[str] = mapped_column(String(160))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), default=None, index=True
    )
    channel: Mapped[Channel] = state_column(Channel, default=Channel.EMAIL)
    status: Mapped[CampaignStatus] = state_column(
        CampaignStatus, default=CampaignStatus.DRAFT, index=True
    )
    #: Per-campaign ceiling, additionally capped by the global limit in policies.yaml.
    daily_send_limit: Mapped[int] = mapped_column(Integer, default=25)
    offer: Mapped[dict[str, Any]] = mapped_column(default=dict)
    #: Approved message templates. Copy outside these requires approval.
    templates: Mapped[list[dict[str, Any]]] = mapped_column(default=list)

    killed_at: Mapped[datetime | None] = mapped_column(default=None)
    killed_by: Mapped[str | None] = mapped_column(String(120), default=None)
    kill_reason: Mapped[str | None] = mapped_column(Text, default=None)

    leads: Mapped[list[Lead]] = relationship(back_populates="campaign")


class Lead(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    """One engagement attempt with one prospect under one campaign."""

    __tablename__ = "leads"
    __table_args__ = (
        # Duplicate prevention: a prospect cannot be worked twice by the same campaign.
        UniqueConstraint("prospect_id", "campaign_id", name="uq_leads_prospect_campaign"),
        Index("ix_leads_status_score", "status", "current_score"),
    )

    prospect_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), default=None, index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), default=None, index=True
    )
    status: Mapped[LeadStatus] = state_column(
        LeadStatus, default=LeadStatus.DISCOVERED, index=True
    )
    current_score: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Why the funnel stopped here. Required when status is disqualified or do_not_contact.
    status_reason: Mapped[str | None] = mapped_column(Text, default=None)
    touch_count: Mapped[int] = mapped_column(Integer, default=0)
    last_touch_at: Mapped[datetime | None] = mapped_column(default=None)

    campaign: Mapped[Campaign | None] = relationship(back_populates="leads")
    scores: Mapped[list[LeadScore]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    conversations: Mapped[list[Conversation]] = relationship(back_populates="lead")


class LeadScore(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Scoring history. Rows are appended, never updated, so score drift is visible."""

    __tablename__ = "lead_scores"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    score: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(80))
    rationale: Mapped[dict[str, Any]] = mapped_column(default=dict)
    scored_by_agent: Mapped[str] = mapped_column(String(80))

    lead: Mapped[Lead] = relationship(back_populates="scores")


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), default=None, index=True
    )
    channel: Mapped[Channel] = state_column(Channel, default=Channel.EMAIL)
    status: Mapped[ConversationStatus] = state_column(
        ConversationStatus, default=ConversationStatus.OPEN, index=True
    )
    subject: Mapped[str | None] = mapped_column(String(300), default=None)
    last_inbound_at: Mapped[datetime | None] = mapped_column(default=None)
    last_outbound_at: Mapped[datetime | None] = mapped_column(default=None)
    #: Running summary maintained by Conversation Management so later turns need not
    #: replay the whole thread into a prompt.
    summary: Mapped[str | None] = mapped_column(Text, default=None)

    lead: Mapped[Lead] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin, MockFlagMixin):
    """A single message. Outbound messages move through ActionState before sending."""

    __tablename__ = "messages"
    __table_args__ = (
        # Idempotency: the same logical send can never be executed twice, even if a
        # worker retries after a provider timeout.
        Index("ix_messages_idempotency_key_unique", "idempotency_key", unique=True),
        Index("ix_messages_state_scheduled", "state", "scheduled_for"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: Denormalized from the conversation so per-prospect rate limits are a single query.
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[MessageDirection] = state_column(MessageDirection)
    channel: Mapped[Channel] = state_column(Channel, default=Channel.EMAIL)
    state: Mapped[ActionState] = state_column(ActionState, default=ActionState.DRAFT, index=True)

    subject: Mapped[str | None] = mapped_column(String(300), default=None)
    body: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[str | None] = mapped_column(String(120), default=None)

    idempotency_key: Mapped[str | None] = mapped_column(String(200), default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), default=None)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), default=None
    )
    #: Set when this outbound message is a proposal send rather than outreach. Lets
    #: Conversation Management tell a proposal reply apart from an outreach reply and
    #: route it to Sales (``proposal.reply_received``) instead of Qualification
    #: (``conversation.reply_received``).
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("proposals.id", ondelete="SET NULL"), default=None, index=True
    )

    scheduled_for: Mapped[datetime | None] = mapped_column(default=None)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    received_at: Mapped[datetime | None] = mapped_column(default=None)
    #: Why this message ended in failed/suppressed/rejected.
    state_reason: Mapped[str | None] = mapped_column(Text, default=None)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
