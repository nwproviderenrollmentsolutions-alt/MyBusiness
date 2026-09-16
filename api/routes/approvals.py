"""The CEO's approval queue.

Approving or rejecting only records the decision and, for an approval, resumes the
originating task by moving it back to `pending` — it does not execute anything inline
(see api/routes/commands.py's docstring: the worker is the only component that executes
agents). The actual send/creation happens once a running worker picks the resumed task
back up.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import ApprovalApprove, ApprovalOut, ApprovalReject
from core import approval_gate
from core.errors import ApprovalStateError
from db.models.runtime import Approval

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_pending_approvals(limit: int = 50, session: Session = Depends(get_db)) -> list[Approval]:
    return approval_gate.pending(session, limit=limit)


def _require_exists(session: Session, approval_id: uuid.UUID) -> None:
    if session.get(Approval, approval_id) is None:
        raise HTTPException(status_code=404, detail=f"approval {approval_id} not found")


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
def approve(
    approval_id: uuid.UUID, body: ApprovalApprove, session: Session = Depends(get_db)
) -> Approval:
    _require_exists(session, approval_id)
    try:
        return approval_gate.approve(
            session,
            approval_id,
            decided_by=body.decided_by,
            notes=body.notes,
            edited_payload=body.edited_payload,
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    approval_id: uuid.UUID, body: ApprovalReject, session: Session = Depends(get_db)
) -> Approval:
    _require_exists(session, approval_id)
    try:
        return approval_gate.reject(
            session, approval_id, decided_by=body.decided_by, notes=body.notes
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
