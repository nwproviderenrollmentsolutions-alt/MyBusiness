"""Dashboard metrics. Read-only — answers straight from current database state, exactly
what "show status" / "show decisions" answer for the CEO command line, so the API and the
CLI never tell a different story about the same data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from agents.chief_of_staff import status as status_reporting
from api.deps import get_db
from api.schemas import DecisionsOut, StatusOut

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusOut)
def get_status(session: Session = Depends(get_db)) -> StatusOut:
    _, result = status_reporting.status_summary(session)
    return StatusOut.model_validate(result)


@router.get("/decisions", response_model=DecisionsOut)
def get_decisions(session: Session = Depends(get_db)) -> DecisionsOut:
    _, result = status_reporting.decisions_summary(session)
    return DecisionsOut.model_validate(result)
