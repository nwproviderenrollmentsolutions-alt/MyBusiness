from __future__ import annotations

import pytest
from sqlalchemy import select

from agents.opportunity_discovery.agent import AGENT, DEFAULT_AUTO_DISCOVERY_LEAD_COUNT
from core import task_queue
from core.registry import AGENTS
from db.enums import OpportunityStatus, RunStatus
from db.models.market import Opportunity
from db.models.runtime import AgentTask, OutboxEvent
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def registered(db):
    AGENTS.register(AGENT)


def _enqueue(**task_input) -> AgentTask:
    with session_scope() as session:
        return task_queue.enqueue(session, agent="opportunity_discovery", task_input=task_input)


def test_discovery_creates_a_proposed_opportunity(db):
    task = _enqueue()
    status = runner.execute_task(task.id, worker_id="w1")

    assert status is RunStatus.SUCCEEDED
    with session_scope() as session:
        opportunity = session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.status is OpportunityStatus.PROPOSED
        assert opportunity.discovered_by_agent == "opportunity_discovery"


def test_no_number_is_fabricated_for_value_or_confidence(db):
    """Research alone doesn't establish a real dollar figure — a plausible-looking one
    would be worse than none."""
    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        opportunity = session.scalar(select(Opportunity))
        assert opportunity.estimated_value_usd is None
        assert opportunity.confidence is None


def test_mock_provider_output_is_flagged(db):
    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        opportunity = session.scalar(select(Opportunity))
        assert opportunity.is_mock is True
        assert "MOCK" in opportunity.thesis


def test_hint_narrows_the_segment(db):
    task = _enqueue(hint="mobile dog grooming")
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        opportunity = session.scalar(select(Opportunity))
        assert opportunity.segment == "mobile dog grooming"
        assert opportunity.icp["segment"] == "mobile dog grooming"


def test_discovery_emits_an_event_lead_discovery_can_subscribe_to(db):
    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "opportunity.discovered")
        )
        assert event is not None
        assert event.payload["count"] == DEFAULT_AUTO_DISCOVERY_LEAD_COUNT
        opportunity = session.scalar(select(Opportunity))
        assert event.payload["opportunity_id"] == str(opportunity.id)
        assert event.payload["segment"] == opportunity.segment


def test_extra_input_fields_are_rejected(db):
    """extra='forbid' on AgentInput: a task with fields this agent doesn't understand
    must fail loudly, not silently ignore them."""
    task = _enqueue(nonsense_field="x")
    status = runner.execute_task(task.id, worker_id="w1")
    assert status is RunStatus.FAILED


def test_records_llm_usage_on_the_run(db):
    task = _enqueue()
    runner.execute_task(task.id, worker_id="w1")

    with session_scope() as session:
        from db.models.runtime import AgentRun

        run = session.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
        assert run.tokens_in > 0
        # The mock LLM spends nothing.
        assert run.cost_usd == 0
