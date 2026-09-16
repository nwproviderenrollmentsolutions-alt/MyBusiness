from __future__ import annotations

import pytest

from core.errors import InvalidStateTransition
from core.states import (
    assert_action_transition,
    assert_lead_transition,
    can_transition_action,
    can_transition_lead,
)
from db.enums import ACTION_STATE_TRANSITIONS, ActionState, LeadStatus


def test_draft_cannot_skip_to_completed():
    """The path from draft to sent must pass through approval or explicit authorization."""
    assert not can_transition_action(ActionState.DRAFT, ActionState.COMPLETED)
    assert not can_transition_action(ActionState.DRAFT, ActionState.EXECUTING)
    with pytest.raises(InvalidStateTransition):
        assert_action_transition(ActionState.DRAFT, ActionState.COMPLETED)


def test_happy_path_through_approval():
    for current, target in [
        (ActionState.DRAFT, ActionState.PENDING_APPROVAL),
        (ActionState.PENDING_APPROVAL, ActionState.APPROVED),
        (ActionState.APPROVED, ActionState.EXECUTING),
        (ActionState.EXECUTING, ActionState.COMPLETED),
    ]:
        assert_action_transition(current, target)


def test_rejected_and_completed_are_terminal():
    for terminal in (ActionState.COMPLETED, ActionState.REJECTED, ActionState.SUPPRESSED):
        assert ACTION_STATE_TRANSITIONS[terminal] == frozenset()


def test_failed_send_may_be_retried():
    assert can_transition_action(ActionState.FAILED, ActionState.EXECUTING)


def test_suppression_reachable_before_sending_but_not_after():
    assert can_transition_action(ActionState.APPROVED, ActionState.SUPPRESSED)
    assert not can_transition_action(ActionState.COMPLETED, ActionState.SUPPRESSED)


def test_lead_must_progress_in_order():
    assert can_transition_lead(LeadStatus.DISCOVERED, LeadStatus.ENRICHED)
    assert not can_transition_lead(LeadStatus.DISCOVERED, LeadStatus.QUALIFIED)
    with pytest.raises(InvalidStateTransition):
        assert_lead_transition(LeadStatus.DISCOVERED, LeadStatus.CONTACTED)


@pytest.mark.parametrize("origin", list(LeadStatus))
def test_opting_out_always_wins(origin):
    """Someone can ask not to be contacted at any point, including after qualifying."""
    assert can_transition_lead(origin, LeadStatus.DO_NOT_CONTACT)
    assert can_transition_lead(origin, LeadStatus.DISQUALIFIED)
