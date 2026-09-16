"""State transition guards.

Illegal transitions are a bug, not a business outcome, so they raise rather than being
silently coerced. A message that jumps from draft straight to completed would mean the
approval gate was bypassed, which must never fail quietly.
"""

from __future__ import annotations

from core.errors import InvalidStateTransition
from db.enums import (
    ACTION_STATE_TRANSITIONS,
    ALWAYS_REACHABLE_LEAD_STATUSES,
    LEAD_STATUS_TRANSITIONS,
    ActionState,
    LeadStatus,
)


def can_transition_action(current: ActionState, target: ActionState) -> bool:
    return target in ACTION_STATE_TRANSITIONS[current]


def assert_action_transition(current: ActionState, target: ActionState) -> None:
    if not can_transition_action(current, target):
        raise InvalidStateTransition(f"action state {current} -> {target} is not allowed")


def can_transition_lead(current: LeadStatus, target: LeadStatus) -> bool:
    # Opting out or being disqualified always wins, from any point in the funnel.
    if target in ALWAYS_REACHABLE_LEAD_STATUSES:
        return True
    return target in LEAD_STATUS_TRANSITIONS[current]


def assert_lead_transition(current: LeadStatus, target: LeadStatus) -> None:
    if not can_transition_lead(current, target):
        raise InvalidStateTransition(f"lead status {current} -> {target} is not allowed")
