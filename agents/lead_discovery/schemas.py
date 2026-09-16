from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class LeadDiscoveryInput(AgentInput):
    """Same shape whether triggered by a CEO command ("find N leads in segment X") or by
    an ``opportunity.discovered`` event — see ARCHITECTURE.md's event/payload contract.
    """

    segment: str
    count: int = 10
    opportunity_id: uuid.UUID | None = None
