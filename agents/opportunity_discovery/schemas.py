from __future__ import annotations

from core.agent_base import AgentInput


class OpportunityDiscoveryInput(AgentInput):
    """No fields are required. A CEO command like "find our best market opportunity"
    carries no parameters — the interpreter extracts none for this intent — so every
    field here must have a sensible default.
    """

    #: Optional steer, e.g. "HVAC contractors in the Pacific Northwest". None means the
    #: agent researches broadly rather than pretending to have a CEO-given focus.
    hint: str | None = None
