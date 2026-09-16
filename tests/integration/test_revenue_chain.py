"""The full pipeline, wired exactly the way config/agents.yaml wires it — not a
hand-assembled registry. One CEO command flows through Opportunity Discovery -> Lead
Discovery -> Lead Enrichment -> Lead Scoring -> Outreach purely via the task queue and
event bus, with no test code calling one agent from another directly, until it correctly
stops and waits at the one point a human has to decide: approving the drafted email.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core import task_queue
from core.registry import AGENTS
from db.enums import ActionState, CommandStatus, LeadStatus, OpportunityStatus, TaskStatus
from db.models.command import CeoCommand
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Lead, LeadScore, Message
from db.models.runtime import AgentTask, Approval
from db.session import session_scope
from worker import runner

pytestmark = pytest.mark.integration

_EXPECTED_AGENTS = {
    "chief_of_staff",
    "opportunity_discovery",
    "lead_discovery",
    "lead_enrichment",
    "lead_scoring",
    "outreach",
    "conversation_management",
}

#: A worker should never leave a task sitting in one of these — every task must either
#: finish or be legitimately parked waiting on a human (AWAITING_APPROVAL, checked
#: separately below since it's an expected outcome here, not a stuck one).
_STUCK_TASK_STATUSES = (TaskStatus.PENDING, TaskStatus.LEASED, TaskStatus.RUNNING)


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
    # chief_of_staff + opportunity_discovery + lead_discovery
    # + N * (enrichment + scoring + outreach draft)
    assert executed >= 5

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
        assert all(lead.current_score is not None for lead in leads)
        # A qualified lead doesn't stop at `scored` — Outreach picks it up automatically
        # and drives it to `contacted` while its draft awaits approval. A disqualified
        # one is never touched by Outreach at all.
        assert all(
            lead.status in (LeadStatus.CONTACTED, LeadStatus.DISQUALIFIED) for lead in leads
        )

        scores = list(session.scalars(select(LeadScore)))
        assert len(scores) == len(leads)

        contacted = [lead for lead in leads if lead.status is LeadStatus.CONTACTED]
        assert contacted, "the mock ICP's default roles should score high enough to qualify"

        # Every qualified lead has a drafted message sitting in pending_approval — the
        # pipeline stopped exactly where a human is supposed to be asked, not before.
        drafts = list(session.scalars(select(Message)))
        assert len(drafts) == len(contacted)
        assert all(m.state is ActionState.PENDING_APPROVAL for m in drafts)

        approvals = list(session.scalars(select(Approval)))
        assert len(approvals) == len(contacted)
        assert all(a.subject_type == "message" for a in approvals)

        for company in session.scalars(select(Company)):
            assert company.is_mock is True
            assert company.domain.endswith(".invalid")
        for prospect in session.scalars(select(Prospect)):
            assert prospect.is_mock is True
            assert prospect.email.endswith(".invalid")

        # Nothing is sitting idle waiting on a worker. Tasks parked on a human decision
        # (AWAITING_APPROVAL) are the expected, correct resting state here — one per
        # drafted outreach message — not a stuck pipeline.
        stuck = session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.status.in_(_STUCK_TASK_STATUSES))
        )
        assert stuck == 0
        awaiting_approval = session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.status == TaskStatus.AWAITING_APPROVAL)
        )
        assert awaiting_approval == len(contacted)


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
        assert all(
            lead.status in (LeadStatus.CONTACTED, LeadStatus.DISQUALIFIED) for lead in leads
        )
