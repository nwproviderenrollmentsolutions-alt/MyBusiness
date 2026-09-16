from __future__ import annotations

import pytest

from agents.chief_of_staff.agent import AGENT
from core import flags
from core.agent_base import AgentContext
from core.tool_registry import TOOLS, ToolBelt
from db.enums import CampaignStatus, Channel, CommandStatus
from db.models.command import CeoCommand
from db.models.pipeline import Campaign

pytestmark = pytest.mark.integration

# The "unbuilt agent" tests below rely on the global agent registry being empty of
# domain agents (the autouse clean_registries fixture in conftest.py guarantees this),
# so FIND_OPPORTUNITIES / FIND_LEADS / START_CAMPAIGN commands land on BLOCKED.


def _command(db, text: str, created_by: str = "ceo@example.com") -> CeoCommand:
    command = CeoCommand(raw_text=text, created_by=created_by)
    db.add(command)
    db.flush()
    return command


def _run(db, command: CeoCommand) -> CeoCommand:
    """Invoke the agent directly against the real DB session, bypassing the task queue.

    Milestone 2's worker-level dispatch is exercised separately in test_worker_dispatch;
    this file is about the agent's decision logic per intent.
    """
    tools = ToolBelt(
        agent="chief_of_staff",
        allowed=frozenset(AGENT.MANIFEST.allowed_tools),
        session=db,
        registry=TOOLS,
    )
    ctx = AgentContext(
        session=db,
        task=_fake_task(command),
        run_id=command.id,
        tools=tools,
        dry_run=True,
        correlation_id=command.id,
    )
    from agents.chief_of_staff.schemas import ChiefOfStaffInput

    AGENT.run(ChiefOfStaffInput(command_id=command.id), ctx)
    db.flush()
    return command


def _fake_task(command: CeoCommand):  # noqa: ANN201
    from db.models.runtime import AgentTask

    return AgentTask(id=command.id, agent="chief_of_staff", correlation_id=command.id)


def test_show_status_answers_directly(db):
    command = _command(db, "What's our status?")
    _run(db, command)

    assert command.status is CommandStatus.ANSWERED
    assert "No business agents are running yet" in command.response
    assert command.result["counts"]["leads"] == 0


def test_show_decisions_reports_nothing_pending_when_queue_is_empty(db):
    command = _command(db, "Show me today's decisions")
    _run(db, command)

    assert command.status is CommandStatus.ANSWERED
    assert "Nothing needs your attention" in command.response


def test_unrecognized_command_gets_help_text(db):
    command = _command(db, "please reorganize my sock drawer")
    _run(db, command)

    assert command.status is CommandStatus.UNRECOGNIZED
    assert "didn't recognize" in command.response


def test_engage_kill_switch_is_executed_and_audited(db):
    command = _command(db, "engage the kill switch", created_by="ceo@example.com")
    _run(db, command)

    assert command.status is CommandStatus.EXECUTED
    assert flags.is_emergency_stopped(db) is True


def test_release_kill_switch(db):
    flags.engage_emergency_stop(db, engaged_by="ceo", reason="testing")
    command = _command(db, "release the kill switch")
    _run(db, command)

    assert command.status is CommandStatus.EXECUTED
    assert flags.is_emergency_stopped(db) is False


def test_pause_unknown_campaign_fails_cleanly(db):
    command = _command(db, "pause campaign Nonexistent Campaign")
    _run(db, command)

    assert command.status is CommandStatus.FAILED
    assert "No campaign named" in command.response


def test_pause_and_resume_a_real_campaign(db):
    campaign = Campaign(
        name="Q1 HVAC Outreach", status=CampaignStatus.ACTIVE, channel=Channel.EMAIL
    )
    db.add(campaign)
    db.flush()

    _run(db, _command(db, "pause campaign Q1 HVAC Outreach"))
    assert campaign.status is CampaignStatus.PAUSED

    _run(db, _command(db, "resume campaign Q1 HVAC Outreach"))
    assert campaign.status is CampaignStatus.ACTIVE


def test_kill_campaign_is_terminal(db):
    campaign = Campaign(name="Dead End", status=CampaignStatus.ACTIVE, channel=Channel.EMAIL)
    db.add(campaign)
    db.flush()

    _run(db, _command(db, "kill campaign Dead End"))
    assert campaign.status is CampaignStatus.KILLED

    command = _command(db, "resume campaign Dead End")
    _run(db, command)
    assert command.status is CommandStatus.FAILED


def test_campaign_names_are_matched_case_insensitively(db):
    campaign = Campaign(
        name="Q1 HVAC Outreach", status=CampaignStatus.ACTIVE, channel=Channel.EMAIL
    )
    db.add(campaign)
    db.flush()

    _run(db, _command(db, "pause campaign q1 hvac outreach"))
    assert campaign.status is CampaignStatus.PAUSED


@pytest.mark.parametrize(
    ("text", "expected_agent"),
    [
        ("find our best market opportunity", "opportunity_discovery"),
        ("find 50 qualified plumbing leads", "lead_discovery"),
        ("start a campaign for the new offer", "outreach"),
    ],
)
def test_commands_needing_unbuilt_agents_are_blocked_not_faked(db, text, expected_agent):
    """Milestone 2 has no domain agents. Claiming to dispatch would be a fake feature."""
    command = _command(db, text)
    _run(db, command)

    assert command.status is CommandStatus.BLOCKED
    assert command.required_agents == [expected_agent]
    assert expected_agent in command.response
