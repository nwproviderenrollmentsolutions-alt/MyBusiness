"""Agent registry.

Maps an agent name to an implementation. The worker resolves tasks through this, so
replacing an agent means pointing ``config/agents.yaml`` at a different module — no other
code changes, no redeployment of anything else.
"""

from __future__ import annotations

import importlib

from config.settings import get_agents_config
from core.agent_base import Agent
from core.errors import AgentNotRegistered


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        name = agent.MANIFEST.name
        if name in self._agents:
            raise ValueError(f"agent {name!r} is already registered")
        self._validate(agent)
        self._agents[name] = agent

    def get(self, name: str) -> Agent:
        try:
            return self._agents[name]
        except KeyError:
            raise AgentNotRegistered(
                f"agent {name!r} is not registered; check config/agents.yaml"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._agents)

    def all(self) -> list[Agent]:
        return [self._agents[name] for name in self.names()]

    def clear(self) -> None:
        self._agents.clear()

    def load_from_config(self) -> list[str]:
        """Import and register every enabled agent named in config/agents.yaml."""
        loaded = []
        for name, entry in get_agents_config().agents.items():
            if not entry.enabled:
                continue
            module = importlib.import_module(entry.module)
            agent = getattr(module, "AGENT", None)
            if agent is None:
                raise AgentNotRegistered(
                    f"module {entry.module!r} for agent {name!r} does not expose AGENT"
                )
            if agent.MANIFEST.name != name:
                raise AgentNotRegistered(
                    f"agent in {entry.module!r} is named {agent.MANIFEST.name!r}, "
                    f"but config/agents.yaml registers it as {name!r}"
                )
            if name not in self._agents:
                self.register(agent)
            loaded.append(name)
        return loaded

    @staticmethod
    def _validate(agent: Agent) -> None:
        """Reject an agent whose manifest omits anything the runtime depends on."""
        manifest = agent.MANIFEST
        if not manifest.version:
            raise ValueError(f"agent {manifest.name!r} must declare a version")
        if manifest.timeout_seconds <= 0:
            raise ValueError(f"agent {manifest.name!r} must declare a positive timeout")
        if manifest.max_attempts < 1:
            raise ValueError(f"agent {manifest.name!r} must allow at least one attempt")
        overlap = set(manifest.allowed_actions) & set(manifest.approval_required_actions)
        if overlap:
            raise ValueError(
                f"agent {manifest.name!r} lists {sorted(overlap)} as both freely allowed "
                "and approval-required; one of the two is wrong"
            )


#: Process-wide registry.
AGENTS = AgentRegistry()
