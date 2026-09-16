"""CEO command intake.

Submitting a command only enqueues it for the Chief of Staff — it does not execute
anything inline. ARCHITECTURE.md is explicit that the worker is the only component that
executes agents; the API and the worker both talk to the same Postgres, but only one of
them runs agent code. A submitted command therefore starts `pending` and resolves once a
running worker (`python -m worker.runner`) picks it up — poll GET /commands/{id} for the
outcome, the same way the CLI's `ceo commands` lets the CEO check back on a
fire-and-forget dispatch.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import CommandCreate, CommandOut
from core import task_queue
from db.models.command import CeoCommand

router = APIRouter(prefix="/commands", tags=["commands"])


@router.post("", response_model=CommandOut, status_code=202)
def create_command(body: CommandCreate, session: Session = Depends(get_db)) -> CommandOut:
    command = CeoCommand(raw_text=body.text, created_by=body.created_by)
    session.add(command)
    session.flush()
    task_queue.enqueue(
        session,
        agent="chief_of_staff",
        task_input={"command_id": str(command.id)},
        dedupe_key=f"ceo_command:{command.id}",
    )
    return CommandOut.model_validate(command)


@router.get("", response_model=list[CommandOut])
def list_commands(limit: int = 20, session: Session = Depends(get_db)) -> list[CeoCommand]:
    rows = session.scalars(
        select(CeoCommand).order_by(CeoCommand.created_at.desc()).limit(limit)
    ).all()
    return list(rows)


@router.get("/{command_id}", response_model=CommandOut)
def get_command(command_id: uuid.UUID, session: Session = Depends(get_db)) -> CeoCommand:
    command = session.get(CeoCommand, command_id)
    if command is None:
        raise HTTPException(status_code=404, detail=f"command {command_id} not found")
    return command
