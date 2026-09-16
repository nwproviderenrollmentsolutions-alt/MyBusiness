"""The full discovery chain, wired exactly the way config/agents.yaml wires it — not a
hand-assembled registry. One CEO command flows through Opportunity Discovery -> Lead
Discovery -> Lead Enrichment -> Lead Scoring purely via the task queue and event bus,
with no test code calling one agent from another directly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core import task_queue
from core.registry import AGENTS
from db.enums import CommandStatus, LeadStatus, OpportunityStatus, TaskStatus
from db.models.command import CeoCommand
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Lead, LeadScore
from db.models.runtime import AgentTask
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration

_EXPECTED_AGENTS = {
    "chief_of_staff",
    "opportunity_discovery",
    "lead_discovery",
    "lead_enrichment",
    "lead_scoring",
}

_ACTIVE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.LEASED,
    TaskStatus.RUNNING,
    TaskStatus.AWAITING_APPROVAL,
)


def _drain_queue(*, max_passes: int = 50) -> int:
    """Run the worker until nothing is left to do. Returns total tasks executed."""
    total = 0
    for _ in range(max_passes):
        executed = runner.run_once(worker_id="chain-test", batch_size=50)
        total += executed
        if executed == 0:
            return total
    pytest.fail("queue did not drain within max_passes — a task is stuck or looping")


def test_a_ceo_command_flows_all_the_way_to_scored_leads(db):
    loaded = AGENTS.load_from_config()
    assert set(loaded) == _EXPECTED_AGENTS

    with session_scope() as session:
        command = CeoCommand(raw_text="find our best market opportunity", created_by="ceo")
        session.add(command)
        session.flush()
        task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
        )
        command_id = command.id

    executed = _drain_queue()
    # chief_of_staff + opportunity_discovery + (lead_discovery + N * (enrichment + scoring))
    assert executed >= 4

    with session_scope() as session:
        resolved = session.get(CeoCommand, command_id)
        assert resolved is not None
        assert resolved.status is CommandStatus.DISPATCHED

        opportunity = session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.status is OpportunityStatus.PROPOSED
        assert opportunity.is_mock is True

        leads = list(session.scalars(select(Lead)))
        assert len(leads) > 0
        assert all(lead.opportunity_id == opportunity.id for lead in leads)
        assert all(lead.status in (LeadStatus.SCORED, LeadStatus.DISQUALIFIED) for lead in leads)
        assert all(lead.current_score is not None for lead in leads)

        scores = list(session.scalars(select(LeadScore)))
        assert len(scores) == len(leads)

        for company in session.scalars(select(Company)):
            assert company.is_mock is True
            assert company.domain.endswith(".invalid")
        for prospect in session.scalars(select(Prospect)):
            assert prospect.is_mock is True
            assert prospect.email.endswith(".invalid")

        # Every task the chain spawned reached a terminal state — nothing left hanging
        # waiting on a worker or a human.
        stuck = session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.status.in_(_ACTIVE_TASK_STATUSES))
        )
        assert stuck == 0


def test_direct_lead_discovery_command_skips_opportunity_discovery(db):
    """"find N leads in segment X" doesn't need an opportunity at all."""
    AGENTS.load_from_config()

    with session_scope() as session:
        command = CeoCommand(raw_text="find 5 qualified plumbing leads", created_by="ceo")
        session.add(command)
        session.flush()
        task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
        )
        command_id = command.id

    _drain_queue()

    with session_scope() as session:
        resolved = session.get(CeoCommand, command_id)
        assert resolved.status is CommandStatus.DISPATCHED
        assert session.scalar(select(func.count()).select_from(Opportunity)) == 0

        leads = list(session.scalars(select(Lead)))
        assert len(leads) == 5
        assert all(lead.opportunity_id is None for lead in leads)
        assert all(lead.status in (LeadStatus.SCORED, LeadStatus.DISQUALIFIED) for lead in leads)
