from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from core import approval_gate, task_queue
from core.errors import ApprovalStateError
from db.enums import ActionType, ApprovalStatus, RiskLevel, TaskStatus
from db.models.runtime import Approval, AuditLog, OutboxEvent
from tests.conftest import make_policy

pytestmark = pytest.mark.integration


@pytest.fixture
def parked(db, policy):
    policy(make_policy())
    task = task_queue.enqueue(db, agent="outreach", action_type="outreach.send_email")
    task_queue.mark_running(db, task)
    approval = approval_gate.request(
        db,
        action_type=ActionType.OUTREACH_SEND_EMAIL,
        agent="outreach",
        summary="Send intro email to MOCK Sam Rivera",
        payload={"to": "sam@acme.invalid", "body": "MOCK draft"},
        risk=RiskLevel.MEDIUM,
        task=task,
        policy_reason="autonomy_off",
    )
    db.commit()
    return task, approval


def test_requesting_approval_parks_the_task(db, parked):
    task, approval = parked
    assert task.status is TaskStatus.AWAITING_APPROVAL
    assert approval.status is ApprovalStatus.PENDING
    assert approval.expires_at is not None


def test_parked_task_is_not_leased_by_a_worker(db, parked):
    """A task waiting on a human must not consume worker capacity."""
    assert task_queue.lease(db, worker_id="w1", limit=5) == []


def test_approval_resumes_the_task(db, parked):
    task, approval = parked
    approval_gate.approve(db, approval.id, decided_by="ceo", notes="looks good")
    db.commit()

    assert approval.status is ApprovalStatus.APPROVED
    assert approval.decided_by == "ceo"
    assert task.status is TaskStatus.PENDING
    assert len(task_queue.lease(db, worker_id="w1", limit=5)) == 1


def test_rejection_cancels_the_task_rather_than_retrying_it(db, parked):
    task, approval = parked
    approval_gate.reject(db, approval.id, decided_by="ceo", notes="wrong angle")
    db.commit()

    assert approval.status is ApprovalStatus.REJECTED
    assert task.status is TaskStatus.CANCELLED
    assert task_queue.lease(db, worker_id="w1", limit=5) == []


def test_ceo_edits_are_what_execute(db, parked):
    _, approval = parked
    approval_gate.approve(
        db,
        approval.id,
        decided_by="ceo",
        edited_payload={"to": "sam@acme.invalid", "body": "CEO rewrote this"},
    )
    db.commit()

    assert approval.effective_payload["body"] == "CEO rewrote this"
    # The agent's original proposal is retained alongside the human's judgment.
    assert approval.payload["body"] == "MOCK draft"


def test_edits_are_audited_separately(db, parked):
    _, approval = parked
    approval_gate.approve(db, approval.id, decided_by="ceo", edited_payload={"body": "changed"})
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "approval.edited"))
    assert entry.before["body"] == "MOCK draft"
    assert entry.after["body"] == "changed"


def test_an_approval_cannot_be_decided_twice(db, parked):
    _, approval = parked
    approval_gate.approve(db, approval.id, decided_by="ceo")
    db.commit()

    with pytest.raises(ApprovalStateError, match="cannot be decided twice"):
        approval_gate.reject(db, approval.id, decided_by="someone_else")


def test_expiry_never_approves(db, parked):
    """An unanswered request means the action does not happen."""
    task, approval = parked
    approval.expires_at = task_queue.db_now(db) - timedelta(minutes=1)
    db.flush()

    expired = approval_gate.expire_due(db)
    db.commit()

    assert [a.id for a in expired] == [approval.id]
    assert approval.status is ApprovalStatus.EXPIRED
    assert task.status is TaskStatus.CANCELLED


def test_pending_queue_is_ordered_by_risk(db, policy):
    policy(make_policy())
    for risk in (RiskLevel.LOW, RiskLevel.CRITICAL, RiskLevel.MEDIUM, RiskLevel.HIGH):
        approval_gate.request(
            db,
            action_type=ActionType.OUTREACH_SEND_EMAIL,
            agent="outreach",
            summary=f"{risk} item",
            risk=risk,
        )
    db.commit()

    assert [a.risk for a in approval_gate.pending(db)] == [
        RiskLevel.CRITICAL,
        RiskLevel.HIGH,
        RiskLevel.MEDIUM,
        RiskLevel.LOW,
    ]


def test_decisions_emit_events_for_other_agents_to_react_to(db, parked):
    _, approval = parked
    approval_gate.approve(db, approval.id, decided_by="ceo")
    db.commit()

    types = {e.event_type for e in db.scalars(select(OutboxEvent))}
    assert {"approval.requested", "approval.approved"} <= types


def test_approved_payload_is_available_to_the_resumed_run(db, parked):
    task, approval = parked
    approval_gate.approve(db, approval.id, decided_by="ceo", edited_payload={"body": "final"})
    db.commit()

    found = approval_gate.approved_for_task(db, task.id)
    assert found is not None
    assert found.effective_payload["body"] == "final"


def test_approval_records_which_policy_rule_required_it(db, parked):
    _, approval = parked
    assert approval.policy_reason == "autonomy_off"
    stored = db.scalar(select(Approval).where(Approval.id == approval.id))
    assert stored.requested_by_agent == "outreach"
