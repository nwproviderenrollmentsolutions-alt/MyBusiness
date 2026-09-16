from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from agents.outreach.agent import AGENT
from core import approval_gate, flags, suppression, task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    ApprovalStatus,
    ConversationStatus,
    LeadStatus,
    RunStatus,
    SuppressionScope,
    TaskStatus,
)
from db.models.market import Company, Prospect
from db.models.pipeline import Conversation, Lead, Message
from db.models.runtime import AgentTask, Approval, OutboxEvent
from db.session import session_scope
from providers.factory import get_email_provider
from tests.conftest import make_policy
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


@pytest.fixture
def scored_lead() -> uuid.UUID:
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
            status=LeadStatus.SCORED,
            current_score=90,
            is_mock=True,
        )
        session.add(lead)
        session.flush()
        return lead.id


def _enqueue(lead_id: uuid.UUID, *, dry_run: bool = True) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(
            session,
            agent="outreach",
            task_input={"lead_id": str(lead_id), "score": 90},
            dry_run=dry_run,
        )


def test_draft_requests_approval(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.NEEDS_APPROVAL
    with session_scope() as session:
        lead = session.get(Lead, scored_lead)
        assert lead.status is LeadStatus.CONTACTED
        assert lead.touch_count == 1

        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.PENDING_APPROVAL
        assert "MOCK" in message.body

        approval = session.scalar(select(Approval))
        assert approval.status is ApprovalStatus.PENDING
        assert approval.subject_type == "message"
        assert approval.subject_id == message.id
        assert approval.policy_reason == "message does not use an approved template"


def test_approval_then_dry_run_simulates_the_send(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead, dry_run=True)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.SUCCEEDED

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.COMPLETED
        assert message.provider_message_id == f"simulated-{message.idempotency_key[:12]}"
        # Dry run: nothing actually left the building.
        assert get_email_provider().sent == []

        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "outreach.sent"))
        assert event is not None
        assert event.payload["lead_id"] == str(scored_lead)


def test_approval_then_live_run_actually_sends(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.COMPLETED
        conversation = session.get(Conversation, message.conversation_id)
        assert conversation.status is ConversationStatus.AWAITING_REPLY
        assert conversation.last_outbound_at is not None

    sent = get_email_provider().sent
    assert len(sent) == 1
    assert sent[0]["to"] == "sam@acme-hvac.invalid"


def test_ceo_edit_is_what_actually_sends(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(
            session,
            approval.id,
            decided_by="ceo",
            edited_payload={
                **approval.payload,
                "subject": "CEO rewrote the subject",
                "body": "CEO rewrote the body entirely.",
            },
        )

    runner.execute_task(task.id, worker_id="w1")

    sent = get_email_provider().sent
    assert sent[0]["subject"] == "CEO rewrote the subject"
    assert sent[0]["body"] == "CEO rewrote the body entirely."


def test_rejection_marks_the_message_rejected_and_stops_the_task(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.reject(session, approval.id, decided_by="ceo", notes="wrong angle")

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.REJECTED
        task_row = session.get(AgentTask, task.id)
        assert task_row.status is TaskStatus.CANCELLED
        # The lead was genuinely attempted — it stays contacted even though this
        # particular draft was rejected, rather than reverting to scored.
        lead = session.get(Lead, scored_lead)
        assert lead.status is LeadStatus.CONTACTED

    assert get_email_provider().sent == []


def test_suppressed_recipient_is_never_drafted_into_an_approval(db, policy, scored_lead):
    policy(make_policy())
    with session_scope() as session:
        suppression.add_suppression(
            session, scope=SuppressionScope.EMAIL, value="sam@acme-hvac.invalid", reason="opted out"
        )

    task = _enqueue(scored_lead)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        lead = session.get(Lead, scored_lead)
        assert lead.status is LeadStatus.DO_NOT_CONTACT
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.SUPPRESSED
        assert session.scalar(select(Approval)) is None


def test_suppression_added_after_approval_blocks_the_send_at_execution_time(
    db, policy, scored_lead
):
    """Approval satisfies 'should this happen'; it never satisfies 'is the recipient
    still contactable right now'."""
    policy(make_policy())
    task = _enqueue(scored_lead, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")
        suppression.add_suppression(
            session,
            scope=SuppressionScope.EMAIL,
            value="sam@acme-hvac.invalid",
            reason="complained after approval, before send",
        )

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.SUPPRESSED
    assert get_email_provider().sent == []


def test_emergency_stop_after_approval_blocks_the_send(db, policy, scored_lead):
    policy(make_policy())
    task = _enqueue(scored_lead, dry_run=False)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        approval = session.scalar(select(Approval))
        approval_gate.approve(session, approval.id, decided_by="ceo")
        flags.engage_emergency_stop(session, engaged_by="ceo", reason="pause")

    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == scored_lead))
        assert message.state is ActionState.SUPPRESSED
    assert get_email_provider().sent == []


def test_already_contacted_lead_is_a_no_op(db, policy):
    policy(make_policy())
    with session_scope() as session:
        company = Company(name="MOCK X", domain="x.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id, full_name="MOCK Y", email="y@x.invalid", is_mock=True
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
        lead_id = lead.id

    task = _enqueue(lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(Message).where(Message.lead_id == lead_id)) is None


def test_redelivered_event_does_not_draft_a_second_message(db, policy, scored_lead):
    policy(make_policy())
    task_a = _enqueue(scored_lead)
    runner.execute_task(task_a.id, worker_id="w1")

    with session_scope() as session:
        lead = session.get(Lead, scored_lead)
        # Simulate a redelivered lead.scored while the first draft is still pending
        # approval, by forcing the lead back to scored (the real guard is the
        # idempotency-keyed message row, exercised directly here).
        lead.status = LeadStatus.SCORED

    task_b = _enqueue(scored_lead)
    runner.execute_task(task_b.id, worker_id="w1")

    with session_scope() as session:
        messages = list(session.scalars(select(Message).where(Message.lead_id == scored_lead)))
        assert len(messages) == 1


def test_unknown_lead_fails_permanently(db, policy):
    policy(make_policy())
    task = _enqueue(uuid.uuid4())
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED
