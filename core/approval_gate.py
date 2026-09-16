"""Human approval gate.

The CEO's queue. An agent that needs a decision parks its task here rather than acting,
and the task only resumes once a human decides. Approvals record both what the agent
proposed and what the human changed it to.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from config.settings import get_policy_config
from core import action_state_sync, audit, event_bus, task_queue
from core.errors import ApprovalStateError
from core.task_queue import db_now
from db.enums import ActionState, ActionType, ApprovalStatus, RiskLevel
from db.models.runtime import AgentTask, Approval

_AUDIT_FIELDS = ("status", "decided_by", "decided_at")


def request(
    session: Session,
    *,
    action_type: ActionType,
    agent: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    risk: RiskLevel = RiskLevel.MEDIUM,
    subject_type: str | None = None,
    subject_id: uuid.UUID | None = None,
    task: AgentTask | None = None,
    policy_reason: str | None = None,
    expires_in_hours: int | None = None,
    correlation_id: uuid.UUID | None = None,
) -> Approval:
    """Open an approval and park the originating task."""
    policy = get_policy_config()
    hours = (
        expires_in_hours
        if expires_in_hours is not None
        else policy.approval.default_expiry_hours
    )

    approval = Approval(
        action_type=str(action_type),
        status=ApprovalStatus.PENDING,
        risk=risk,
        summary=summary,
        payload=payload or {},
        policy_reason=policy_reason,
        subject_type=subject_type,
        subject_id=subject_id,
        requested_by_agent=agent,
        task_id=task.id if task else None,
        correlation_id=correlation_id or (task.correlation_id if task else None),
        expires_at=db_now(session) + timedelta(hours=hours),
    )
    session.add(approval)
    session.flush()

    audit.record_agent(
        session,
        agent=agent,
        action="approval.requested",
        subject_type="approval",
        subject_id=approval.id,
        after={"action_type": str(action_type), "risk": str(risk), "summary": summary},
        reason=policy_reason,
        task_id=task.id if task else None,
        correlation_id=approval.correlation_id,
    )
    event_bus.emit(
        session,
        event_type="approval.requested",
        subject_type="approval",
        subject_id=approval.id,
        payload={"action_type": str(action_type), "risk": str(risk), "summary": summary},
        emitted_by=agent,
        task_id=task.id if task else None,
        correlation_id=approval.correlation_id,
        dry_run=task.dry_run if task else True,
    )

    if task is not None:
        task_queue.pause_for_approval(session, task, approval_id=approval.id)
    return approval


def approve(
    session: Session,
    approval_id: uuid.UUID,
    *,
    decided_by: str,
    notes: str | None = None,
    edited_payload: dict[str, Any] | None = None,
) -> Approval:
    """Approve, optionally with edits. The edited version is what executes."""
    approval = _load_pending(session, approval_id)
    before = audit.snapshot(approval, _AUDIT_FIELDS)

    approval.status = ApprovalStatus.APPROVED
    approval.decided_by = decided_by
    approval.decided_at = db_now(session)
    approval.decision_notes = notes
    if edited_payload is not None:
        approval.edited_payload = edited_payload
    session.flush()

    audit.record_human(
        session,
        who=decided_by,
        action="approval.approved",
        subject_type="approval",
        subject_id=approval.id,
        before=before,
        after=audit.snapshot(approval, _AUDIT_FIELDS),
        reason=notes,
        task_id=approval.task_id,
        correlation_id=approval.correlation_id,
    )
    if edited_payload is not None:
        audit.record_human(
            session,
            who=decided_by,
            action="approval.edited",
            subject_type="approval",
            subject_id=approval.id,
            before=approval.payload,
            after=edited_payload,
            reason="CEO edited the proposed action before approving",
            task_id=approval.task_id,
            correlation_id=approval.correlation_id,
        )

    event_bus.emit(
        session,
        event_type="approval.approved",
        subject_type="approval",
        subject_id=approval.id,
        payload={
            "approval_id": str(approval.id),
            "action_type": approval.action_type,
            "decided_by": decided_by,
            "subject_type": approval.subject_type,
            "subject_id": str(approval.subject_id) if approval.subject_id else None,
        },
        emitted_by="approval_gate",
        task_id=approval.task_id,
        correlation_id=approval.correlation_id,
        dry_run=_dry_run_of(session, approval),
    )
    action_state_sync.sync_from_approval(
        session,
        subject_type=approval.subject_type,
        subject_id=approval.subject_id,
        target=ActionState.APPROVED,
        decided_by=decided_by,
    )
    _resume_task(session, approval, reason=f"approved by {decided_by}")
    return approval


def reject(
    session: Session,
    approval_id: uuid.UUID,
    *,
    decided_by: str,
    notes: str | None = None,
) -> Approval:
    """Reject. The originating task is cancelled rather than retried."""
    approval = _load_pending(session, approval_id)
    before = audit.snapshot(approval, _AUDIT_FIELDS)

    approval.status = ApprovalStatus.REJECTED
    approval.decided_by = decided_by
    approval.decided_at = db_now(session)
    approval.decision_notes = notes
    session.flush()

    audit.record_human(
        session,
        who=decided_by,
        action="approval.rejected",
        subject_type="approval",
        subject_id=approval.id,
        before=before,
        after=audit.snapshot(approval, _AUDIT_FIELDS),
        reason=notes,
        task_id=approval.task_id,
        correlation_id=approval.correlation_id,
    )
    event_bus.emit(
        session,
        event_type="approval.rejected",
        subject_type="approval",
        subject_id=approval.id,
        payload={
            "approval_id": str(approval.id),
            "action_type": approval.action_type,
            "decided_by": decided_by,
            "notes": notes,
            "subject_type": approval.subject_type,
            "subject_id": str(approval.subject_id) if approval.subject_id else None,
        },
        emitted_by="approval_gate",
        task_id=approval.task_id,
        correlation_id=approval.correlation_id,
        dry_run=_dry_run_of(session, approval),
    )
    action_state_sync.sync_from_approval(
        session,
        subject_type=approval.subject_type,
        subject_id=approval.subject_id,
        target=ActionState.REJECTED,
        decided_by=decided_by,
    )

    task = _task_of(session, approval)
    if task is not None:
        task_queue.cancel(session, task, reason=f"approval rejected by {decided_by}")
    return approval


def expire_due(session: Session, *, limit: int = 100) -> list[Approval]:
    """Expire approvals nobody answered in time.

    Expiry never approves. An unanswered request means the action does not happen.
    """
    now = db_now(session)
    due = list(
        session.scalars(
            select(Approval)
            .where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at.is_not(None),
                Approval.expires_at < now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for approval in due:
        before = audit.snapshot(approval, _AUDIT_FIELDS)
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        session.flush()
        audit.record_system(
            session,
            action="approval.expired",
            subject_type="approval",
            subject_id=approval.id,
            before=before,
            after=audit.snapshot(approval, _AUDIT_FIELDS),
            reason="no decision before expiry; action not taken",
            task_id=approval.task_id,
            correlation_id=approval.correlation_id,
        )
        event_bus.emit(
            session,
            event_type="approval.expired",
            subject_type="approval",
            subject_id=approval.id,
            payload={
                "approval_id": str(approval.id),
                "action_type": approval.action_type,
                "subject_type": approval.subject_type,
                "subject_id": str(approval.subject_id) if approval.subject_id else None,
            },
            emitted_by="approval_gate",
            task_id=approval.task_id,
            correlation_id=approval.correlation_id,
            dry_run=_dry_run_of(session, approval),
        )
        # Expiry never approves — the action does not happen, same as a rejection.
        action_state_sync.sync_from_approval(
            session,
            subject_type=approval.subject_type,
            subject_id=approval.subject_id,
            target=ActionState.REJECTED,
            decided_by="system (expired)",
        )
        task = _task_of(session, approval)
        if task is not None:
            task_queue.cancel(session, task, reason="approval expired")
    return due


def pending(session: Session, *, limit: int = 100) -> list[Approval]:
    """The CEO's queue, riskiest first, then oldest first."""
    risk_order = case(
        {
            RiskLevel.CRITICAL: 0,
            RiskLevel.HIGH: 1,
            RiskLevel.MEDIUM: 2,
            RiskLevel.LOW: 3,
        },
        value=Approval.risk,
        else_=4,
    )
    return list(
        session.scalars(
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(risk_order.asc(), Approval.created_at.asc())
            .limit(limit)
        )
    )


