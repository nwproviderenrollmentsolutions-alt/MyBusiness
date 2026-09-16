"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from db.session import session_scope


def get_db() -> Iterator[Session]:
    """One request, one transaction. Commits on success, rolls back on any exception —
    the same session_scope() every CLI command and every agent uses, so the API is not a
    second, divergent way of touching the database.
    """
    with session_scope() as session:
        yield session
