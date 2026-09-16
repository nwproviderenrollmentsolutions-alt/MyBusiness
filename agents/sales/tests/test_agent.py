from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from agents.sales.agent import AGENT
from core import task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    Channel,
    ConversationStatus,
    DealStage,
    MessageDirection,
    RunStatus,
)
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Conversation, Lead, Message
from db.models.revenue import Deal
from db.models.runtime import OutboxEvent
from db.session import session_scope
from tests.conftest import make_policy
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _company_and_lead(
    *, estimated_value: Decimal | None, size_band: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        opportunity_id = None
        if estimated_value is not None:
            opportunity = Opportunity(
                title="MOCK Opportunity",
                segment="hvac",
                thesis="test",
                estimated_value_usd=estimated_value,
                is_mock=True,
            )
            session.add(opportunity)
            session.flush()
            opportunity_id = opportunity.id

        company = Company(
            name="MOCK Acme HVAC",
            domain="acme-hvac.invalid",
            size_band=size_band,
            opportunity_id=opportunity_id,
            is_mock=True,
        )
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id,
            full_name="MOCK Sam Rivera",
            email="sam@acme-hvac.invalid",
            is_mock=True,
        )
        session.add(prospect)
        session.flush()
        lead = Lead(
            prospect_id=prospect.id,
            company_id=company.id,
            opportunity_id=opportunity_id,
            is_mock=True,
        )
        session.add(lead)
        session.flush()
        return lead.id, company.id


def _deal_for(lead_id: uuid.UUID, *, stage: DealStage = DealStage.QUALIFICATION) -> uuid.UUID:
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        deal = Deal(
            lead_id=lead_id,
            opportunity_id=lead.opportunity_id if lead else None,
            stage=stage,
            is_mock=True,
        )
        session.add(deal)
        session.flush()
        return deal.id


def _enqueue_qualified(deal_id: uuid.UUID, lead_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        task = task_queue.enqueue(
            session,
            agent="sales",
            task_input={"deal_id": str(deal_id), "lead_id": str(lead_id)},
        )
        return task.id


def _enqueue_reply(deal_id: uuid.UUID, lead_id: uuid.UUID, message_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        task = task_queue.enqueue(
            session,
            agent="sales",
            task_input={
                "deal_id": str(deal_id),
                "lead_id": str(lead_id),
                "proposal_id": str(uuid.uuid4()),
                "message_id": str(message_id),
            },
        )
        return task.id


def test_qualified_deal_is_valued_from_opportunity_estimate(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=Decimal("4200"), size_band="11-50")
    deal_id = _deal_for(lead_id)

    task_id = _enqueue_qualified(deal_id, lead_id)
    status = runner.execute_task(task_id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.PROPOSAL
        assert deal.value_usd == Decimal("4200.00")

        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "deal.ready_for_proposal")
        )
        assert event is not None
        assert event.payload == {"deal_id": str(deal_id), "lead_id": str(lead_id)}


def test_qualified_deal_falls_back_to_size_band_value(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=None, size_band="51-200")
    deal_id = _deal_for(lead_id)

    task_id = _enqueue_qualified(deal_id, lead_id)
    runner.execute_task(task_id, worker_id="w1")

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.value_usd == Decimal("15000.00")


def test_already_valued_deal_is_a_no_op(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=Decimal("1000"), size_band=None)
    deal_id = _deal_for(lead_id, stage=DealStage.PROPOSAL)

    task_id = _enqueue_qualified(deal_id, lead_id)
    status = runner.execute_task(task_id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(OutboxEvent)) is None


def _reply_message(lead_id: uuid.UUID, *, body: str) -> uuid.UUID:
    with session_scope() as session:
        conversation = Conversation(
            lead_id=lead_id, channel=Channel.EMAIL, status=ConversationStatus.ENGAGED
        )
        session.add(conversation)
        session.flush()
        reply = Message(
            conversation_id=conversation.id,
            lead_id=lead_id,
            direction=MessageDirection.INBOUND,
            channel=Channel.EMAIL,
            state=ActionState.COMPLETED,
            body=body,
            is_mock=True,
        )
        session.add(reply)
        session.flush()
        return reply.id


def test_positive_reply_wins_the_deal(db, policy):
    policy(make_policy())
    lead_id, company_id = _company_and_lead(estimated_value=Decimal("2000"), size_band=None)
    deal_id = _deal_for(lead_id, stage=DealStage.PROPOSAL)
    message_id = _reply_message(lead_id, body="MOCK: sounds good, let's proceed")

    task_id = _enqueue_reply(deal_id, lead_id, message_id)
    runner.execute_task(task_id, worker_id="w1")

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.WON
        assert deal.won_at is not None
        assert deal.probability == 100

        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "deal.won"))
        assert event is not None
        assert event.payload == {
            "deal_id": str(deal_id),
            "lead_id": str(lead_id),
            "company_id": str(company_id),
        }


def test_negative_reply_loses_the_deal(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=Decimal("2000"), size_band=None)
    deal_id = _deal_for(lead_id, stage=DealStage.PROPOSAL)
    message_id = _reply_message(lead_id, body="MOCK: we went with a competitor")

    task_id = _enqueue_reply(deal_id, lead_id, message_id)
    runner.execute_task(task_id, worker_id="w1")

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.LOST
        assert deal.lost_at is not None

        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "deal.lost"))
        assert event is not None


def test_ambiguous_reply_leaves_the_deal_open(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=Decimal("2000"), size_band=None)
    deal_id = _deal_for(lead_id, stage=DealStage.PROPOSAL)
    message_id = _reply_message(lead_id, body="Can you clarify the payment terms?")

    task_id = _enqueue_reply(deal_id, lead_id, message_id)
    status = runner.execute_task(task_id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.PROPOSAL
        assert session.scalar(select(OutboxEvent)) is None


def test_already_closed_deal_ignores_a_further_reply(db, policy):
    policy(make_policy())
    lead_id, _ = _company_and_lead(estimated_value=Decimal("2000"), size_band=None)
    deal_id = _deal_for(lead_id, stage=DealStage.WON)
    message_id = _reply_message(lead_id, body="MOCK: we went with a competitor")

    task_id = _enqueue_reply(deal_id, lead_id, message_id)
    status = runner.execute_task(task_id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.WON


def test_unknown_deal_fails_permanently(db, policy):
    policy(make_policy())
    task_id = _enqueue_qualified(uuid.uuid4(), uuid.uuid4())
    status = runner.execute_task(task_id, worker_id="w1")
    assert status is RunStatus.FAILED
