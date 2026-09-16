from __future__ import annotations

import uuid

import pytest

from core import approval_gate
from db.enums import ActionType, ApprovalStatus, RiskLevel
from db.session import session_scope

pytestmark = pytest.mark.integration


def _pending_approval(**overrides) -> uuid.UUID:
    with session_scope() as session:
        approval = approval_gate.request(
            session,
            action_type=overrides.get("action_type", ActionType.OUTREACH_SEND_EMAIL),
            agent="outreach",
            summary=overrides.get("summary", "Send intro email to MOCK Sam Rivera"),
            payload={"to": "sam@acme.invalid", "subject": "Hi", "body": "MOCK draft"},
            risk=overrides.get("risk", RiskLevel.MEDIUM),
        )
        return approval.id


def test_list_pending_approvals(client):
    approval_id = _pending_approval()

    response = client.get("/approvals")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(approval_id)
    assert body[0]["status"] == ApprovalStatus.PENDING.value


def test_approve_records_the_decision(client):
    approval_id = _pending_approval()

    response = client.post(
        f"/approvals/{approval_id}/approve", json={"decided_by": "ceo", "notes": "go ahead"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ApprovalStatus.APPROVED.value
    assert body["decided_by"] == "ceo"
    assert body["decision_notes"] == "go ahead"

    assert client.get("/approvals").json() == []


def test_approve_with_an_edit_stores_the_edited_payload(client):
    approval_id = _pending_approval()

    response = client.post(
        f"/approvals/{approval_id}/approve",
        json={"decided_by": "ceo", "edited_payload": {"subject": "CEO rewrote it"}},
    )
    assert response.status_code == 200
    assert response.json()["edited_payload"] == {"subject": "CEO rewrote it"}


def test_reject_records_the_decision(client):
    approval_id = _pending_approval()

    response = client.post(
        f"/approvals/{approval_id}/reject", json={"decided_by": "ceo", "notes": "not this one"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ApprovalStatus.REJECTED.value
    assert body["decision_notes"] == "not this one"


def test_approve_unknown_approval_is_404(client):
    response = client.post(f"/approvals/{uuid.uuid4()}/approve", json={})
    assert response.status_code == 404


def test_approving_an_already_decided_approval_is_409(client):
    approval_id = _pending_approval()
    client.post(f"/approvals/{approval_id}/approve", json={"decided_by": "ceo"})

    response = client.post(f"/approvals/{approval_id}/approve", json={"decided_by": "ceo"})
    assert response.status_code == 409


def test_decided_by_defaults_to_ceo(client):
    approval_id = _pending_approval()
    response = client.post(f"/approvals/{approval_id}/reject", json={})
    assert response.json()["decided_by"] == "ceo"
