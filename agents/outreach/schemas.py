from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class OutreachInput(AgentInput):
    """Matches the ``lead.scored`` event payload exactly.

    ``score`` isn't used directly — the fact that Lead Scoring emitted ``lead.scored``
    rather than ``lead.disqualified`` already means the lead cleared the bar — but it must
    be declared because ``AgentInput`` forbids fields it doesn't know about, and the
    payload carries it.
    """

    lead_id: uuid.UUID
    score: int | None = None
