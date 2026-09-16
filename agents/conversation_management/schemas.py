from __future__ import annotations

from core.agent_base import AgentInput


class ConversationManagementInput(AgentInput):
    """No fields: "check for replies" carries no parameters, and there is exactly one
    thing to do — fetch and process whatever is in the inbox.
    """
