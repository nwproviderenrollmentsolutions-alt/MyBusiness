from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from agents.customer.agent import AGENT
from core import approval_gate, flags, task_queue
from core.registry import AGENTS
from db.enums import ApprovalStatus, CustomerStatus, DealStage, RunStatus, TaskStatus
from db.models.market import Company, Prospect
from db.models.pipeline import Lead
from db.models.revenue import Customer, Deal
from db.models.runtime import AgentTask, Approval, OutboxEvent
from db.session import session_scope
from tests.conftest import make_policy
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _won_deal() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        company = Company(name="MOCK Acme HVAC", domain="acme-hvac.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id, full_name="MOCK Sam", email="sam@acme-hvac.invalid", is_mock=True
        )
        session.add(prospect)
        session.flush()
        lead = Lead(prospect_id=prospect.id, company_id=company.id, is_mock=True)
        session.add(lead)
        session.flush()
        deal = Deal(
            lead_id=lead.id, stage=DealStage.WON, value_usd=Decimal("4200"), is_mock=True
        )
        session.add(deal)
        session.flush()
        return deal.id, lead.id, company.id


def _enqueue(
    deal_id: uuid.UUID, lead_id: uuid.UUID, company_id: uuid.UUID, *, dry_run: bool = True
) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(
            session,
            agent="customer",
            task_input={
                "deal_id": str(deal_id),
                "lead_id": str(lead_id),
                "company_id": str(company_id),
            },
            dry_run=dry_run,
        )


def test_won_deal_requests_approval(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    task = _enqueue(deal_id, lead_id, company_id)

    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.NEEDS_APPROVAL

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        assert approval.status is ApprovalStatus.PENDING
        assert approval.action_type == "customer.create"
        assert approval.subject_type == "deal"
        assert approval.subject_id == deal_id
        assert session.scalar(select(Customer)) is None


def test_approval_creates_the_customer_and_emits_event(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    task = _enqueue(deal_id, lead_id, company_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        customer = session.scalar(select(Customer).where(Customer.company_id == company_id))
        assert customer is not None
        assert customer.status is CustomerStatus.ONBOARDING
        assert customer.deal_id == deal_id
        assert customer.contract_value_usd == Decimal("4200.00")
        assert customer.onboarded_at is not None

        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "customer.created")
        )
        assert event is not None
        assert event.payload == {
            "customer_id": str(customer.id),
            "company_id": str(company_id),
            "deal_id": str(deal_id),
        }


def test_dry_run_approval_simulates_without_creating_a_customer(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    task = _enqueue(deal_id, lead_id, company_id, dry_run=True)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        assert session.scalar(select(Customer)) is None


def test_rejection_leaves_no_customer_and_cancels_the_task(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    task = _enqueue(deal_id, lead_id, company_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.reject(session, approval.id, decided_by="ceo", notes="not yet")

    with session_scope() as session:
        assert session.scalar(select(Customer)) is None
        task_row = session.get(AgentTask, task.id)
        assert task_row.status is TaskStatus.CANCELLED


def test_emergency_stop_after_approval_blocks_creation(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    task = _enqueue(deal_id, lead_id, company_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")
        flags.engage_emergency_stop(session, engaged_by="ceo", reason="pause")

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        assert session.scalar(select(Customer)) is None


def test_deal_not_won_is_a_no_op(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        deal.stage = DealStage.PROPOSAL

    task = _enqueue(deal_id, lead_id, company_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(Approval)) is None


def test_already_a_customer_is_a_no_op(db, policy):
    policy(make_policy())
    deal_id, lead_id, company_id = _won_deal()
    with session_scope() as session:
        session.add(Customer(company_id=company_id, deal_id=deal_id, is_mock=True))

    task = _enqueue(deal_id, lead_id, company_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(Approval)) is None
        customers = list(session.scalars(select(Customer).where(Customer.company_id == company_id)))
        assert len(customers) == 1


def test_unknown_deal_fails_permanently(db, policy):
    policy(make_policy())
    task = _enqueue(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED
