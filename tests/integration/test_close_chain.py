"""End to end past the outreach boundary: a prospect's interested reply drives
Qualification -> Sales -> Proposal -> (approval) -> a second reply -> Sales closes the
deal -> Customer -> (approval), all through the real task queue and event bus, wired the
way config/agents.yaml wires it. Continues where test_outreach_chain.py leaves off.

"find N qualified leads" can qualify more than one lead, each with its own pending
outreach approval — every helper below resolves *one specific* approval (by the subject
it belongs to), never "the only pending approval," so an untouched sibling lead's
approval sitting in the queue can never make a later assertion ambiguous.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from core import approval_gate, task_queue
from core.registry import AGENTS
from db.enums import (
    ActionState,
    ApprovalStatus,
    CommandStatus,
    CustomerStatus,
    DealStage,
    LeadStatus,
)
from db.models.command import CeoCommand
from db.models.pipeline import Lead, Message
from db.models.revenue import Customer, Deal, Proposal
from db.models.runtime import Approval
from db.session import session_scope
from providers.factory import get_email_provider
from worker import runner

pytestmark = pytest.mark.integration


def _drain_queue(*, max_passes: int = 50) -> int:
    total = 0
    for _ in range(max_passes):
        executed = runner.run_once(worker_id="chain-test", batch_size=50)
        total += executed
        if executed == 0:
            return total
    pytest.fail("queue did not drain within max_passes — a task is stuck or looping")


def _submit_command(text: str, *, dry_run: bool = False) -> uuid.UUID:
    with session_scope() as session:
        command = CeoCommand(raw_text=text, created_by="ceo")
        session.add(command)
        session.flush()
        task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
            dry_run=dry_run,
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


def _send_first_touch_outreach() -> uuid.UUID:
    """Runs the discovery + outreach chain (Milestones 3-4) up to one approved, sent
    first-touch email, and returns that lead's id. Every close-chain test below
    continues from here with a different reply."""
    AGENTS.load_from_config()
    _submit_command("find 2 qualified plumbing leads")
    _drain_queue()

    with session_scope() as session:
        contacted = list(session.scalars(select(Lead).where(Lead.status == LeadStatus.CONTACTED)))
        assert contacted, "at least one lead should have qualified and been drafted"
        lead_id = contacted[0].id
        message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        assert message is not None
        message_id = message.id

    _approve(subject_type="message", subject_id=message_id)
    _drain_queue()

    with session_scope() as session:
        message = session.get(Message, message_id)
        assert message is not None
        assert message.state is ActionState.COMPLETED
    return lead_id


def _seed_reply_to(*, provider_message_id, from_email, body):
    # Deliberately untyped (see pyproject.toml's mypy override for tests.*): the mock
    # provider's seed_reply() is a test-only affordance, not part of the EmailProvider
    # protocol get_email_provider() is typed to return.
    get_email_provider().seed_reply(
        from_email=from_email, body=body, in_reply_to=provider_message_id
    )


def test_positive_reply_drives_the_deal_all_the_way_to_a_customer(db):
    lead_id = _send_first_touch_outreach()

    with session_scope() as session:
        outreach_message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        outreach_provider_id = outreach_message.provider_message_id
    _seed_reply_to(
        provider_message_id=outreach_provider_id,
        from_email=get_email_provider().sent[0]["to"],
        body="MOCK: yes, I'm interested, let's talk",
    )

    _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
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

    _approve(subject_type="proposal", subject_id=proposal_id)
    _drain_queue()

    with session_scope() as session:
        proposal = session.get(Proposal, proposal_id)
        assert proposal.state is ActionState.COMPLETED
        proposal_message = session.scalar(
            select(Message).where(Message.proposal_id == proposal_id)
        )
        proposal_provider_id = proposal_message.provider_message_id

    _seed_reply_to(
        provider_message_id=proposal_provider_id,
        from_email=get_email_provider().sent[-1]["to"],
        body="MOCK: sounds good, let's proceed",
    )
    _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.WON
        assert deal.won_at is not None

        approval = session.scalar(
            select(Approval).where(
                Approval.action_type == "customer.create", Approval.subject_id == deal_id
            )
        )
        assert approval is not None
        assert approval.status is ApprovalStatus.PENDING

    _approve(subject_type="deal", subject_id=deal_id)
    _drain_queue()

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        customer = session.scalar(select(Customer).where(Customer.deal_id == deal_id))
        assert customer is not None
        assert customer.status is CustomerStatus.ONBOARDING
        assert customer.contract_value_usd == deal.value_usd


def test_negative_reply_to_the_proposal_loses_the_deal_without_a_customer(db):
    lead_id = _send_first_touch_outreach()

    with session_scope() as session:
        outreach_message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        outreach_to = get_email_provider().sent[0]["to"]
        _seed_reply_to(
            provider_message_id=outreach_message.provider_message_id,
            from_email=outreach_to,
            body="MOCK: yes, I'm interested, let's talk",
        )

    _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
        deal = session.scalar(select(Deal).where(Deal.lead_id == lead_id))
        deal_id = deal.id
        proposal = session.scalar(select(Proposal).where(Proposal.deal_id == deal_id))
        proposal_id = proposal.id

    _approve(subject_type="proposal", subject_id=proposal_id)
    _drain_queue()

    with session_scope() as session:
        proposal_message = session.scalar(select(Message).where(Message.proposal_id == proposal_id))
        proposal_provider_id = proposal_message.provider_message_id

    _seed_reply_to(
        provider_message_id=proposal_provider_id,
        from_email=get_email_provider().sent[-1]["to"],
        body="MOCK: thanks, but we went with a competitor",
    )
    _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
        deal = session.get(Deal, deal_id)
        assert deal.stage is DealStage.LOST
        assert deal.lost_at is not None
        assert session.scalar(select(Customer)) is None
        assert (
            session.scalar(select(Approval).where(Approval.action_type == "customer.create"))
            is None
        )


def test_negative_reply_to_outreach_disqualifies_the_lead_before_any_deal(db):
    lead_id = _send_first_touch_outreach()

    with session_scope() as session:
        outreach_message = session.scalar(select(Message).where(Message.lead_id == lead_id))
        outreach_provider_id = outreach_message.provider_message_id

    _seed_reply_to(
        provider_message_id=outreach_provider_id,
        from_email=get_email_provider().sent[0]["to"],
        body="MOCK: no thanks, not interested",
    )
    reply_command_id = _submit_command("check for replies")
    _drain_queue()

    with session_scope() as session:
        assert session.get(CeoCommand, reply_command_id).status is CommandStatus.DISPATCHED
        lead = session.get(Lead, lead_id)
        assert lead.status is LeadStatus.DISQUALIFIED
        assert session.scalar(select(Deal).where(Deal.lead_id == lead_id)) is None
