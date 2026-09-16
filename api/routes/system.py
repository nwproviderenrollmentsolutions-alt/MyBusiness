"""The panic button. Flips the emergency-stop flag directly — no command queue, no
interpreter, no chief_of_staff involved, the same way `ceo stop`/`ceo go` bypass them on
the CLI. The one thing that must not depend on anything else working.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import SystemFlagRequest
from core import flags

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/stop")
def engage_emergency_stop(
    body: SystemFlagRequest, session: Session = Depends(get_db)
) -> dict[str, bool]:
    flags.engage_emergency_stop(
        session, engaged_by=body.actor, reason=body.reason or "API emergency stop"
    )
    return {"emergency_stop": True}


@router.post("/go")
def release_emergency_stop(
    body: SystemFlagRequest, session: Session = Depends(get_db)
) -> dict[str, bool]:
    flags.release_emergency_stop(
        session, released_by=body.actor, reason=body.reason or "API release"
    )
    return {"emergency_stop": False}
