"""Error types.

The retryable/permanent split matters: a permanent error must not consume the retry
budget three times before the CEO sees it, and a transient provider timeout must not be
mistaken for a broken agent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MyBusinessError(Exception):
    """Base class for everything this system raises deliberately."""


class RetryableError(MyBusinessError):
    """Transient. The task goes back on the queue with backoff."""


class PermanentError(MyBusinessError):
    """Will fail identically on retry. The task fails terminally and surfaces to the CEO."""


class ToolAccessDenied(PermanentError):
    """An agent attempted a tool outside its manifest allowlist."""


class PolicyViolation(PermanentError):
    """The policy engine denied the action."""

    def __init__(self, message: str, *, rule: str) -> None:
        super().__init__(message)
        self.rule = rule


class ApprovalStateError(PermanentError):
    """An approval was decided twice, or decided after expiry."""


class AgentNotRegistered(PermanentError):
    pass


class InvalidStateTransition(PermanentError):
    pass


class TaskLeaseLost(RetryableError):
    """Another worker took over this task, usually after a lease expiry."""


class AgentError(BaseModel):
    """Structured error returned by an agent, persisted on the run record."""

    type: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_exception(cls, exc: BaseException) -> AgentError:
        return cls(
            type=type(exc).__name__,
            message=str(exc),
            retryable=isinstance(exc, RetryableError),
        )
