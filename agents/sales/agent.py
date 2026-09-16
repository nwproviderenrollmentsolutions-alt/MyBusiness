"""Sales: values a newly-qualified deal, then closes it once the prospect answers a
proposal.

Two triggers, distinguished by which fields the event payload carries (``run()`` checks
``message_id`` the way Outreach checks ``ctx.approval is not None``):

* ``deal.qualified`` — sets the deal's value and probability (a deterministic rubric, see
  valuation.py) and advances it to ``proposal``, emitting ``deal.ready_for_proposal``.
* ``proposal.reply_received`` — classifies the reply (core/reply_signals.py, the same
  rule-based approach Qualification uses) and closes the deal ``won`` or ``lost``.
  An ambiguous reply changes nothing; a deal that isn't a clear yes or no is not forced
  into one.

Owns the ``deals`` table for writes (Qualification only inserts the initial row).
Uses no tools and no policy-gated action, like Lead Scoring: stage transitions and
valuation have no external-world effect, so there is nothing for the policy engine to
authorize here.
"""

from __future__ import annotations

from agents.sales.schemas import SalesInput
from agents.sales.valuation import ValuationInputs, value_deal
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
from core.states import assert_deal_transition
from core.task_queue import db_now
from db.enums import OPEN_DEAL_STAGES, DealStage
from db.models.market import Company, Opportunity
from db.models.pipeline import Lead, Message
from db.models.revenue import Deal


class SalesAgent(Agent):
    MANIFEST = AgentManifest(
        name="sales",
        version="1.0.0",
        description=(
            "Values a qualified deal and advances it to proposal, then closes it won or "
            "lost once the prospect replies to the proposal."
        ),
        input_model=SalesInput,
        allowed_tools=[],
        owns_tables=["deals"],
        subscribes_to=["deal.qualified", "proposal.reply_received"],
        timeout_seconds=15,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, SalesInput)
        session = ctx.session

        deal = session.get(Deal, agent_input.deal_id)
        if deal is None:
            raise PermanentError(f"deal {agent_input.deal_id} does not exist")
        lead = session.get(Lead, agent_input.lead_id)
        if lead is None:
            raise PermanentError(f"lead {agent_input.lead_id} does not exist")

        if agent_input.message_id is not None:
            return self._close_from_reply(ctx, deal, lead, agent_input)
        return self._value_and_advance(ctx, deal, lead)

    def _value_and_advance(self, ctx: AgentContext, deal: Deal, lead: Lead) -> AgentOutput:
        session = ctx.session
        if deal.stage is not DealStage.QUALIFICATION:
            # Already valued (or further along) by an earlier attempt or a redelivered
            # event. Idempotent no-op.
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "skipped": True, "stage": str(deal.stage)}
            )

        opportunity = (
            session.get(Opportunity, deal.opportunity_id) if deal.opportunity_id else None
        )
        company = session.get(Company, lead.company_id)
        result = value_deal(
            ValuationInputs(
                estimated_opportunity_value_usd=(
                    opportunity.estimated_value_usd if opportunity else None
                ),
                size_band=company.size_band if company else None,
            )
        )

        deal.value_usd = result.value_usd
        deal.probability = result.probability
        deal.qualification = {**deal.qualification, "valuation": result.rationale}
        assert_deal_transition(deal.stage, DealStage.PROPOSAL)
        deal.stage = DealStage.PROPOSAL
        session.flush()

        output = AgentOutput.success(
            result={
                "deal_id": str(deal.id),
                "value_usd": str(deal.value_usd),
                "stage": str(deal.stage),
            }
        )
        output.events.append(
            EventRequest(
                event_type="deal.ready_for_proposal",
                subject_type="deal",
                subject_id=deal.id,
                payload={"deal_id": str(deal.id), "lead_id": str(lead.id)},
            )
        )
        return output

    def _close_from_reply(
        self, ctx: AgentContext, deal: Deal, lead: Lead, agent_input: SalesInput
    ) -> AgentOutput:
        session = ctx.session
        if deal.stage not in OPEN_DEAL_STAGES:
            # Already closed by an earlier attempt or a redelivered event.
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "skipped": True, "stage": str(deal.stage)}
            )

        message = session.get(Message, agent_input.message_id)
        if message is None:
            raise PermanentError(f"message {agent_input.message_id} does not exist")

        classification = classify_reply(message.body)
        if classification.signal == "neutral":
            return AgentOutput.success(
                result={
                    "deal_id": str(deal.id),
                    "signal": classification.signal,
                    "action_taken": False,
                }
            )

        now = db_now(session)
        if classification.signal == "positive":
            assert_deal_transition(deal.stage, DealStage.WON)
            deal.stage = DealStage.WON
            deal.won_at = now
            deal.probability = 100
            session.flush()
            output = AgentOutput.success(
                result={
                    "deal_id": str(deal.id),
                    "signal": classification.signal,
                    "stage": str(deal.stage),
                }
            )
            output.events.append(
                EventRequest(
                    event_type="deal.won",
                    subject_type="deal",
                    subject_id=deal.id,
                    payload={
                        "deal_id": str(deal.id),
                        "lead_id": str(lead.id),
                        "company_id": str(lead.company_id),
                    },
                )
            )
            return output

        assert_deal_transition(deal.stage, DealStage.LOST)
        deal.stage = DealStage.LOST
        deal.lost_at = now
        deal.probability = 0
        deal.stage_reason = classification.rationale
        session.flush()
        output = AgentOutput.success(
            result={
                "deal_id": str(deal.id),
                "signal": classification.signal,
                "stage": str(deal.stage),
            }
        )
        output.events.append(
            EventRequest(
                event_type="deal.lost",
                subject_type="deal",
                subject_id=deal.id,
                payload={
                    "deal_id": str(deal.id),
                    "lead_id": str(lead.id),
                    "reason": classification.rationale,
                },
            )
        )
        return output


AGENT = SalesAgent()
