from __future__ import annotations

import pytest

from core import flags
from db.models.market import Company
from db.session import session_scope

pytestmark = pytest.mark.integration


def test_health_reports_ok_and_current_safety_posture(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "database": True,
        "emergency_stop": False,
        "dry_run": True,
        "environment": "development",
    }


def test_health_reflects_an_engaged_emergency_stop(client):
    with session_scope() as session:
        flags.engage_emergency_stop(session, engaged_by="ceo", reason="test")

    response = client.get("/health")
    assert response.json()["emergency_stop"] is True


def test_status_reports_business_counts(client):
    with session_scope() as session:
        session.add(Company(name="MOCK Acme", domain="acme.invalid", is_mock=True))

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["companies"] == 1
    assert body["counts"]["leads"] == 0
    assert body["pending_approvals"] == 0
    assert isinstance(body["domain_agents_running"], list)
    assert "chief_of_staff" not in body["domain_agents_running"]


def test_decisions_is_empty_with_nothing_to_review(client):
    response = client.get("/decisions")
    assert response.status_code == 200
    assert response.json() == {"pending_approvals": [], "failed_tasks": []}
