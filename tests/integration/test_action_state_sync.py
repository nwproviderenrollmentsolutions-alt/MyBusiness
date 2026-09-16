from __future__ import annotations

import uuid

import pytest

from core import action_state_sync
from db.enums import ActionState, Channel, MessageDirection
from db.models.market import Company, Prospect
from db.models.pipeline import Conversation, Lead, Message

pytestmark = pytest.mark.integration


@pytest.fixture
def draft_message(db) -> Message:
    company = Company(name="MOCK Acme", domain="acme.invalid", is_mock=True)
    db.add(company)
    db.flush()
    prospect = Prospect(
        company_id=company.id, full_name="MOCK Sam", email="sam@acme.invalid", is_mock=True
    )
    db.add(prospect)
    db.flush()
    lead = Lead(prospect_id=prospect.id, company_id=company.id, is_mock=True)
    db.add(lead)
    db.flush()
    conversation = Conversation(lead_id=lead.id)
    db.add(conversation)
    db.flush()
    message = Message(
        conversation_id=conversation.id,
        lead_id=lead.id,
        direction=MessageDirection.OUTBOUND,
        channel=Channel.EMAIL,
        state=ActionState.PENDING_APPROVAL,
    )
    db.add(message)
    db.flush()
    return message


def test_approval_advances_a_pending_message_to_approved(db, draft_message):
    action_state_sync.sync_from_approval(
        db,
        subject_type="message",
        subject_id=draft_message.id,
        target=ActionState.APPROVED,
        decided_by="ceo",
    )
    assert draft_message.state is ActionState.APPROVED


def test_rejection_advances_a_pending_message_to_rejected(db, draft_message):
    action_state_sync.sync_from_approval(
        db,
        subject_type="message",
        subject_id=draft_message.id,
        target=ActionState.REJECTED,
        decided_by="ceo",
    )
    assert draft_message.state is ActionState.REJECTED


def test_missing_subject_is_a_silent_no_op(db):
    # Should not raise — many approvals (Chief of Staff's kill-switch ones) gate nothing.
    action_state_sync.sync_from_approval(
        db, subject_type=None, subject_id=None, target=ActionState.APPROVED, decided_by="ceo"
    )


def test_unknown_subject_type_is_a_silent_no_op(db, draft_message):
    action_state_sync.sync_from_approval(
        db,
        subject_type="something_not_registered",
        subject_id=draft_message.id,
        target=ActionState.APPROVED,
        decided_by="ceo",
    )
    assert draft_message.state is ActionState.PENDING_APPROVAL


def test_illegal_transition_is_a_silent_no_op_not_a_crash(db, draft_message):
    """A message already completed (say, manually) must not be knocked back to approved
    by a stale sync call."""
    draft_message.state = ActionState.COMPLETED
    db.flush()

    action_state_sync.sync_from_approval(
        db,
        subject_type="message",
        subject_id=draft_message.id,
        target=ActionState.APPROVED,
        decided_by="ceo",
    )
    assert draft_message.state is ActionState.COMPLETED


def test_nonexistent_row_is_a_silent_no_op(db):
    action_state_sync.sync_from_approval(
        db,
        subject_type="message",
        subject_id=uuid.uuid4(),
        target=ActionState.APPROVED,
        decided_by="ceo",
    )
