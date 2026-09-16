"""End to end past the approval boundary: a CEO decision resumes the pipeline through
Outreach's send and into Conversation Management picking up a reply — all through the
real task queue and event bus, wired the way config/agents.yaml wires it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from core import approval_gate, task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    ApprovalStatus,
    CommandStatus,
    ConversationStatus,
    LeadStatus,
    MessageDirection,
)
from db.models.command import CeoCommand
from db.models.pipeline import Conversation, Lead, Message
from db.models.runtime import Approval, OutboxEvent
from db.session import session_scope
from providers.factory import get_email_provider
from worker import runner

pytestmark = pytest.mark.integration


def _drain_queue(*, max_passes: int = 50) -> int:
    total = 0
    for _ in range(max_passes):
        executed = runner.run_once(worker_id="chain-test", batch_size=50)
        total += executed
        if executed == 0:
            return total
    pytest.fail("queue did not drain within max_passes — a task is stuck or looping")


def _submit_command(text: str, *, dry_run: bool = False) -> uuid.UUID:
    with session_scope() as session:
        command = CeoCommand(raw_text=text, created_by="ceo")
        session.add(command)
        session.flush()
        task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
            dry_run=dry_run,
        )
        return command.id


def test_approve_drives_the_send_and_a_reply_engages_the_lead(db):
    AGENTS.load_from_config()

    command_id = _submit_command("find 3 qualified plumbing leads")
    _drain_queue()

    with session_scope() as session:
        contacted = list(session.scalars(select(Lead).where(Lead.status == LeadStatus.CONTACTED)))
        assert contacted, "at least one lead should have qualified and been drafted"
        lead_id = contacted[0].id
        message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        approval = session.scalar(
            select(Approval).where(
                Approval.subject_type == "message", Approval.subject_id == message.id
            )
        )
        assert approval is not None
        assert approval.status is ApprovalStatus.PENDING
        approval_id = approval.id

    with session_scope() as session:
        approval_gate.approve(session, approval_id, decided_by="ceo@example.com")

    _drain_queue()

    with session_scope() as session:
        message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        assert message.state is ActionState.COMPLETED
        provider_message_id = message.provider_message_id

    sent = get_email_provider().sent
    assert len(sent) == 1

    get_email_provider().seed_reply(
        from_email=sent[0]["to"],
        body="MOCK: yes, I'm interested, let's talk",
        in_reply_to=provider_message_id,
    )

    reply_command_id = _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
        reply_command = session.get(CeoCommand, reply_command_id)
        assert reply_command.status is CommandStatus.DISPATCHED

        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.ENGAGED

        conversation = session.scalar(select(Conversation).where(Conversation.lead_id == lead_id))
        assert conversation.status is ConversationStatus.ENGAGED

        inbound = session.scalar(
            select(Message).where(
                Message.lead_id == lead_id, Message.direction == MessageDirection.INBOUND
            )
        )
        assert inbound is not None
        assert "interested" in inbound.body

        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "conversation.reply_received")
        )
        assert event is not None
        assert event.payload["lead_id"] == str(lead_id)

    with session_scope() as session:
        assert session.get(CeoCommand, command_id).status is CommandStatus.DISPATCHED


def test_reject_stops_the_send_and_leaves_no_email_sent(db):
    AGENTS.load_from_config()

    _submit_command("find 2 qualified plumbing leads")
    _drain_queue()

    with session_scope() as session:
        contacted = list(session.scalars(select(Lead).where(Lead.status == LeadStatus.CONTACTED)))
        assert contacted
        approval = session.scalar(select(Approval).where(Approval.status == ApprovalStatus.PENDING))
        approval_id = approval.id
        message_id = approval.subject_id

    with session_scope() as session:
        approval_gate.reject(session, approval_id, decided_by="ceo", notes="not this one")

    _drain_queue()

    with session_scope() as session:
        message = session.get(Message, message_id)
        assert message.state is ActionState.REJECTED

    assert get_email_provider().sent == []
