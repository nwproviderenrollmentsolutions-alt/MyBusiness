"""Input/output contracts for the Chief of Staff agent."""

from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class ChiefOfStaffInput(AgentInput):
    """The agent processes one CEO command per task, identified by id.

    The task carries only the id, not the command text, so the ceo_commands row (owned by
    this agent) stays the single source of truth for what was actually asked.
    """

    command_id: uuid.UUID
