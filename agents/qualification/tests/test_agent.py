from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from agents.qualification.agent import AGENT
from core import task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    Channel,
    ConversationStatus,
    LeadStatus,
    MessageDirection,
    RunStatus,
)
from db.models.market import Company, Prospect
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


def _engaged_lead(*, reply_body: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with session_scope() as session:
        company = Company(name="MOCK Acme HVAC", domain="acme-hvac.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id,
            full_name="MOCK Sam Rivera",
            title="Owner",
            email="sam@acme-hvac.invalid",
            is_mock=True,
        )
        session.add(prospect)
        session.flush()
        lead = Lead(
            prospect_id=prospect.id,
            company_id=company.id,
            status=LeadStatus.ENGAGED,
            current_score=90,
            is_mock=True,
        )
        session.add(lead)
        session.flush()
        conversation = Conversation(
            lead_id=lead.id, channel=Channel.EMAIL, status=ConversationStatus.ENGAGED
        )
        session.add(conversation)
        session.flush()
        reply = Message(
            conversation_id=conversation.id,
            lead_id=lead.id,
            direction=MessageDirection.INBOUND,
            channel=Channel.EMAIL,
            state=ActionState.COMPLETED,
            body=reply_body,
            provider_message_id="reply-1",
            is_mock=True,
        )
        session.add(reply)
        session.flush()
        return lead.id, conversation.id, reply.id


def _enqueue(lead_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        task = task_queue.enqueue(
            session,
            agent="qualification",
            task_input={
                "lead_id": str(lead_id),
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
            },
        )
        return task.id


def test_positive_reply_creates_a_deal_and_qualifies_the_lead(db, policy):
    policy(make_policy())
    lead_id, conversation_id, message_id = _engaged_lead(
        reply_body="MOCK: yes, I'm interested, let's talk"
    )
    task_id = _enqueue(lead_id, conversation_id, message_id)

    status = runner.execute_task(task_id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.QUALIFIED

        deal = session.scalar(select(Deal).where(Deal.lead_id == lead_id))
        assert deal is not None
        assert deal.qualification["signal"] == "positive"

        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "deal.qualified")
        )
        assert event is not None
        assert event.payload == {"deal_id": str(deal.id), "lead_id": str(lead_id)}


def test_negative_reply_disqualifies_the_lead_without_a_deal(db, policy):
    policy(make_policy())
    lead_id, conversation_id, message_id = _engaged_lead(
        reply_body="MOCK: no thanks, not interested"
    )
    task_id = _enqueue(lead_id, conversation_id, message_id)

    runner.execute_task(task_id, worker_id="w1")

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.DISQUALIFIED
        assert session.scalar(select(Deal).where(Deal.lead_id == lead_id)) is None

        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "lead.disqualified")
        )
        assert event is not None
        assert event.payload["lead_id"] == str(lead_id)


def test_ambiguous_reply_leaves_the_lead_engaged(db, policy):
    policy(make_policy())
    lead_id, conversation_id, message_id = _engaged_lead(
        reply_body="Can you resend the pricing sheet?"
    )
    task_id = _enqueue(lead_id, conversation_id, message_id)

    status = runner.execute_task(task_id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.ENGAGED
        assert session.scalar(select(Deal).where(Deal.lead_id == lead_id)) is None
        assert session.scalar(select(OutboxEvent)) is None


def test_already_qualified_lead_is_a_no_op(db, policy):
    policy(make_policy())
    lead_id, conversation_id, message_id = _engaged_lead(reply_body="let's talk")
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        lead.status = LeadStatus.QUALIFIED

    task_id = _enqueue(lead_id, conversation_id, message_id)
    status = runner.execute_task(task_id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(Deal).where(Deal.lead_id == lead_id)) is None


def test_unknown_lead_fails_permanently(db, policy):
    policy(make_policy())
    task_id = _enqueue(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    status = runner.execute_task(task_id, worker_id="w1")
    assert status is RunStatus.FAILED
