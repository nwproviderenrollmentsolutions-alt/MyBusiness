"""Test fixtures.

Integration tests run against a real PostgreSQL database, not SQLite. The queue depends on
``FOR UPDATE SKIP LOCKED`` and the schema depends on JSONB and partial indexes; testing
those against a different engine would prove nothing about production behavior.
"""

from __future__ import annotations

import os

# Point every component at the test database before anything reads configuration.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://mybusiness:mybusiness_dev@localhost:5432/mybusiness_test",
    ),
)
os.environ.setdefault("DRY_RUN", "true")
# The suite must be hermetic regardless of a developer's local .env — a real provider
# configured there (e.g. LLM_PROVIDER=anthropic for Milestone 8's manual verification)
# must never leak into what the tests exercise. Environment variables outrank .env file
# values in pydantic-settings' resolution order, so setting these here overrides it.
for _provider_kind in (
    "LLM",
    "WEB_RESEARCH",
    "LEAD_DISCOVERY",
    "EMAIL",
    "CRM",
    "CALENDAR",
    "PAYMENT",
):
    os.environ.setdefault(f"{_provider_kind}_PROVIDER", "mock")

from collections.abc import Iterator, Mapping  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from config.settings import (  # noqa: E402
    AgentEntry,
    AgentsConfig,
    AutonomyMode,
    PolicyConfig,
    reload_config,
)
from core.registry import AGENTS  # noqa: E402
from core.tool_registry import TOOLS  # noqa: E402
from core.tools import register_builtin_tools  # noqa: E402
from db.base import Base  # noqa: E402
from db.models import *  # noqa: F401,F403,E402  (registers tables on Base.metadata)
from db.session import get_engine, get_session_factory, reset_engine  # noqa: E402
from providers.factory import reset_providers  # noqa: E402


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    reset_engine()
    eng = get_engine()
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment problem, not a test failure
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {exc}")

    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session per test, with every table emptied afterwards."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.rollback()
        session.close()
        tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def clean_registries() -> Iterator[None]:
    """Agents and providers do not leak between tests."""
    AGENTS.clear()
    reset_providers()
    register_builtin_tools()
    yield
    AGENTS.clear()
    reset_providers()
    reload_config()


@pytest.fixture
def tools_registry() -> Iterator[None]:
    """For tests that need to register throwaway tools."""
    saved = dict(TOOLS._tools)
    yield
    TOOLS._tools.clear()
    TOOLS._tools.update(saved)


def make_policy(
    *,
    autonomy: Mapping[Any, AutonomyMode] | None = None,
    **overrides: Any,
) -> PolicyConfig:
    """Build a policy for a test without touching config/policies.yaml."""
    data: dict[str, Any] = {
        "autonomy": {str(k): v for k, v in (autonomy or {}).items()},
        **overrides,
    }
    return PolicyConfig.model_validate(data)


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a policy config for the duration of one test."""

    def _install(config: PolicyConfig) -> PolicyConfig:
        monkeypatch.setattr("core.policy.get_policy_config", lambda: config)
        monkeypatch.setattr("core.approval_gate.get_policy_config", lambda: config)
        return config

    return _install


@pytest.fixture
def agents_config(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install an agent wiring config (used for event subscriptions)."""

    def _install(subscriptions: dict[str, list[str]]) -> AgentsConfig:
        config = AgentsConfig(
            agents={
                name: AgentEntry(module=f"tests.fixtures.{name}", subscribes_to=events)
                for name, events in subscriptions.items()
            }
        )
        monkeypatch.setattr("core.event_bus.get_agents_config", lambda: config)
        return config

    return _install


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """A TestClient against the real API app, real Postgres underneath (the `db` fixture
    dependency is what gives us the schema and per-test truncation). The `with` block
    matters: it is what runs the app's lifespan (register_builtin_tools +
    AGENTS.load_from_config), same as a real server starting up.
    """
    from api.main import app

    with TestClient(app) as test_client:
        yield test_client
