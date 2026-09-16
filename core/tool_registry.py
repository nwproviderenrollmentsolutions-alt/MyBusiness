"""Tool registry and per-agent tool belts.

Two guarantees, both enforced here rather than trusted to agent code or to a model:

1. **Allowlist.** An agent can only call tools listed in its manifest. The check happens
   at call time, outside anything a model can influence.
2. **Policy first.** A tool marked ``requires_policy`` cannot be called without a
   ``Decision`` from the policy engine that authorized *that action type*. There is no
   code path from an agent to an external provider that skips the policy engine.

Under a SIMULATE decision the provider function is never invoked at all — the tool returns
its simulated result. Dry run is therefore not a provider-level courtesy; the real call
does not happen.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core import audit
from core.errors import PolicyViolation, ToolAccessDenied
from core.observability import get_logger
from core.policy import Decision
from db.enums import ActionType, PolicyDecision

logger = get_logger(__name__)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str
    #: When True, calls must carry an authorizing Decision.
    requires_policy: bool = False
    #: Which action types this tool may serve. Prevents laundering an authorization for a
    #: harmless action into permission to perform a dangerous one.
    allowed_action_types: frozenset[ActionType] = frozenset()
    #: Produces the stand-in result under a SIMULATE decision.
    simulate_fn: Callable[..., Any] | None = None


@dataclass
class ToolCallRecord:
    tool: str
    ok: bool
    duration_ms: int
    simulated: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "simulated": self.simulated,
            "error": self.error,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        if spec.requires_policy and not spec.allowed_action_types:
            raise ValueError(
                f"tool {spec.name!r} requires policy but declares no allowed action types"
            )
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolAccessDenied(f"tool {name!r} does not exist") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def clear(self) -> None:
        """Test affordance. Production registration happens once at import."""
        self._tools.clear()


#: The process-wide registry. Tools register into it at import time.
TOOLS = ToolRegistry()


@dataclass
class ToolBelt:
    """The subset of tools one agent may use during one run."""

    agent: str
    allowed: frozenset[str]
    session: Session
    registry: ToolRegistry = TOOLS
    task_id: uuid.UUID | None = None
    correlation_id: uuid.UUID | None = None
    calls: list[ToolCallRecord] = field(default_factory=list)

    def call(self, name: str, *, decision: Decision | None = None, **kwargs: Any) -> Any:
        spec = self.registry.get(name)
        self._authorize(spec, decision)

        simulated = decision is not None and decision.decision is PolicyDecision.SIMULATE
        started = time.perf_counter()
        try:
            result = self._simulate(spec, **kwargs) if simulated else spec.fn(**kwargs)
        except Exception as exc:
            self._record(spec.name, ok=False, started=started, simulated=simulated, error=str(exc))
            raise
        self._record(spec.name, ok=True, started=started, simulated=simulated, error=None)
        return result

    def _authorize(self, spec: ToolSpec, decision: Decision | None) -> None:
        if spec.name not in self.allowed:
            self._audit_denial(spec.name, "tool is not in the agent's manifest allowlist")
            raise ToolAccessDenied(
                f"agent {self.agent!r} may not call tool {spec.name!r}; "
                "add it to the agent manifest if this is intended"
            )
        if not spec.requires_policy:
            return

        if decision is None:
            self._audit_denial(spec.name, "no policy decision supplied")
            raise PolicyViolation(
                f"tool {spec.name!r} requires a policy decision", rule="missing_decision"
            )
        if decision.decision not in (PolicyDecision.ALLOW, PolicyDecision.SIMULATE):
            self._audit_denial(spec.name, f"policy returned {decision.decision}")
            raise PolicyViolation(
                f"tool {spec.name!r} blocked: {decision.reason}", rule=decision.rule
            )
        if decision.action_type is None or decision.action_type not in spec.allowed_action_types:
            self._audit_denial(
                spec.name, f"decision authorized {decision.action_type}, not this tool"
            )
            raise PolicyViolation(
                f"decision for {decision.action_type} does not authorize {spec.name!r}",
                rule="action_type_mismatch",
            )

    def _simulate(self, spec: ToolSpec, **kwargs: Any) -> Any:
        if spec.simulate_fn is None:
            raise PolicyViolation(
                f"tool {spec.name!r} has no simulation and cannot run in dry run",
                rule="no_simulation",
            )
        return spec.simulate_fn(**kwargs)

    def _record(
        self, name: str, *, ok: bool, started: float, simulated: bool, error: str | None
    ) -> None:
        record = ToolCallRecord(
            tool=name,
            ok=ok,
            duration_ms=int((time.perf_counter() - started) * 1000),
            simulated=simulated,
            error=error,
        )
        self.calls.append(record)
        logger.info(
            "tool call",
            extra={
                "agent": self.agent,
                "tool": name,
                "ok": ok,
                "simulated": simulated,
                "duration_ms": record.duration_ms,
                "task_id": str(self.task_id) if self.task_id else None,
            },
        )

    def _audit_denial(self, tool: str, reason: str) -> None:
        audit.record_agent(
            self.session,
            agent=self.agent,
            action="tool.denied",
            subject_type="tool",
            after={"tool": tool},
            reason=reason,
            task_id=self.task_id,
            correlation_id=self.correlation_id,
        )
