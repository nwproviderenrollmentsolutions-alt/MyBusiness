"""Response and request models for the CEO-facing API.

Slim, hand-written schemas rather than exposing ORM models directly — the API's contract
should be something the CEO's dashboard (or a future integration) can depend on, not an
accident of what columns a table happens to have.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CommandCreate(BaseModel):
    text: str = Field(min_length=1)
    created_by: str = "ceo"


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_text: str
    intent: str | None
    status: str
    response: str | None
    result: dict[str, Any] | None
    created_by: str
    created_at: datetime
    resolved_at: datetime | None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type: str
    status: str
    risk: str
    summary: str
    payload: dict[str, Any]
    edited_payload: dict[str, Any] | None
    policy_reason: str | None
    subject_type: str | None
    subject_id: uuid.UUID | None
    requested_by_agent: str
    created_at: datetime
    expires_at: datetime | None
    decided_by: str | None
    decided_at: datetime | None
    decision_notes: str | None


class ApprovalApprove(BaseModel):
    decided_by: str = "ceo"
    notes: str | None = None
    #: The CEO's edit to the proposed payload, if any. What actually executes.
    edited_payload: dict[str, Any] | None = None


class ApprovalReject(BaseModel):
    decided_by: str = "ceo"
    notes: str | None = None


class SystemFlagRequest(BaseModel):
    actor: str = "ceo"
    reason: str | None = None


class StatusOut(BaseModel):
    counts: dict[str, int]
    active_campaigns: int
    pending_approvals: int
    task_counts: dict[str, int]
    domain_agents_running: list[str]


class DecisionsOut(BaseModel):
    pending_approvals: list[dict[str, Any]]
    failed_tasks: list[dict[str, Any]]


class HealthOut(BaseModel):
    status: str
    database: bool
    emergency_stop: bool
    dry_run: bool
    environment: str
