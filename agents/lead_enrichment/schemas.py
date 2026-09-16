from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class LeadEnrichmentInput(AgentInput):
    """Matches the ``lead.discovered`` event payload exactly — see the event/payload
    contract in ARCHITECTURE.md."""

    lead_id: uuid.UUID