def _load_pending(session: Session, approval_id: uuid.UUID) -> Approval:
    approval = session.get(Approval, approval_id, with_for_update=True)
    if approval is None:
        raise ApprovalStateError(f"approval {approval_id} does not exist")
    if approval.status is not ApprovalStatus.PENDING:
        raise ApprovalStateError(
            f"approval {approval_id} is already {approval.status}; it cannot be decided twice"
        )
    return approval


def _task_of(session: Session, approval: Approval) -> AgentTask | None:
    return session.get(AgentTask, approval.task_id) if approval.task_id else None


def _dry_run_of(session: Session, approval: Approval) -> bool:
    """The originating task's dry_run flag, or the safe default when there is none."""
    task = _task_of(session, approval)
    return task.dry_run if task is not None else True


def _resume_task(session: Session, approval: Approval, *, reason: str) -> None:
    task = _task_of(session, approval)
    if task is not None:
        task_queue.resume(session, task, reason=reason)


def approved_for_task(session: Session, task_id: uuid.UUID) -> Approval | None:
    """The approval that unblocked this task, if any. Carries the CEO's edits."""
    return session.scalar(
        select(Approval)
        .where(Approval.task_id == task_id, Approval.status == ApprovalStatus.APPROVED)
        .order_by(Approval.decided_at.desc())
    )
