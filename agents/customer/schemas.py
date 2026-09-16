from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class CustomerInput(AgentInput):
    """Matches the ``deal.won`` event payload exactly."""

    deal_id: uuid.UUID
    lead_id: uuid.UUID
    company_id: uuid.UUID
