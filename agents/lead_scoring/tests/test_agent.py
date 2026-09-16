from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from agents.lead_scoring.agent import AGENT
from agents.lead_scoring.scoring import DEFAULT_QUALIFYING_THRESHOLD
from core import task_queue
from core.registry import AGENTS
from db.enums import LeadStatus, RunStatus
from db.models.market import Company, Prospect
from db.models.pipeline import Lead, LeadScore
from db.models.runtime import AgentTask, OutboxEvent
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _enriched_lead(
    *, title: str | None, size_band: str | None, has_phone: bool = False
) -> uuid.UUID:
    with session_scope() as session:
        company = Company(
            name="MOCK Acme HVAC", domain=f"acme-{uuid.uuid4().hex[:6]}.invalid",
            size_band=size_band, is_mock=True,
        )
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id,
            full_name="MOCK Sam Rivera",
            title=title,
            email=f"sam-{uuid.uuid4().hex[:6]}@acme.invalid",
            phone="+15550100000" if has_phone else None,
            is_mock=True,
        )
        session.add(prospect)
        session.flush()
        lead = Lead(
            prospect_id=prospect.id, company_id=company.id, status=LeadStatus.ENRICHED, is_mock=True
        )
        session.add(lead)
        session.flush()
        return lead.id


def _enqueue(lead_id: uuid.UUID) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(
            session, agent="lead_scoring", task_input={"lead_id": str(lead_id)}
        )


def test_a_strong_lead_is_scored_and_qualifies(db):
    lead_id = _enriched_lead(title="Owner", size_band="11-50", has_phone=True)
    task = _enqueue(lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.SCORED
        assert lead.current_score == 100
        score_row = session.scalar(select(LeadScore).where(LeadScore.lead_id == lead_id))
        assert score_row.model_version == "rule_based_v1"
        assert score_row.score == 100


def test_a_weak_lead_is_disqualified_not_passed_downstream(db):
    lead_id = _enriched_lead(title=None, size_band=None, has_phone=False)
    task = _enqueue(lead_id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.DISQUALIFIED
        assert lead.current_score < DEFAULT_QUALIFYING_THRESHOLD
        assert "below qualifying threshold" in lead.status_reason


def test_qualifying_lead_emits_lead_scored(db):
    lead_id = _enriched_lead(title="Owner", size_band="11-50", has_phone=True)
    task = _enqueue(lead_id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        event = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "lead.scored"))
        assert event is not None
        assert event.payload["lead_id"] == str(lead_id)
        assert event.payload["score"] == 100


def test_disqualified_lead_emits_lead_disqualified_not_lead_scored(db):
    lead_id = _enriched_lead(title=None, size_band=None, has_phone=False)
    task = _enqueue(lead_id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        scored = session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "lead.scored"))
        disqualified = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "lead.disqualified")
        )
        assert scored is None
        assert disqualified is not None
        assert "reason" in disqualified.payload


def test_lead_not_yet_enriched_is_a_no_op(db):
    with session_scope() as session:
        company = Company(name="MOCK X", domain="x.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id, full_name="MOCK Y", email="y@x.invalid", is_mock=True
        )
        session.add(prospect)
        session.flush()
        lead = Lead(
            prospect_id=prospect.id,
            company_id=company.id,
            status=LeadStatus.DISCOVERED,
            is_mock=True,
        )
        session.add(lead)
        session.flush()
        lead_id = lead.id

    task = _enqueue(lead_id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        # Skipped, not scored: no LeadScore row and status unchanged.
        assert session.scalar(select(LeadScore).where(LeadScore.lead_id == lead_id)) is None
        assert session.get(Lead, lead_id).status is LeadStatus.DISCOVERED


def test_unknown_lead_fails_permanently(db):
    task = _enqueue(uuid.uuid4())
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED
