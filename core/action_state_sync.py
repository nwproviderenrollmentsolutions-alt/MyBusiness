"""Bridges an approval decision to the ActionState of whatever it gates.

``approval_gate`` intentionally knows nothing about messages, proposals, or any other
domain concept — it is generic infrastructure for "a human must decide this." But
something still has to move a drafted message to ``approved`` or ``rejected`` when the
CEO decides, or it sits in ``pending_approval`` forever even after the decision is made.

This module is the narrow, structural bridge: every artifact an approval can gate shares
the same shape (a ``state: ActionState`` column), so reacting to the decision needs to
know only that shape, not what a "message" or "proposal" *is*. Message is wired in today;
Proposal (Milestone 5) reuses this for free.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from core import audit
from core.states import can_transition_action
from db.enums import ActionState, ActorType
from db.models.pipeline import Message
from db.models.revenue import Proposal


class _HasActionState(Protocol):
    """What every subject type registered below is required to look like."""

    id: uuid.UUID
    state: ActionState


_SUBJECT_MODELS: dict[str, type[_HasActionState]] = {
    "message": Message,
    "proposal": Proposal,
}


def sync_from_approval(
    session: Session,
    *,
    subject_type: str | None,
    subject_id: uuid.UUID | None,
    target: ActionState,
    decided_by: str,
) -> None:
    """Move the approval's subject to ``target`` if it has one and the move is legal.

    Silently does nothing when there is no subject (many approvals — Chief of Staff's
    kill-switch-adjacent ones, for instance — gate nothing with a state machine), when the
    subject type isn't one of the ones this bridge knows about, or when the subject is
    already past the point where this transition would apply (a second decision on an
    approval that was already synced, or manual intervention). Silence here is
    deliberate: this is best-effort synchronization, not the source of truth for whether
    the decision itself succeeded — ``approval_gate`` already recorded that.
    """
    if subject_type is None or subject_id is None:
        return
    model = _SUBJECT_MODELS.get(subject_type)
    if model is None:
        return

    row = session.get(model, subject_id)
    if row is None or not can_transition_action(row.state, target):
        return

    before = row.state
    row.state = target
    session.flush()
    audit.record(
        session,
        actor_type=ActorType.SYSTEM,
        actor=f"approval_gate (decided by {decided_by})",
        action=f"{subject_type}.state_synced",
        subject_type=subject_type,
        subject_id=row.id,
        before={"state": str(before)},
        after={"state": str(target)},
        reason="synced from an approval decision",
    )
