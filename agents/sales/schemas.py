from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class SalesInput(AgentInput):
    """Covers both events this agent subscribes to.

    ``deal.qualified`` carries only ``deal_id``/``lead_id``. ``proposal.reply_received``
    additionally carries ``proposal_id`` and ``message_id`` — the presence of
    ``message_id`` is what ``run()`` uses to tell the two triggers apart, the same way
    Outreach branches on ``ctx.approval is not None``.
    """

    deal_id: uuid.UUID
    lead_id: uuid.UUID
    proposal_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
