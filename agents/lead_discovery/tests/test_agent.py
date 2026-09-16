from __future__ import annotations

import pytest
from sqlalchemy import func, select

from agents.lead_discovery.agent import AGENT
from core import task_queue
from core.registry import AGENTS
from db.enums import LeadStatus, OpportunityStatus, RunStatus
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Lead
from db.models.runtime import AgentTask, OutboxEvent
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _enqueue(**task_input) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(session, agent="lead_discovery", task_input=task_input)


def test_direct_command_creates_companies_prospects_and_leads(db):
    task = _enqueue(segment="HVAC", count=5)
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 5
        assert session.scalar(select(func.count()).select_from(Prospect)) == 5
        assert session.scalar(select(func.count()).select_from(Lead)) == 5
        lead = session.scalar(select(Lead))
        assert lead.status is LeadStatus.DISCOVERED
        assert lead.opportunity_id is None


def test_discovered_data_is_flagged_mock_and_unreachable(db):
    task = _enqueue(segment="plumbing", count=2)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        company = session.scalar(select(Company))
        prospect = session.scalar(select(Prospect))
        assert company.is_mock is True
        assert company.domain.endswith(".invalid")
        assert prospect.is_mock is True
        assert prospect.email.endswith(".invalid")


def test_a_lead_discovered_event_fires_per_new_lead(db):
    task = _enqueue(segment="dental", count=3)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        events = list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "lead.discovered"))
        )
        assert len(events) == 3
        lead_ids = {str(lead_id) for (lead_id,) in session.execute(select(Lead.id))}
        assert {e.payload["lead_id"] for e in events} == lead_ids


def test_rerunning_the_same_segment_does_not_duplicate_anything(db):
    """Idempotent: a retried task or a second identical command must not double the funnel."""
    first = _enqueue(segment="roofing", count=4)
    runner.execute_task(first.id, worker_id="w1")

    second = _enqueue(segment="roofing", count=4)
    status = runner.execute_task(second.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Company)) == 4
        assert session.scalar(select(func.count()).select_from(Lead)) == 4
        # No re-discovery events for leads that already existed.
        events = list(
            session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "lead.discovered"))
        )
        assert len(events) == 4


def test_count_is_capped_regardless_of_what_is_asked_for(db):
    task = _enqueue(segment="landscaping", count=100_000)
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        created = session.scalar(select(func.count()).select_from(Lead))
        assert created <= 250


def test_triggered_from_an_opportunity_links_leads_to_it(db):
    with session_scope() as session:
        opportunity = Opportunity(
            title="Opp",
            segment="mobile pet grooming",
            thesis="MOCK thesis",
            icp={"segment": "mobile pet grooming", "roles": ["Owner"]},
            status=OpportunityStatus.PROPOSED,
            is_mock=True,
        )
        session.add(opportunity)
        session.flush()
        opportunity_id = opportunity.id

    task = _enqueue(segment="mobile pet grooming", count=2, opportunity_id=str(opportunity_id))
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        leads = list(session.scalars(select(Lead)))
        assert len(leads) == 2
        assert all(lead.opportunity_id == opportunity_id for lead in leads)


def test_unknown_opportunity_id_fails_permanently(db):
    import uuid

    task = _enqueue(segment="x", count=1, opportunity_id=str(uuid.uuid4()))
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED
