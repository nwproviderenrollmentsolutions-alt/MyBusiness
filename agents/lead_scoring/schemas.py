from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class LeadScoringInput(AgentInput):
    """Matches the ``lead.enriched`` event payload exactly."""

    lead_id: uuid.UUID
