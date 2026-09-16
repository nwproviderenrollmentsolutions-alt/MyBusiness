from __future__ import annotations

import pytest

from core import approval_gate, flags
from db.enums import ActionType, ApprovalStatus, RiskLevel
from db.session import session_scope

pytestmark = pytest.mark.integration


def test_dashboard_renders_with_no_data(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Nothing pending" in response.text
    assert "No commands yet" in response.text
    assert "Running normally" in response.text


def test_dashboard_shows_the_emergency_stop_banner_when_engaged(client):
    with session_scope() as session:
        flags.engage_emergency_stop(session, engaged_by="ceo", reason="test")

    response = client.get("/")
    assert "Emergency stop is ENGAGED" in response.text


def test_dashboard_lists_a_pending_approval(client):
    with session_scope() as session:
        approval_gate.request(
            session,
            action_type=ActionType.OUTREACH_SEND_EMAIL,
            agent="outreach",
            summary="Send intro email to MOCK Sam Rivera",
            payload={"to": "sam@acme.invalid"},
            risk=RiskLevel.LOW,
        )

    response = client.get("/")
    assert "Send intro email to MOCK Sam Rivera" in response.text
    assert "outreach.send_email" in response.text


def test_submitting_a_command_via_the_form_redirects_home(client):
    response = client.post(
        "/dashboard/commands", data={"text": "show status"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    response = client.get("/")
    assert "show status" in response.text


def test_approving_via_the_form_redirects_and_clears_the_queue(client):
    with session_scope() as session:
        approval = approval_gate.request(
            session,
            action_type=ActionType.OUTREACH_SEND_EMAIL,
            agent="outreach",
            summary="Send intro email",
            payload={},
            risk=RiskLevel.LOW,
        )
        approval_id = approval.id

    response = client.post(
        f"/dashboard/approvals/{approval_id}/approve",
        data={"actor": "sam", "notes": "go ahead"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with session_scope() as session:
        from db.models.runtime import Approval

        refreshed = session.get(Approval, approval_id)
        assert refreshed.status is ApprovalStatus.APPROVED
        assert refreshed.decided_by == "sam"

    assert "Nothing pending" in client.get("/").text


def test_rejecting_via_the_form_redirects_and_clears_the_queue(client):
    with session_scope() as session:
        approval = approval_gate.request(
            session,
            action_type=ActionType.OUTREACH_SEND_EMAIL,
            agent="outreach",
            summary="Send intro email",
            payload={},
            risk=RiskLevel.LOW,
        )
        approval_id = approval.id

    response = client.post(
        f"/dashboard/approvals/{approval_id}/reject", data={}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "Nothing pending" in client.get("/").text


def test_stop_and_go_via_the_dashboard_forms(client):
    response = client.post("/dashboard/system/stop", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert "Emergency stop is ENGAGED" in client.get("/").text

    response = client.post("/dashboard/system/go", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert "Running normally" in client.get("/").text


def test_a_reply_containing_html_is_escaped_not_rendered(client):
    """Command text is CEO-authored, not attacker-controlled, but the template must not
    trust that — no user-supplied string should ever be interpreted as markup."""
    client.post("/dashboard/commands", data={"text": "<script>alert(1)</script>"})
    response = client.get("/")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
