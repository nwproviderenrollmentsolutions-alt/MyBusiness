"""Runtime substrate: task queue, run records, approvals, event outbox, audit log."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, Cost, TimestampMixin, UUIDPrimaryKeyMixin, state_column
from db.enums import ActorType, ApprovalStatus, RiskLevel, RunStatus, TaskStatus


class AgentTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A unit of agent work. This table is the queue."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        # The polling index: claim the highest-priority due task.
        Index("ix_agent_tasks_claim", "status", "run_after", "priority"),
        # Idempotent task creation: the same event cannot spawn duplicate work.
        Index("ix_agent_tasks_dedupe_key_unique", "dedupe_key", unique=True),
    )

    agent: Mapped[str] = mapped_column(String(80), index=True)
    action_type: Mapped[str | None] = mapped_column(String(80), default=None)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    status: Mapped[TaskStatus] = state_column(TaskStatus, default=TaskStatus.PENDING, index=True)
    #: Higher runs first.
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    #: Not eligible for lease before this time. Drives both scheduling and retry backoff.
    run_after: Mapped[datetime] = mapped_column(server_default=func.now())

    lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    leased_by: Mapped[str | None] = mapped_column(String(120), default=None)

    dedupe_key: Mapped[str | None] = mapped_column(String(300), default=None)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL"), default=None, index=True
    )
    #: Shared by every task descended from one CEO command or one triggering event.
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), index=True
    )
    #: Per-task override of the global setting. A task created in dry run stays in dry run.
    dry_run: Mapped[bool] = mapped_column(default=True)

    created_by_actor_type: Mapped[ActorType] = state_column(ActorType, default=ActorType.SYSTEM)
    created_by_actor: Mapped[str] = mapped_column(String(120), default="system")

    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    runs: Mapped[list[AgentRun]] = relationship(back_populates="task")


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution attempt of one task. Carries cost and timing for observability."""

    __tablename__ = "agent_runs"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(80), index=True)
    agent_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[RunStatus] = state_column(RunStatus, default=RunStatus.RUNNING, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Cost, default=Decimal("0"))
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    #: [{tool, ok, duration_ms}] — enough to see which tool is slow or failing.
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(default=list)

    dry_run: Mapped[bool] = mapped_column(default=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    error: Mapped[dict[str, Any] | None] = mapped_column(default=None)

    task: Mapped[AgentTask] = relationship(back_populates="runs")


class Approval(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A decision only the CEO may make.

    ``payload`` is what the agent proposed; ``edited_payload`` is what the CEO changed it
    to. Both are kept so the audit trail shows the agent's intent and the human's judgment.
    """

    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_status_created", "status", "created_at"),)

    action_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[ApprovalStatus] = state_column(
        ApprovalStatus, default=ApprovalStatus.PENDING, index=True
    )
    risk: Mapped[RiskLevel] = state_column(RiskLevel, default=RiskLevel.MEDIUM)

    #: One sentence the CEO can decide on without opening anything else.
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    edited_payload: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    #: Which policy rule forced this approval.
    policy_reason: Mapped[str | None] = mapped_column(Text, default=None)

    subject_type: Mapped[str | None] = mapped_column(String(60), default=None)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)

    requested_by_agent: Mapped[str] = mapped_column(String(80))
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL"), default=None, index=True
    )
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)

    expires_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    decided_by: Mapped[str | None] = mapped_column(String(120), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decision_notes: Mapped[str | None] = mapped_column(Text, default=None)

    @property
    def effective_payload(self) -> dict[str, Any]:
        """What actually executes: the CEO's edit if there was one, else the proposal."""
        return self.edited_payload if self.edited_payload is not None else self.payload


class OutboxEvent(Base, UUIDPrimaryKeyMixin):
    """A domain event, written in the same transaction as the change that caused it."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        # Partial index: the dispatcher only ever scans unprocessed rows.
        Index(
            "ix_outbox_events_unprocessed",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_type: Mapped[str | None] = mapped_column(String(60), default=None)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)

    emitted_by: Mapped[str] = mapped_column(String(80), default="system")
    task_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only history of everything that happened and who caused it.

    No update or delete path exists in application code. ``task_id`` and ``run_id`` are
    plain UUIDs rather than foreign keys on purpose: no other table's lifecycle may ever
    cascade into, or block a write to, the audit log.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_subject", "subject_type", "subject_id"),
        Index("ix_audit_logs_action_created", "action", "created_at"),
    )

    actor_type: Mapped[ActorType] = state_column(ActorType, index=True)
    actor: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(120))

    subject_type: Mapped[str | None] = mapped_column(String(60), default=None)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)

    before: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    after: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    #: Why this happened — the policy rule, the CEO's note, or the triggering event.
    reason: Mapped[str | None] = mapped_column(Text, default=None)

    task_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), default=None)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), default=None, index=True
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class SystemFlag(Base):
    """Runtime switches the CEO can flip without a deploy.

    Holds the global emergency stop. Kept in the database rather than config so it takes
    effect immediately for every worker and survives a restart.
    """

    __tablename__ = "system_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_by: Mapped[str] = mapped_column(String(120), default="system")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    reason: Mapped[str | None] = mapped_column(Text, default=None)
