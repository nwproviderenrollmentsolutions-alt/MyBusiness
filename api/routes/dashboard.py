"""The server-rendered CEO dashboard: one page, real data, plain HTML forms.

Form submissions here call the same core functions the JSON routes call
(approval_gate, flags, task_queue) directly, rather than the JSON endpoints — an HTML
form posts `application/x-www-form-urlencoded`, not JSON, so it needs its own thin
Form()-based handlers, but the business logic underneath is the single copy every
surface (CLI, JSON API, dashboard) shares. Every action redirects back to `/`
(POST-redirect-GET) so reloading the page never resubmits a decision.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.chief_of_staff import status as status_reporting
from api.deps import get_db
from core import approval_gate, flags, task_queue
from db.models.command import CeoCommand

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request, session: Session = Depends(get_db)) -> HTMLResponse:
    _, status_result = status_reporting.status_summary(session)
    pending_approvals = approval_gate.pending(session, limit=50)
    recent_commands = session.scalars(
        select(CeoCommand).order_by(CeoCommand.created_at.desc()).limit(15)
    ).all()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "status": status_result,
            "approvals": pending_approvals,
            "commands": recent_commands,
            "emergency_stopped": flags.is_emergency_stopped(session),
        },
    )


@router.post("/dashboard/commands")
def submit_command(
    text: str = Form(...), actor: str = Form("ceo"), session: Session = Depends(get_db)
) -> RedirectResponse:
    command = CeoCommand(raw_text=text, created_by=actor)
    session.add(command)
    session.flush()
    task_queue.enqueue(
        session,
        agent="chief_of_staff",
        task_input={"command_id": str(command.id)},
        dedupe_key=f"ceo_command:{command.id}",
    )
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/approvals/{approval_id}/approve")
def approve_from_dashboard(
    approval_id: uuid.UUID,
    actor: str = Form("ceo"),
    notes: str = Form(""),
    session: Session = Depends(get_db),
) -> RedirectResponse:
    approval_gate.approve(session, approval_id, decided_by=actor, notes=notes or None)
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/approvals/{approval_id}/reject")
def reject_from_dashboard(
    approval_id: uuid.UUID,
    actor: str = Form("ceo"),
    notes: str = Form(""),
    session: Session = Depends(get_db),
) -> RedirectResponse:
    approval_gate.reject(session, approval_id, decided_by=actor, notes=notes or None)
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/system/stop")
def stop_from_dashboard(
    actor: str = Form("ceo"), session: Session = Depends(get_db)
) -> RedirectResponse:
    flags.engage_emergency_stop(session, engaged_by=actor, reason="dashboard emergency stop")
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/system/go")
def go_from_dashboard(
    actor: str = Form("ceo"), session: Session = Depends(get_db)
) -> RedirectResponse:
    flags.release_emergency_stop(session, released_by=actor, reason="dashboard release")
    return RedirectResponse(url="/", status_code=303)
