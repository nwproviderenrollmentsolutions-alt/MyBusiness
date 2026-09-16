"""The migration must produce the schema the models expect.

``create_all`` (used by the other tests for speed) proves the models are self-consistent.
It does not prove the migration works — and the migration is what will run in production.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text

from config.settings import REPO_ROOT, get_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def scratch_database() -> Iterator[str]:
    """A throwaway database, so migrating cannot disturb the test schema."""
    settings = get_settings()
    admin_url = settings.database_url.rsplit("/", 1)[0]
    name = f"mybusiness_migration_{uuid.uuid4().hex[:8]}"

    admin = create_engine(f"{admin_url}/postgres", isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.skip(f"cannot create a scratch database: {exc}")

    yield f"{admin_url}/{name}"

    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        conn.execute(text(f'DROP DATABASE "{name}"'))
    admin.dispose()


def test_migration_builds_the_whole_schema(scratch_database):
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": scratch_database},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            }
            # Every table the runtime depends on.
            assert {
                "agent_tasks",
                "agent_runs",
                "approvals",
                "audit_logs",
                "outbox_events",
                "suppressions",
                "system_flags",
                "leads",
                "messages",
                "deals",
                "proposals",
                "customers",
            } <= tables

            # State columns are constrained at the database level, not just in Python.
            constraint = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'messages'::regclass AND contype = 'c' "
                    "AND conname LIKE '%actionstate%'"
                )
            ).scalar_one()
            assert "pending_approval" in constraint
            assert "suppressed" in constraint

            # Duplicate prevention is a database guarantee, not a convention.
            unique_indexes = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename IN ('messages', 'agent_tasks', 'leads')"
                    )
                )
            }
            assert "ix_messages_idempotency_key_unique" in unique_indexes
            assert "ix_agent_tasks_dedupe_key_unique" in unique_indexes
    finally:
        engine.dispose()


def test_migration_is_reversible(scratch_database):
    env = {**os.environ, "DATABASE_URL": scratch_database}
    subprocess.run(
        ["alembic", "upgrade", "head"], cwd=REPO_ROOT, env=env, capture_output=True, timeout=180
    )
    result = subprocess.run(
        ["alembic", "downgrade", "base"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr

    engine = create_engine(scratch_database)
    try:
        with engine.connect() as conn:
            remaining = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
                )
            ).scalar_one()
        assert remaining == 0
    finally:
        engine.dispose()
