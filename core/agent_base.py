"""The agent contract.

An agent is a bounded function with a declared interface. It returns *intent*; the worker
performs the effects — persisting runs, emitting events, enqueuing follow-ups, opening
approvals. An agent that crashes halfway therefore cannot leave a half-committed side
effect behind, and any agent can be replaced by another that honors the same manifest.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from core.errors import AgentError
from core.tool_registry import ToolBelt
from db.enums import ActionType, RiskLevel
from db.models.runtime import AgentTask, Approval


class AgentInput(BaseModel):
    """Base for per-agent input schemas.

    ``extra="forbid"`` so a task carrying fields the agent does not understand fails loudly
    instead of silently ignoring them.
    """

    model_config = ConfigDict(extra="forbid")


class TaskRequest(BaseModel):
    """A follow-up task the agent wants created. The worker creates it."""

    agent: str
    task_input: dict[str, Any] = Field(default_factory=dict)
    action_type: str | None = None
    priority: int = 0
    dedupe_key: str | None = None
    delay_seconds: float = 0.0
    max_attempts: int = 3


class EventRequest(BaseModel):
    """A domain event the agent wants emitted. The worker emits it transactionally."""

    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_type: str | None = None
    subject_id: uuid.UUID | None = None


class ApprovalRequest(BaseModel):
    """A decision the agent is handing to the CEO."""

    action_type: ActionType
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.MEDIUM
    subject_type: str | None = None
    subject_id: uuid.UUID | None = None
    policy_reason: str | None = None
    expires_in_hours: int | None = None


class UsageRecord(BaseModel):
    """What this run consumed. Persisted on the run row for cost reporting."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")

    def add_llm(self, *, tokens_in: int, tokens_out: int, cost_usd: Decimal) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd


class AgentOutput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: Literal["success", "failed", "needs_approval"]
    result: dict[str, Any] | None = None
    events: list[EventRequest] = Field(default_factory=list)
    follow_up_tasks: list[TaskRequest] = Field(default_factory=list)
    approval_request: ApprovalRequest | None = None
    error: AgentError | None = None
    usage: UsageRecord = Field(default_factory=UsageRecord)

    @classmethod
    def success(cls, **kwargs: Any) -> AgentOutput:
        return cls(status="success", **kwargs)

    @classmethod
    def needs_approval(cls, request: ApprovalRequest, **kwargs: Any) -> AgentOutput:
        return cls(status="needs_approval", approval_request=request, **kwargs)

    @classmethod
    def failure(cls, error: AgentError, **kwargs: Any) -> AgentOutput:
        return cls(status="failed", error=error, **kwargs)


class AgentManifest(BaseModel):
    """Everything about an agent that the runtime enforces.

    Declared as data next to the code so an agent cannot be deployed without stating its
    permissions, timeout, and retry policy.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    version: str
    description: str
    input_model: type[AgentInput]
    output_model: type[AgentOutput] = AgentOutput

    allowed_tools: list[str] = Field(default_factory=list)
    #: Actions this agent may take, subject to the policy engine.
    allowed_actions: list[ActionType] = Field(default_factory=list)
    #: Actions this agent must always route through a human, even if policy would allow.
    approval_required_actions: list[ActionType] = Field(default_factory=list)
    owns_tables: list[str] = Field(default_factory=list)
    subscribes_to: list[str] = Field(default_factory=list)

    timeout_seconds: int = 120
    max_attempts: int = 3
    retry_backoff_seconds: float = 2.0

    def requires_approval(self, action_type: ActionType) -> bool:
        return action_type in self.approval_required_actions


@dataclass
class AgentContext:
    """Everything an agent may touch during one run."""

    session: Session
    task: AgentTask
    run_id: uuid.UUID
    tools: ToolBelt
    dry_run: bool
    correlation_id: uuid.UUID
    #: Present when this run resumed after a human approved something. Carries the CEO's
    #: edits, which are what must actually execute.
    approval: Approval | None = None
    usage: UsageRecord = field(default_factory=UsageRecord)

    @property
    def task_id(self) -> uuid.UUID:
        return self.task.id

    @property
    def approved_payload(self) -> dict[str, Any] | None:
        return self.approval.effective_payload if self.approval else None


class Agent(ABC):
    """Base class for every agent.

    Subclasses declare MANIFEST and implement run(). They must not commit the session,
    call other agents, or perform side effects outside the tool belt.
    """

    MANIFEST: ClassVar[AgentManifest]

    @property
    def name(self) -> str:
        return self.MANIFEST.name

    @property
    def version(self) -> str:
        return self.MANIFEST.version

    @abstractmethod
    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput: ...

    def parse_input(self, raw: dict[str, Any]) -> AgentInput:
        return self.MANIFEST.input_model.model_validate(raw)
