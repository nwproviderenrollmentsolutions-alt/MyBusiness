from __future__ import annotations

import uuid

from core.agent_base import AgentInput


class QualificationInput(AgentInput):
    """Matches the ``conversation.reply_received`` event payload exactly."""

    conversation_id: uuid.UUID
    lead_id: uuid.UUID
    message_id: uuid.UUID
