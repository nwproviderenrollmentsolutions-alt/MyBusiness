"""Milestone 7: the whole vertical slice, one CEO command chain at a time, entirely on
mock providers under the default dry-run posture — opportunity -> company -> prospect ->
lead -> research -> score -> outreach -> reply -> qualification -> deal -> proposal ->
approval -> a second reply -> won -> an attempted customer, asserting throughout that no
provider ever really sends anything and that every step left a trace in the audit log.

This is deliberately the one test in the suite that never overrides `dry_run` — every
`_submit_command` call below relies on the same default the CLI and the API use
(``settings.dry_run``, true in tests per tests/conftest.py), because the property being
proven is what happens with *nobody touching that switch*, the posture every real
deployment starts in.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from core import approval_gate, task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    ApprovalStatus,
    CommandStatus,
    DealStage,
    LeadStatus,
    TaskStatus,
)
from db.models.command import CeoCommand
from db.models.market import Opportunity
from db.models.pipeline import Lead, Message
from db.models.revenue import Customer, Deal, Proposal
from db.models.runtime import AgentTask, Approval, AuditLog
from db.session import session_scope
from providers.factory import get_email_provider
from worker import runner

pytestmark = pytest.mark.integration

#: A worker should never leave a task sitting here — every task either finishes or is
#: legitimately parked on a human (AWAITING_APPROVAL), never failed or dead-lettered.
_BAD_TASK_STATUSES = (TaskStatus.FAILED, TaskStatus.DEAD_LETTER)


def _drain_queue(*, max_passes: int = 80) -> int:
    total = 0
    for _ in range(max_passes):
        executed = runner.run_once(worker_id="e2e-test", batch_size=50)
        total += executed
        if executed == 0:
            return total
    pytest.fail("queue did not drain within max_passes — a task is stuck or looping")


def _submit_command(text: str) -> uuid.UUID:
    """Deliberately no dry_run override — see module docstring."""
    with session_scope() as session:
        command = CeoCommand(raw_text=text, created_by="ceo")
        session.add(command)
        session.flush()
        task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
        )
        return command.id


def _approve(*, subject_type: str, subject_id: uuid.UUID) -> None:
    with session_scope() as session:
        approval = session.scalar(
            select(Approval).where(
                Approval.subject_type == subject_type,
                Approval.subject_id == subject_id,
                Approval.status == ApprovalStatus.PENDING,
            )
        )
        assert approval is not None, f"no pending approval for {subject_type} {subject_id}"
        approval_gate.approve(session, approval.id, decided_by="ceo")


def _assert_no_task_is_stuck() -> None:
    with session_scope() as session:
        stuck = session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.status.in_(_BAD_TASK_STATUSES))
        )
        assert stuck == 0, "a task failed or was dead-lettered during a dry-run walk"


def _audit_action_count(action: str) -> int:
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
            )
            or 0
        )


def test_full_slice_under_dry_run_never_sends_anything_for_real(db):
    AGENTS.load_from_config()

    # --- Opportunity -> company -> prospect -> lead -> research -> score -> outreach ---
    opportunity_command_id = _submit_command("find our best market opportunity")
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        assert session.get(CeoCommand, opportunity_command_id).status is CommandStatus.DISPATCHED
        opportunity = session.scalar(select(Opportunity))
        assert opportunity is not None
        assert opportunity.is_mock is True

        contacted = list(session.scalars(select(Lead).where(Lead.status == LeadStatus.CONTACTED)))
        assert contacted, "the mock ICP's default roles should score high enough to qualify"
        lead_id = contacted[0].id
        message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        message_id = message.id
        assert message.state is ActionState.PENDING_APPROVAL

    # --- Approve the outreach draft; dry run must simulate the send, not perform it ---
    _approve(subject_type="message", subject_id=message_id)
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        message = session.get(Message, message_id)
        assert message is not None
        assert message.state is ActionState.COMPLETED
        assert message.provider_message_id is not None
        assert message.provider_message_id.startswith("simulated-")
    assert get_email_provider().sent == [], "dry run must never reach the real provider call"

    # --- A positive reply drives qualification, valuation, and a drafted proposal ---
    get_email_provider().seed_reply(
        from_email="anyone@example.invalid",
        body="MOCK: yes, I'm interested, let's talk",
        in_reply_to=message.provider_message_id,
    )
    reply_command_id = _submit_command("check for replies")
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        assert session.get(CeoCommand, reply_command_id).status is CommandStatus.DISPATCHED
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.QUALIFIED

        deal = session.scalar(select(Deal).where(Deal.lead_id == lead_id))
        assert deal is not None
        assert deal.stage is DealStage.PROPOSAL
        assert deal.value_usd > 0
        deal_id = deal.id

        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        assert proposal is not None
        assert proposal.state is ActionState.PENDING_APPROVAL
        proposal_id = proposal.id

    # --- Approve the proposal; dry run must simulate this send too ---
    _approve(subject_type="proposal", subject_id=proposal_id)
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        proposal = session.get(Proposal, proposal_id)
        assert proposal is not None
        assert proposal.state is ActionState.COMPLETED
        proposal_message = session.scalar(
            select(Message).where(Message.proposal_id == proposal_id)
        )
        assert proposal_message is not None
        assert proposal_message.provider_message_id.startswith("simulated-")
        proposal_provider_id = proposal_message.provider_message_id
    assert get_email_provider().sent == []

    # --- A positive reply to the proposal closes the deal ---
    get_email_provider().seed_reply(
        from_email="anyone@example.invalid",
        body="MOCK: sounds good, let's proceed",
        in_reply_to=proposal_provider_id,
    )
    _submit_command("check for replies")
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal is not None
        assert deal.stage is DealStage.WON
        assert deal.won_at is not None

        approval = session.scalar(
            select(Approval).where(
                Approval.action_type == "customer.create", Approval.subject_id == deal_id
            )
        )
        assert approval is not None
        assert approval.status is ApprovalStatus.PENDING

    # --- Approve customer creation; under dry run this must simulate, not materialize a
    #     real customer. Unlike a message or a proposal, a customer record has no "draft"
    #     representation independent of being real — simulating it means not creating the
    #     row at all (see agents/customer/agent.py), which is what this asserts. ---
    _approve(subject_type="deal", subject_id=deal_id)
    _drain_queue()
    _assert_no_task_is_stuck()

    with session_scope() as session:
        assert session.scalar(select(Customer)) is None, (
            "dry run must never materialize a real customer record"
        )

    # --- Nothing, anywhere in this whole chain, ever reached a real provider call ---
    assert get_email_provider().sent == []

    # --- Every phase left a trace an auditor could reconstruct without guessing ---
    # "find our best market opportunity" can qualify more than one lead — this test only
    # ever drives one of them through, so approval.requested (one per qualified lead's
    # outreach draft, plus this lead's proposal and customer requests) is a minimum, not
    # an exact count; approval.approved is exact, since exactly three decisions were made.
    assert _audit_action_count("ceo_command.resolved") == 3
    assert _audit_action_count("approval.requested") >= 3
    assert _audit_action_count("approval.approved") == 3
    assert _audit_action_count("policy.evaluated") >= 5
    assert _audit_action_count("event.dispatched") >= 6
    assert _audit_action_count("task.created") >= 10

    with session_scope() as session:
        simulated_customer_eval = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "policy.evaluated",
                AuditLog.subject_id == lead_id,
                AuditLog.after["decision"].astext == "simulate",
                AuditLog.after["action_type"].astext == "customer.create",
            )
        )
        assert simulated_customer_eval is not None, (
            "the audit trail must show *why* no customer was created — a SIMULATE "
            "decision, not silence"
        )
