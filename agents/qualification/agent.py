"""Qualification: decides whether a lead who replied is worth turning into a deal.

Subscribes to ``conversation.reply_received``. Classification is rule-based (see
core/reply_signals.py), not an LLM call — the same reasoning as Lead Scoring: a decision
that gates the funnel into or out of the pipeline needs to be reproducible and inspectable,
and it is also the only option that works against a mock LLM provider, whose output is
fixed marker text with nothing in it to classify.

A positive reply creates the lead's one deal (``uq_deals_lead``) and emits
``deal.qualified``. A negative reply disqualifies the lead — reusing the same
``lead.disqualified`` event type Lead Scoring emits, since both are "this lead is not
being pursued further," regardless of which stage decided it. An ambiguous reply changes
nothing: the lead stays ``engaged`` rather than being forced into a decision the reply
didn't actually make.
"""

from __future__ import annotations

from agents.qualification.persistence import get_or_create_deal
from agents.qualification.schemas import QualificationInput
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    EventRequest,
)
from core.errors import PermanentError
from core.reply_signals import classify_reply
from core.states import assert_lead_transition
from db.enums import LeadStatus
from db.models.pipeline import Lead, Message


class QualificationAgent(Agent):
    MANIFEST = AgentManifest(
        name="qualification",
        version="1.0.0",
        description=(
            "Classifies a lead's reply as positive, negative, or ambiguous, and turns a "
            "positive reply into the lead's deal."
        ),
        input_model=QualificationInput,
        allowed_tools=[],
        owns_tables=["deals"],
        subscribes_to=["conversation.reply_received"],
        timeout_seconds=15,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, QualificationInput)
        session = ctx.session

        lead = session.get(Lead, agent_input.lead_id)
        if lead is None:
            raise PermanentError(f"lead {agent_input.lead_id} does not exist")

        if lead.status is not LeadStatus.ENGAGED:
            # Already qualified/disqualified by an earlier attempt, or a redelivered
            # event. Idempotent no-op.
            return AgentOutput.success(
                result={"lead_id": str(lead.id), "skipped": True, "status": str(lead.status)}
            )

        message = session.get(Message, agent_input.message_id)
        if message is None:
            raise PermanentError(f"message {agent_input.message_id} does not exist")

        classification = classify_reply(message.body)
        qualification = {
            "signal": classification.signal,
            "matched_phrase": classification.matched_phrase,
            "rationale": classification.rationale,
        }

        if classification.signal == "neutral":
            return AgentOutput.success(
                result={
                    "lead_id": str(lead.id),
                    "signal": classification.signal,
                    "action_taken": False,
                }
            )

        if classification.signal == "negative":
            assert_lead_transition(lead.status, LeadStatus.DISQUALIFIED)
            lead.status = LeadStatus.DISQUALIFIED
            lead.status_reason = classification.rationale
            session.flush()
            output = AgentOutput.success(
                result={
                    "lead_id": str(lead.id),
                    "signal": classification.signal,
                    "status": str(lead.status),
                }
            )
            output.events.append(
                EventRequest(
                    event_type="lead.disqualified",
                    subject_type="lead",
                    subject_id=lead.id,
                    payload={"lead_id": str(lead.id), "reason": classification.rationale},
                )
            )
            return output

        assert_lead_transition(lead.status, LeadStatus.QUALIFIED)
        lead.status = LeadStatus.QUALIFIED
        deal, created = get_or_create_deal(session, lead=lead, qualification=qualification)
        session.flush()

        output = AgentOutput.success(
            result={
                "lead_id": str(lead.id),
                "deal_id": str(deal.id),
                "signal": classification.signal,
                "status": str(lead.status),
            }
        )
        if created:
            output.events.append(
                EventRequest(
                    event_type="deal.qualified",
                    subject_type="deal",
                    subject_id=deal.id,
                    payload={"deal_id": str(deal.id), "lead_id": str(lead.id)},
                )
            )
        return output


AGENT = QualificationAgent()
