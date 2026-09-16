"""Customer: turns a won deal into a customer record, once the CEO approves.

Subscribes to ``deal.won``. Unlike Message and Proposal, no row is created before
approval — there is nothing here that needs to exist early for a duplicate check to find
(a customer's natural key is ``company_id``, checked directly), so the approval's payload
alone carries what ``_create_after_approval`` needs. ``customer.create`` is
approval-required by default policy (config/policies.yaml) and money-adjacent enough that
this agent never overrides that with a template-approved-style forcing flag: the ordinary
autonomy switch is exactly the right gate here.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from agents.customer.schemas import CustomerInput
from core.agent_base import (
    Agent,
    AgentContext,
    AgentInput,
    AgentManifest,
    AgentOutput,
    ApprovalRequest,
    EventRequest,
)
from core.errors import PermanentError
from core.policy import PolicyContext, evaluate, evaluate_after_approval
from core.task_queue import db_now
from db.enums import ActionType, CustomerStatus, DealStage, RiskLevel
from db.models.market import Company
from db.models.pipeline import Lead
from db.models.revenue import Customer, Deal


class CustomerAgent(Agent):
    MANIFEST = AgentManifest(
        name="customer",
        version="1.0.0",
        description=(
            "Requests approval to onboard a won deal's company as a customer, and "
            "creates the customer record once approved."
        ),
        input_model=CustomerInput,
        allowed_tools=[],
        approval_required_actions=[ActionType.CUSTOMER_CREATE],
        owns_tables=["customers"],
        subscribes_to=["deal.won"],
        timeout_seconds=15,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, CustomerInput)
        session = ctx.session

        deal = session.get(Deal, agent_input.deal_id)
        if deal is None:
            raise PermanentError(f"deal {agent_input.deal_id} does not exist")
        company = session.get(Company, agent_input.company_id)
        if company is None:
            raise PermanentError(f"company {agent_input.company_id} does not exist")

        if ctx.approval is not None:
            return self._create_after_approval(ctx, deal, company)
        return self._propose(ctx, deal, company)

    def _propose(self, ctx: AgentContext, deal: Deal, company: Company) -> AgentOutput:
        session = ctx.session
        if deal.stage is not DealStage.WON:
            # A redelivered event, or something else already moved the deal on.
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "skipped": True, "stage": str(deal.stage)}
            )

        existing = session.scalar(select(Customer).where(Customer.company_id == company.id))
        if existing is not None:
            return AgentOutput.success(
                result={
                    "deal_id": str(deal.id),
                    "customer_id": str(existing.id),
                    "skipped": True,
                }
            )

        decision = evaluate(
            session,
            PolicyContext(
                action_type=ActionType.CUSTOMER_CREATE,
                agent=self.name,
                dry_run=ctx.dry_run,
                lead_id=deal.lead_id,
                value_usd=deal.value_usd,
                task_id=ctx.task_id,
                correlation_id=ctx.correlation_id,
            ),
        )

        if decision.denied:
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "state": "denied", "reason": decision.reason}
            )

        if not decision.needs_approval:
            raise PermanentError(
                f"unexpected policy decision for customer creation: {decision.rule}"
            )

        return AgentOutput.needs_approval(
            ApprovalRequest(
                action_type=ActionType.CUSTOMER_CREATE,
                summary=f"Onboard {company.name} as a customer (deal ${deal.value_usd})",
                payload={
                    "deal_id": str(deal.id),
                    "company_id": str(company.id),
                    "contract_value_usd": str(deal.value_usd),
                },
                risk=RiskLevel.MEDIUM,
                subject_type="deal",
                subject_id=deal.id,
                policy_reason=decision.reason,
            ),
            result={"deal_id": str(deal.id), "company_id": str(company.id)},
        )

    def _create_after_approval(
        self, ctx: AgentContext, deal: Deal, company: Company
    ) -> AgentOutput:
        session = ctx.session
        approved = ctx.approved_payload or {}

        existing = session.scalar(select(Customer).where(Customer.company_id == company.id))
        if existing is not None:
            # Already created by an earlier attempt at this same resumed task.
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "customer_id": str(existing.id), "skipped": True}
            )

        decision = evaluate_after_approval(
            session,
            PolicyContext(
                action_type=ActionType.CUSTOMER_CREATE,
                agent=self.name,
                dry_run=ctx.dry_run,
                lead_id=deal.lead_id,
                value_usd=deal.value_usd,
                task_id=ctx.task_id,
                correlation_id=ctx.correlation_id,
            ),
        )

        if decision.denied:
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "state": "denied", "reason": decision.reason}
            )

        if decision.simulate:
            return AgentOutput.success(
                result={"deal_id": str(deal.id), "simulated": True}
            )

        contract_value_usd = Decimal(str(approved.get("contract_value_usd", deal.value_usd)))
        customer = Customer(
            company_id=company.id,
            deal_id=deal.id,
            status=CustomerStatus.ONBOARDING,
            contract_value_usd=contract_value_usd,
            onboarded_at=db_now(session),
            is_mock=company.is_mock,
        )
        session.add(customer)
        session.flush()

        # Not this agent's table to write, but a straightforward, real signal to leave on
        # the lead: the funnel's terminal, successful state.
        lead = session.get(Lead, deal.lead_id)
        if lead is not None:
            lead.status_reason = f"won as customer {customer.id}"

        output = AgentOutput.success(
            result={
                "deal_id": str(deal.id),
                "customer_id": str(customer.id),
                "company_id": str(company.id),
            }
        )
        output.events.append(
            EventRequest(
                event_type="customer.created",
                subject_type="customer",
                subject_id=customer.id,
                payload={
                    "customer_id": str(customer.id),
                    "company_id": str(company.id),
                    "deal_id": str(deal.id),
                },
            )
        )
        return output


AGENT = CustomerAgent()
