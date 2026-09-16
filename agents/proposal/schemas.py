from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class ProposalInput(AgentInput):
    """Matches the ``deal.ready_for_proposal`` event payload exactly."""

    deal_id: uuid.UUID
    lead_id: uuid.UUID
