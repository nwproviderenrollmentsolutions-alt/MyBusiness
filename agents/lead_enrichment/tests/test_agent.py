from __future__ import annotations

import pytest
from sqlalchemy import select

from agents.lead_enrichment.agent import AGENT
from core import task_queue
from core.registry import AGENTS
from db.enums import LeadStatus, RunStatus
from db.models.market import Company, Prospect
from db.models.pipeline import Lead
from db.models.runtime import AgentRun, AgentTask, OutboxEvent
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


@pytest.fixture
def discovered_lead(db) -> Lead:
    """Written through session_scope(), not the ``db`` fixture session directly.

    The worker (via runner.execute_task) opens its own connection through
    session_scope(), and under READ COMMITTED it cannot see rows the ``db`` fixture has
    only flushed, not committed. session_scope() commits on exit so the row is visible
    across connections, matching how every other agent test that pre-seeds data does it.
    """
    with session_scope() as session:
        company = Company(name="MOCK Acme HVAC", domain="acme-hvac.invalid", is_mock=True)
        session.add(company)
        session.flush()
        prospect = Prospect(
            company_id=company.id,
            full_name="MOCK Sam Rivera",
            title="Owner",
            email="sam@acme-hvac.invalid",
            is_mock=True,
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
        return lead


def _enqueue(lead_id) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(
            session, agent="lead_enrichment", task_input={"lead_id": str(lead_id)}
        )


def test_enrichment_adds_research_and_advances_status(db, discovered_lead):
    task = _enqueue(discovered_lead.id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        lead = session.get(Lead, discovered_lead.id)
        assert lead.status is LeadStatus.ENRICHED

        company = session.get(Company, discovered_lead.company_id)
        prospect = session.get(Prospect, discovered_lead.prospect_id)
        assert "summary" in company.research
        assert "MOCK" in company.research["summary"]
        assert "summary" in prospect.research
        assert company.research["is_mock"] is True


def test_enrichment_preserves_existing_research_fields(db, discovered_lead):
    with session_scope() as session:
        company = session.get(Company, discovered_lead.company_id)
        company.research = {"manual_note": "CEO already knows this contact"}
        session.flush()

    task = _enqueue(discovered_lead.id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        company = session.get(Company, discovered_lead.company_id)
        assert company.research["manual_note"] == "CEO already knows this contact"
        assert "summary" in company.research


def test_enrichment_emits_lead_enriched_event(db, discovered_lead):
    task = _enqueue(discovered_lead.id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "lead.enriched")
        )
        assert event is not None
        assert event.payload["lead_id"] == str(discovered_lead.id)


def test_already_enriched_lead_is_a_no_op_not_a_re_enrichment(db, discovered_lead):
    """Idempotent: a duplicate delivery of the same lead must not redo the research or
    fire a second lead.enriched event."""
    with session_scope() as session:
        lead = session.get(Lead, discovered_lead.id)
        assert lead is not None
        lead.status = LeadStatus.ENRICHED

    task = _enqueue(discovered_lead.id)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert (
            session.scalar(
                select(AgentRun).where(AgentRun.task_id == task.id)
            ).output["skipped"]
            is True
        )
        events = list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "lead.enriched"))
        )
        assert events == []


def test_unknown_lead_fails_permanently(db):
    import uuid

    task = _enqueue(uuid.uuid4())
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED


def test_records_llm_usage(db, discovered_lead):
    task = _enqueue(discovered_lead.id)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        run = session.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
        assert run.tokens_in > 0
        assert run.cost_usd == 0
