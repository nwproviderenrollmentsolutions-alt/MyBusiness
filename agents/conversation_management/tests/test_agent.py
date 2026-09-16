from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from agents.conversation_management.agent import AGENT
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
from db.models.revenue import Deal, Proposal
from db.models.runtime import AgentRun, AgentTask, OutboxEvent
from db.session import session_scope
from providers.factory import get_email_provider
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _sent_outreach(*, provider_message_id: str = "mock-msg-1") -> tuple[uuid.UUID, uuid.UUID]:
    """A lead already contacted, with a completed outbound message. Returns
    (lead_id, conversation_id)."""
    with session_scope() as session:
        company = Company(
            name="MOCK Acme", domain=f"acme-{provider_message_id}.invalid", is_mock=True
        )
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id,
            full_name="MOCK Sam",
            email=f"sam-{provider_message_id}@acme.invalid",
            is_mock=True,
        )
        session.add(prospect)
        session.flush()
        lead = Lead(
            prospect_id=prospect.id,
            company_id=company.id,
            status=LeadStatus.CONTACTED,
            is_mock=True,
        )
        session.add(lead)
        session.flush()
        conversation = Conversation(lead_id=lead.id, channel=Channel.EMAIL)
        session.add(conversation)
        session.flush()
        outbound = Message(
            conversation_id=conversation.id,
            lead_id=lead.id,
            direction=MessageDirection.OUTBOUND,
            channel=Channel.EMAIL,
            state=ActionState.COMPLETED,
            subject="Quick question",
            body="MOCK outbound body",
            provider_message_id=provider_message_id,
            idempotency_key=f"key-{provider_message_id}",
            is_mock=True,
        )
        session.add(outbound)
        session.flush()
        return lead.id, conversation.id


def _enqueue() -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(session, agent="conversation_management", task_input={})


def test_a_matched_reply_advances_the_lead_and_conversation(db):
    lead_id, conversation_id = _sent_outreach()
    get_email_provider().seed_reply(
        from_email="sam@acme.invalid", body="MOCK reply body", in_reply_to="mock-msg-1"
    )

    task = _enqueue()
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.ENGAGED

        conversation = session.get(Conversation, conversation_id)
        assert conversation.status is ConversationStatus.ENGAGED
        assert conversation.last_inbound_at is not None

        inbound = session.scalar(
            select(Message).where(Message.direction == MessageDirection.INBOUND)
        )
        assert inbound is not None
        assert inbound.body == "MOCK reply body"
        assert inbound.state is ActionState.COMPLETED


def test_reply_emits_an_event_for_future_qualification(db):
    _sent_outreach()
    get_email_provider().seed_reply(
        from_email="sam@acme.invalid", body="MOCK reply", in_reply_to="mock-msg-1"
    )

    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "conversation.reply_received")
        )
        assert event is not None
        assert "conversation_id" in event.payload
        assert "lead_id" in event.payload


def test_an_unmatched_reply_is_recorded_but_not_linked_to_anything(db):
    """A reply to a message we have no record of sending can't be attributed — it must
    not crash, and must not silently invent a conversation."""
    get_email_provider().seed_reply(
        from_email="stranger@nowhere.invalid", body="who is this", in_reply_to="never-sent"
    )

    task = _enqueue()
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        run = session.scalar(select(AgentRun))
        assert run.output["unmatched"] == 1
        assert run.output["processed"] == 0
        assert session.scalar(select(Message)) is None


def test_rerunning_after_the_same_reply_does_not_double_process_it(db):
    _sent_outreach()
    get_email_provider().seed_reply(
        from_email="sam@acme.invalid", body="MOCK reply", in_reply_to="mock-msg-1"
    )

    first = _enqueue()
    runner.execute_task(first.id, worker_id="w1")
    second = _enqueue()
    runner.execute_task(second.id, worker_id="w1")

    with session_scope() as session:
        inbound_count = session.scalar(
            select(Message).where(Message.direction == MessageDirection.INBOUND)
        )
        assert inbound_count is not None
        all_inbound = list(
            session.scalars(select(Message).where(Message.direction == MessageDirection.INBOUND))
        )
        assert len(all_inbound) == 1

        events = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "conversation.reply_received")
            )
        )
        assert len(events) == 1


def test_no_replies_is_a_clean_success(db):
    task = _enqueue()
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        run = session.scalar(select(AgentRun))
        assert run.output == {
            "fetched": 0,
            "processed": 0,
            "skipped_duplicate": 0,
            "unmatched": 0,
        }


def _sent_proposal(*, provider_message_id: str = "mock-proposal-1") -> tuple[uuid.UUID, uuid.UUID]:
    """A qualified deal with a completed outbound proposal message. Returns
    (deal_id, lead_id)."""
    lead_id, conversation_id = _sent_outreach(provider_message_id=f"outreach-{provider_message_id}")
    with session_scope() as session:
        deal = Deal(lead_id=lead_id, value_usd=Decimal("4200"), is_mock=True)
        session.add(deal)
        session.flush()
        proposal = Proposal(deal_id=deal.id, version=1, title="MOCK Proposal", is_mock=True)
        session.add(proposal)
        session.flush()
        outbound = Message(
            conversation_id=conversation_id,
            lead_id=lead_id,
            proposal_id=proposal.id,
            direction=MessageDirection.OUTBOUND,
            channel=Channel.EMAIL,
            state=ActionState.COMPLETED,
            subject="Proposal",
            body="MOCK proposal body",
            provider_message_id=provider_message_id,
            idempotency_key=f"key-{provider_message_id}",
            is_mock=True,
        )
        session.add(outbound)
        session.flush()
        return deal.id, lead_id


def test_a_reply_to_a_proposal_emits_proposal_reply_received_not_conversation(db):
    deal_id, lead_id = _sent_proposal()
    get_email_provider().seed_reply(
        from_email="sam@acme.invalid",
        body="MOCK: sounds good, let's proceed",
        in_reply_to="mock-proposal-1",
    )

    task = _enqueue()
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        assert (
            session.scalar(
                select(OutboxEvent).where(OutboxEvent.event_type == "conversation.reply_received")
            )
            is None
        )
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "proposal.reply_received")
        )
        assert event is not None
        assert event.payload["deal_id"] == str(deal_id)
        assert event.payload["lead_id"] == str(lead_id)

        proposal_id = event.payload["proposal_id"]
        inbound = session.scalar(
            select(Message).where(Message.direction == MessageDirection.INBOUND)
        )
        assert event.payload["message_id"] == str(inbound.id)
        proposal = session.get(Proposal, uuid.UUID(proposal_id))
        assert proposal.deal_id == deal_id


def test_multiple_replies_are_all_processed_in_one_run(db):
    _sent_outreach(provider_message_id="mock-msg-a")
    _sent_outreach(provider_message_id="mock-msg-b")
    get_email_provider().seed_reply(
        from_email="one@acme.invalid", body="reply one", in_reply_to="mock-msg-a"
    )
    get_email_provider().seed_reply(
        from_email="two@acme.invalid", body="reply two", in_reply_to="mock-msg-b"
    )

    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        inbound = list(
            session.scalars(select(Message).where(Message.direction == MessageDirection.INBOUND))
        )
        assert len(inbound) == 2
