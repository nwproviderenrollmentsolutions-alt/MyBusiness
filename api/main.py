"""The CEO-facing web surface: a JSON API plus a small server-rendered dashboard page.

This process never executes agents — see api/routes/commands.py's docstring. Run
``python -m worker.runner`` alongside it (same Postgres) for submitted commands and
approved actions to actually happen.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes import approvals, commands, dashboard, health, status, system
from core.observability import configure_logging
from core.registry import AGENTS
from core.tools import register_builtin_tools


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    register_builtin_tools()
    AGENTS.load_from_config()
    yield


app = FastAPI(title="MyBusiness CEO Console", lifespan=lifespan)

app.include_router(health.router)
app.include_router(status.router)
app.include_router(commands.router)
app.include_router(approvals.router)
app.include_router(system.router)
app.include_router(dashboard.router)
