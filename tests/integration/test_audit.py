"""The audit log is the CEO's answer to "why did this happen?". It has to be complete."""

from __future__ import annotations

import pytest
from sqlalchemy import select

import core.audit as audit_module
from core import approval_gate, audit, flags, suppression, task_queue
from core.observability import redact
from db.enums import ActionType, ActorType, RiskLevel, SuppressionScope
from db.models.runtime import AuditLog
from tests.conftest import make_policy

pytestmark = pytest.mark.integration


def _actions(db) -> list[str]:
    return [row.action for row in db.scalars(select(AuditLog).order_by(AuditLog.created_at))]


def test_a_task_lifecycle_is_fully_traceable(db):
    task = task_queue.enqueue(db, agent="demo")
    task_queue.mark_running(db, task)
    task_queue.complete(db, task, {"ok": True})
    db.commit()

    assert _actions(db) == ["task.created", "task.started", "task.succeeded"]


def test_approval_lifecycle_records_the_human_decision(db, policy):
    policy(make_policy())
    task = task_queue.enqueue(db, agent="outreach")
    approval = approval_gate.request(
        db,
        action_type=ActionType.OUTREACH_SEND_EMAIL,
        agent="outreach",
        summary="Send intro email",
        task=task,
        risk=RiskLevel.HIGH,
    )
    approval_gate.approve(db, approval.id, decided_by="ceo@example.com", notes="ship it")
    db.commit()

    decision = db.scalar(select(AuditLog).where(AuditLog.action == "approval.approved"))
    assert decision.actor_type is ActorType.HUMAN
    assert decision.actor == "ceo@example.com"
    assert decision.reason == "ship it"
    assert "task.awaiting_approval" in _actions(db)
    assert "task.resumed" in _actions(db)


def test_every_entry_carries_the_correlation_id(db):
    """One business outcome can be traced end to end, across agents."""
    task = task_queue.enqueue(db, agent="demo")
    task_queue.mark_running(db, task)
    db.commit()

    entries = db.scalars(select(AuditLog).where(AuditLog.task_id == task.id)).all()
    assert entries
    assert all(e.correlation_id == task.correlation_id for e in entries)


def test_kill_switch_changes_record_who_and_why(db):
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="prospect complained")
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "flag.emergency_stop.set"))
    assert entry.actor == "ceo"
    assert entry.reason == "prospect complained"
    assert entry.after == {"enabled": True}


def test_suppression_additions_are_audited(db):
    suppression.add_suppression(
        db, scope=SuppressionScope.EMAIL, value="Person@Example.invalid", reason="unsubscribed"
    )
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "suppression.added"))
    assert entry.after["value"] == "person@example.invalid"
    assert entry.reason == "unsubscribed"


def test_secrets_never_reach_the_audit_log(db):
    audit.record_system(
        db,
        action="test.entry",
        after={"api_key": "sk-live-should-not-persist", "to": "sam@acme.invalid"},
    )
    db.commit()

    entry = db.scalar(select(AuditLog).where(AuditLog.action == "test.entry"))
    assert entry.after["api_key"] == "[REDACTED]"
    # Non-secret context is preserved; redaction is targeted, not blanket.
    assert entry.after["to"] == "sam@acme.invalid"


def test_redaction_reaches_nested_structures():
    payload = {"outer": {"authorization": "Bearer xyz", "items": [{"token": "abc", "id": 1}]}}
    cleaned = redact(payload)

    assert cleaned["outer"]["authorization"] == "[REDACTED]"
    assert cleaned["outer"]["items"][0]["token"] == "[REDACTED]"
    assert cleaned["outer"]["items"][0]["id"] == 1


def test_the_audit_module_offers_no_way_to_change_history():
    """Append-only is enforced by there being no update or delete path to call."""
    exported = {name for name in dir(audit_module) if not name.startswith("_")}
    assert not {n for n in exported if "update" in n or "delete" in n or "purge" in n}
