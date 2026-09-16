from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from agents.proposal.agent import AGENT
from core import approval_gate, flags, suppression, task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    ApprovalStatus,
    Channel,
    ConversationStatus,
    DealStage,
    RunStatus,
    SuppressionScope,
    TaskStatus,
)
from db.models.market import Company, Prospect
from db.models.pipeline import Conversation, Lead, Message
from db.models.revenue import Deal, Proposal
from db.models.runtime import AgentTask, Approval, OutboxEvent
from db.session import session_scope
from providers.factory import get_email_provider
from tests.conftest import make_policy
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _priced_deal() -> tuple[uuid.UUID, uuid.UUID]:
    """A lead that already went through outreach (has a conversation) and whose deal has
    been valued by Sales (stage=PROPOSAL)."""
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
        lead = Lead(prospect_id=prospect.id, company_id=company.id, is_mock=True)
        session.add(lead)
        session.flush()
        conversation = Conversation(lead_id=lead.id, channel=Channel.EMAIL)
        session.add(conversation)
        session.flush()
        deal = Deal(
            lead_id=lead.id, stage=DealStage.PROPOSAL, value_usd=Decimal("4200"), is_mock=True
        )
        session.add(deal)
        session.flush()
        return deal.id, lead.id


def _enqueue(deal_id: uuid.UUID, lead_id: uuid.UUID, *, dry_run: bool = True) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(
            session,
            agent="proposal",
            task_input={"deal_id": str(deal_id), "lead_id": str(lead_id)},
            dry_run=dry_run,
        )


def test_draft_requests_approval(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.NEEDS_APPROVAL
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.touch_count == 1

        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.PENDING_APPROVAL
        message = session.scalar(select(Message).where(Message.proposal_id == proposal.id))
        assert message is not None
        assert message.state is ActionState.PENDING_APPROVAL
        assert "MOCK" in message.body

        approval = session.scalar(select(Approval))
        assert approval.status is ApprovalStatus.PENDING
        assert approval.subject_type == "proposal"
        assert approval.subject_id == proposal.id


def test_approval_then_dry_run_simulates_the_send(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id, dry_run=True)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.COMPLETED
        assert get_email_provider().sent == []

        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "proposal.sent"))
        assert event is not None
        assert event.payload["deal_id"] == str(deal_id)


def test_approval_then_live_run_actually_sends(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.COMPLETED
        message = session.scalar(select(Message).where(Message.proposal_id == proposal.id))
        assert message.state is ActionState.COMPLETED
        conversation = session.get(Conversation, message.conversation_id)
        assert conversation.status is ConversationStatus.AWAITING_REPLY

    sent = get_email_provider().sent
    assert len(sent) == 1
    assert sent[0]["to"] == "sam@acme-hvac.invalid"


def test_ceo_edit_is_what_actually_sends(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(
            session,
            approval.id,
            decided_by="ceo",
            edited_payload={**approval.payload, "subject": "CEO rewrote it"},
        )

    runner.execute_task(task.id, worker_id="w1")

    sent = get_email_provider().sent
    assert sent[0]["subject"] == "CEO rewrote it"


def test_rejection_marks_the_proposal_rejected_and_stops_the_task(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.reject(session, approval.id, decided_by="ceo", notes="too pricey")

    with session_scope() as session:
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.REJECTED
        task_row = session.get(AgentTask, task.id)
        assert task_row.status is TaskStatus.CANCELLED

    assert get_email_provider().sent == []


def test_suppressed_recipient_is_never_drafted_into_an_approval(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    with session_scope() as session:
        suppression.add_suppression(
            session, scope=SuppressionScope.EMAIL, value="sam@acme-hvac.invalid", reason="opted out"
        )

    task = _enqueue(deal_id, lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status.value == "do_not_contact"
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.SUPPRESSED
        assert session.scalar(select(Approval)) is None


def test_emergency_stop_after_approval_blocks_the_send(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task = _enqueue(deal_id, lead_id, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")
        flags.engage_emergency_stop(session, engaged_by="ceo", reason="pause")

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal.state is ActionState.SUPPRESSED
    assert get_email_provider().sent == []


def test_deal_not_yet_at_proposal_stage_is_a_no_op(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        deal.stage = DealStage.QUALIFICATION

    task = _enqueue(deal_id, lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(Proposal).where(Proposal.deal_id == deal_id)) is None


def test_redelivered_event_does_not_draft_a_second_proposal(db, policy):
    policy(make_policy())
    deal_id, lead_id = _priced_deal()
    task_a = _enqueue(deal_id, lead_id)
    runner.execute_task(task_a.id, worker_id="w1")

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        # Simulate a redelivered deal.ready_for_proposal while the first draft is still
        # pending approval, by forcing the deal back — the real guard is the
        # idempotency-keyed proposal/message rows, exercised directly here.
        deal.stage = DealStage.PROPOSAL

    task_b = _enqueue(deal_id, lead_id)
    runner.execute_task(task_b.id, worker_id="w1")

    with session_scope() as session:
        proposals = list(session.scalars(select(Proposal).where(Proposal.deal_id == deal_id)))
        assert len(proposals) == 1


def test_missing_conversation_fails_permanently(db, policy):
    policy(make_policy())
    with session_scope() as session:
        company = Company(name="MOCK No Conv", domain="noconv.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id, full_name="MOCK Z", email="z@noconv.invalid", is_mock=True
        )
        session.add(prospect)
        session.flush()
        lead = Lead(prospect_id=prospect.id, company_id=company.id, is_mock=True)
        session.add(lead)
        session.flush()
        deal = Deal(
            lead_id=lead.id, stage=DealStage.PROPOSAL, value_usd=Decimal("1000"), is_mock=True
        )
        session.add(deal)
        session.flush()
        deal_id, lead_id_val = deal.id, lead.id

    task = _enqueue(deal_id, lead_id_val)
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED


def test_unknown_deal_fails_permanently(db, policy):
    policy(make_policy())
    task = _enqueue(uuid.uuid4(), uuid.uuid4())
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED
