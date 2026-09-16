"""Lead Scoring: ranks an enriched lead and disqualifies it if it isn't worth pursuing.

Subscribes to ``lead.enriched``. Scoring is rule-based (see scoring.py), not an LLM call —
a decision that gates a lead into or out of the funnel needs to be reproducible and
inspectable. Below the qualifying threshold, the lead is disqualified rather than passed
downstream: the system optimizes for qualified conversations, not for message volume, and
that has to be enforced somewhere concrete, not just stated as a principle.
"""

from __future__ import annotations

from agents.lead_scoring.schemas import LeadScoringInput
from agents.lead_scoring.scoring import DEFAULT_QUALIFYING_THRESHOLD, ScoringInputs, score_lead
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    EventRequest,
)
from core.errors import PermanentError
from core.states import assert_lead_transition
from db.enums import ActionType, LeadStatus
from db.models.market import Company, Prospect
from db.models.pipeline import Lead, LeadScore


class LeadScoringAgent(Agent):
    MANIFEST = AgentManifest(
        name="lead_scoring",
        version="1.0.0",
        description=(
            "Scores an enriched lead against a deterministic rubric (decision-maker "
            "title, company size, contactability) and disqualifies it below threshold "
            "rather than passing every lead downstream."
        ),
        input_model=LeadScoringInput,
        allowed_tools=[],
        allowed_actions=[ActionType.LEAD_SCORE],
        # Writes lead_scores (owned outright) and updates the score/status fields on the
        # lead it just scored — narrower than owning the whole leads table.
        owns_tables=["lead_scores"],
        subscribes_to=["lead.enriched"],
        timeout_seconds=15,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, LeadScoringInput)
        session = ctx.session

        lead = session.get(Lead, agent_input.lead_id)
        if lead is None:
            raise PermanentError(f"lead {agent_input.lead_id} does not exist")

        if lead.status is not LeadStatus.ENRICHED:
            # Already scored (or moved further) by an earlier attempt or a duplicate
            # delivery. Idempotent no-op.
            return AgentOutput.success(
                result={"lead_id": str(lead.id), "skipped": True, "status": str(lead.status)}
            )

        company = session.get(Company, lead.company_id)
        prospect = session.get(Prospect, lead.prospect_id)
        if company is None or prospect is None:
            raise PermanentError(f"lead {lead.id} is missing its company or prospect row")

        result = score_lead(
            ScoringInputs(
                title=prospect.title,
                size_band=company.size_band,
                has_email=bool(prospect.email),
                has_phone=bool(prospect.phone),
            )
        )

        session.add(
            LeadScore(
                lead_id=lead.id,
                score=result.score,
                model_version="rule_based_v1",
                rationale=result.rationale,
                scored_by_agent=self.name,
            )
        )
        lead.current_score = result.score

        qualifies = result.score >= DEFAULT_QUALIFYING_THRESHOLD
        target_status = LeadStatus.SCORED if qualifies else LeadStatus.DISQUALIFIED
        assert_lead_transition(lead.status, target_status)
        lead.status = target_status

        event_type = "lead.scored" if qualifies else "lead.disqualified"
        payload = {"lead_id": str(lead.id), "score": result.score}
        if not qualifies:
            lead.status_reason = (
                f"score {result.score}/{result.rationale['max_possible']} below "
                f"qualifying threshold {DEFAULT_QUALIFYING_THRESHOLD}"
            )
            payload["reason"] = lead.status_reason

        session.flush()

        output = AgentOutput.success(
            result={"lead_id": str(lead.id), "score": result.score, "status": str(lead.status)}
        )
        output.events.append(
            EventRequest(
                event_type=event_type, subject_type="lead", subject_id=lead.id, payload=payload
            )
        )
        return output


AGENT = LeadScoringAgent()
