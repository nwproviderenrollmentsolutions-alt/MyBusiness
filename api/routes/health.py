from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import HealthOut
from config.settings import get_settings
from core import flags

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(session: Session = Depends(get_db)) -> HealthOut:
    settings = get_settings()
    try:
        session.execute(text("SELECT 1"))
        database_ok = True
    except Exception:
        database_ok = False

    return HealthOut(
        status="ok" if database_ok else "degraded",
        database=database_ok,
        emergency_stop=flags.is_emergency_stopped(session) if database_ok else False,
        dry_run=settings.dry_run,
        environment=settings.environment,
    )
