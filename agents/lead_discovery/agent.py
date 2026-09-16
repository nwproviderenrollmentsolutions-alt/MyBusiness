"""Lead Discovery: finds companies matching a segment and one prospect at each.

Triggered directly by a CEO command ("find 100 qualified HVAC prospects") or by an
``opportunity.discovered`` event. Either way it produces the same thing: Company,
Prospect, and Lead rows in ``discovered`` status, and a ``lead.discovered`` event per
lead for Lead Enrichment to pick up.
"""

from __future__ import annotations

from agents.lead_discovery.persistence import (
    get_or_create_company,
    get_or_create_lead,
    get_or_create_prospect,
)
from agents.lead_discovery.schemas import LeadDiscoveryInput
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    EventRequest,
)
from core.errors import PermanentError
from core.policy import PolicyContext, evaluate
from db.enums import ActionType
from db.models.market import Opportunity

_DEFAULT_ROLES = ["Owner", "General Manager"]

#: A ceiling independent of whatever count a CEO command or event asks for. Discovering
#: leads has no per-message send limit to bound it (that's Outreach's job), so this exists
#: purely so a typo like "find 100000 leads" can't turn into an unbounded provider loop.
_MAX_COMPANIES_PER_RUN = 250


class LeadDiscoveryAgent(Agent):
    MANIFEST = AgentManifest(
        name="lead_discovery",
        version="1.0.0",
        description=(
            "Finds companies matching a target segment and one decision-maker prospect "
            "at each, creating discovered-status leads."
        ),
        input_model=LeadDiscoveryInput,
        allowed_tools=["leads.find_companies", "leads.find_prospects"],
        allowed_actions=[ActionType.LEAD_DISCOVER],
        owns_tables=["companies", "prospects", "leads"],
        subscribes_to=["opportunity.discovered"],
        timeout_seconds=90,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, LeadDiscoveryInput)
        session = ctx.session

        opportunity: Opportunity | None = None
        if agent_input.opportunity_id is not None:
            opportunity = session.get(Opportunity, agent_input.opportunity_id)
            if opportunity is None:
                raise PermanentError(f"opportunity {agent_input.opportunity_id} does not exist")

        roles = (opportunity.icp.get("roles") if opportunity else None) or _DEFAULT_ROLES
        icp: dict[str, object] = {"segment": agent_input.segment, "roles": roles}
        if opportunity is not None:
            icp.update(opportunity.icp)

        count = min(agent_input.count, _MAX_COMPANIES_PER_RUN)
        decision = evaluate(
            session,
            PolicyContext(
                action_type=ActionType.LEAD_DISCOVER,
                agent=self.name,
                dry_run=ctx.dry_run,
                task_id=ctx.task_id,
                correlation_id=ctx.correlation_id,
            ),
        )

        companies_response = ctx.tools.call(
            "leads.find_companies", decision=decision, icp=icp, limit=count
        )

        events: list[EventRequest] = []
        leads_created = 0
        companies_seen = 0

        for company_record in companies_response.companies:
            company = get_or_create_company(
                session,
                company_record,
                opportunity_id=opportunity.id if opportunity else None,
                is_mock=companies_response.is_mock,
            )
            companies_seen += 1

            prospects_response = ctx.tools.call(
                "leads.find_prospects",
                decision=decision,
                company=company_record,
                roles=roles,
                limit=1,
            )
            if not prospects_response.prospects:
                continue

            prospect = get_or_create_prospect(
                session,
                company,
                prospects_response.prospects[0],
                is_mock=prospects_response.is_mock,
            )
            lead, created = get_or_create_lead(
                session,
                prospect=prospect,
                company=company,
                opportunity_id=opportunity.id if opportunity else None,
                is_mock=companies_response.is_mock or prospects_response.is_mock,
            )
            if created:
                leads_created += 1
                events.append(
                    EventRequest(
                        event_type="lead.discovered",
                        subject_type="lead",
                        subject_id=lead.id,
                        payload={"lead_id": str(lead.id)},
                    )
                )

        output = AgentOutput.success(
            result={
                "segment": agent_input.segment,
                "companies_seen": companies_seen,
                "leads_created": leads_created,
            }
        )
        output.events = events
        return output


AGENT = LeadDiscoveryAgent()
