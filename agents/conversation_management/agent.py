"""Conversation Management: processes inbound replies to outreach.

Triggered by the CEO's "check for replies" command rather than an event subscription —
there is no periodic-polling mechanism in this system yet, so checking the inbox is
something the CEO asks for, not something that happens on a schedule. That's a real
limitation, not a design stance: a future scheduled trigger would call the same agent
with the same input, unchanged.

Fetches every reply each run (no ``since`` cursor is persisted anywhere yet) and relies on
idempotent message creation to skip replies already processed — less efficient than a
cursor, but simple and correct, which matters more at this stage.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.conversation_management.schemas import ConversationManagementInput
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    EventRequest,
)
from core.states import assert_lead_transition, can_transition_lead
from db.enums import ActionState, Channel, ConversationStatus, LeadStatus, MessageDirection
from db.models.pipeline import Conversation, Lead, Message
from providers.base import InboundMessage


@dataclass(frozen=True)
class _Outcome:
    kind: str  # "processed" | "duplicate" | "unmatched"
    event: EventRequest | None = None


class ConversationManagementAgent(Agent):
    MANIFEST = AgentManifest(
        name="conversation_management",
        version="1.0.0",
        description=(
            "Fetches inbound replies, matches each to the outreach message it answers, "
            "and advances the lead and conversation accordingly."
        ),
        input_model=ConversationManagementInput,
        allowed_tools=["email.fetch_replies"],
        owns_tables=["messages", "conversations"],
        timeout_seconds=60,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, ConversationManagementInput)
        session = ctx.session

        inbox = ctx.tools.call("email.fetch_replies")

        counts = {"processed": 0, "duplicate": 0, "unmatched": 0}
        events: list[EventRequest] = []
        for inbound in inbox.messages:
            outcome = self._process_one(session, inbound, is_mock=inbox.is_mock)
            counts[outcome.kind] += 1
            if outcome.event is not None:
                events.append(outcome.event)

        output = AgentOutput.success(
            result={
                "fetched": len(inbox.messages),
                "processed": counts["processed"],
                "skipped_duplicate": counts["duplicate"],
                "unmatched": counts["unmatched"],
            }
        )
        output.events = events
        return output

    def _process_one(
        self, session: Session, inbound: InboundMessage, *, is_mock: bool
    ) -> _Outcome:
        existing = session.scalar(
            select(Message).where(Message.provider_message_id == inbound.provider_message_id)
        )
        if existing is not None:
            return _Outcome("duplicate")

        original = None
        if inbound.in_reply_to:
            original = session.scalar(
                select(Message).where(Message.provider_message_id == inbound.in_reply_to)
            )
        if original is None:
            return _Outcome("unmatched")

        conversation = session.get(Conversation, original.conversation_id)
        if conversation is None:
            return _Outcome("unmatched")

        reply = Message(
            conversation_id=conversation.id,
            lead_id=original.lead_id,
            direction=MessageDirection.INBOUND,
            channel=Channel.EMAIL,
            state=ActionState.COMPLETED,
            subject=inbound.subject,
            body=inbound.body,
            provider_message_id=inbound.provider_message_id,
            received_at=inbound.received_at,
            is_mock=is_mock,
        )
        session.add(reply)
        session.flush()

        conversation.last_inbound_at = inbound.received_at
        conversation.status = ConversationStatus.ENGAGED

        lead = session.get(Lead, original.lead_id)
        if lead is not None and can_transition_lead(lead.status, LeadStatus.ENGAGED):
            assert_lead_transition(lead.status, LeadStatus.ENGAGED)
            lead.status = LeadStatus.ENGAGED

        session.flush()
        return _Outcome(
            "processed",
            event=EventRequest(
                event_type="conversation.reply_received",
                subject_type="conversation",
                subject_id=conversation.id,
                payload={
                    "conversation_id": str(conversation.id),
                    "lead_id": str(original.lead_id),
                    "message_id": str(reply.id),
                },
            ),
        )


AGENT = ConversationManagementAgent()
