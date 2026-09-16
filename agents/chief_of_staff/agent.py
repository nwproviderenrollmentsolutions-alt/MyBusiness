"""Chief of Staff: translates CEO commands into answers, system actions, or dispatched work.

Does not sit in the path of routine agent-to-agent flow — that moves via the event bus.
This agent exists solely to serve the CEO command interface: interpret what was asked,
then either answer it from current state, execute a direct system action, hand it to the
agent that owns it (once that agent exists), or say plainly that it can't be done yet.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agents.chief_of_staff import status
from agents.chief_of_staff.interpreter import help_text, interpret
from agents.chief_of_staff.schemas import ChiefOfStaffInput
from core import audit, campaign_control, flags, task_queue
from core.agent_base import Agent, AgentContext, AgentInput, AgentManifest, AgentOutput, TaskRequest
from core.errors import PermanentError
from core.registry import AGENTS
from db.enums import CommandIntent, CommandStatus
from db.models.command import CeoCommand
from db.models.pipeline import Campaign

#: Which agent would eventually handle each intent that Milestone 2 can't dispatch to yet.
_REQUIRED_AGENT: dict[CommandIntent, str] = {
    CommandIntent.FIND_OPPORTUNITIES: "opportunity_discovery",
    CommandIntent.FIND_LEADS: "lead_discovery",
    CommandIntent.START_CAMPAIGN: "outreach",
}


class ChiefOfStaffAgent(Agent):
    MANIFEST = AgentManifest(
        name="chief_of_staff",
        version="1.0.0",
        description=(
            "Interprets CEO commands: answers status/decision queries directly, executes "
            "kill-switch and campaign-control commands, dispatches to a domain agent when "
            "one is registered for the intent, or reports plainly that no agent handles it yet."
        ),
        input_model=ChiefOfStaffInput,
        allowed_tools=[],
        owns_tables=["ceo_commands"],
        timeout_seconds=30,
        max_attempts=2,
    )

    def run(self, agent_input: AgentInput, ctx: AgentContext) -> AgentOutput:
        assert isinstance(agent_input, ChiefOfStaffInput)
        session = ctx.session

        command = session.get(CeoCommand, agent_input.command_id)
        if command is None:
            raise PermanentError(f"ceo command {agent_input.command_id} does not exist")

        parsed = interpret(command.raw_text)
        command.intent = parsed.intent
        command.params = parsed.params
        follow_up: TaskRequest | None = None

        match parsed.intent:
            case CommandIntent.UNKNOWN:
                self._unrecognized(command)
            case CommandIntent.ENGAGE_KILL_SWITCH:
                self._engage_kill_switch(command, session)
            case CommandIntent.RELEASE_KILL_SWITCH:
                self._release_kill_switch(command, session)
            case (
                CommandIntent.PAUSE_CAMPAIGN
                | CommandIntent.RESUME_CAMPAIGN
                | CommandIntent.KILL_CAMPAIGN
            ):
                self._control_campaign(command, session, parsed.intent)
            case CommandIntent.SHOW_DECISIONS:
                response, result = status.decisions_summary(session)
                command.status = CommandStatus.ANSWERED
                command.response = response
                command.result = result
            case CommandIntent.SHOW_STATUS:
                response, result = status.status_summary(session)
                command.status = CommandStatus.ANSWERED
                command.response = response
                command.result = result
            case (
                CommandIntent.FIND_OPPORTUNITIES
                | CommandIntent.FIND_LEADS
                | CommandIntent.START_CAMPAIGN
            ):
                follow_up = self._dispatch_or_block(command, parsed.intent)

        command.resolved_at = task_queue.db_now(session)
        session.flush()
        audit.record_agent(
            session,
            agent=self.name,
            action="ceo_command.resolved",
            subject_type="ceo_command",
            subject_id=command.id,
            after={"intent": str(command.intent), "status": str(command.status)},
            task_id=ctx.task_id,
            correlation_id=ctx.correlation_id,
        )

        output = AgentOutput.success(
            result={
                "command_id": str(command.id),
                "status": str(command.status),
                "response": command.response,
            }
        )
        if follow_up is not None:
            output.follow_up_tasks.append(follow_up)
        return output

    def _unrecognized(self, command: CeoCommand) -> None:
        command.status = CommandStatus.UNRECOGNIZED
        command.response = help_text()

    def _engage_kill_switch(self, command: CeoCommand, session: Session) -> None:
        flags.engage_emergency_stop(
            session, engaged_by=command.created_by, reason=f'CEO command: "{command.raw_text}"'
        )
        command.status = CommandStatus.EXECUTED
        command.response = (
            "Emergency stop engaged. No outbound action will execute until you release it."
        )

    def _release_kill_switch(self, command: CeoCommand, session: Session) -> None:
        flags.release_emergency_stop(
            session, released_by=command.created_by, reason=f'CEO command: "{command.raw_text}"'
        )
        command.status = CommandStatus.EXECUTED
        command.response = "Emergency stop released. Normal policy rules apply again."

    def _control_campaign(
        self, command: CeoCommand, session: Session, intent: CommandIntent
    ) -> None:
        name = str(command.params.get("campaign_name", ""))
        campaign = session.scalar(
            select(Campaign).where(func.lower(Campaign.name) == name.lower())
        )
        if campaign is None:
            command.status = CommandStatus.FAILED
            command.response = f'No campaign named "{name}" was found.'
            return

        try:
            if intent is CommandIntent.PAUSE_CAMPAIGN:
                campaign_control.pause(session, campaign, by=command.created_by)
                command.response = f'Campaign "{campaign.name}" paused.'
            elif intent is CommandIntent.RESUME_CAMPAIGN:
                campaign_control.resume(session, campaign, by=command.created_by)
                command.response = f'Campaign "{campaign.name}" resumed.'
            else:
                campaign_control.kill(
                    session,
                    campaign,
                    by=command.created_by,
                    reason=f'CEO command: "{command.raw_text}"',
                )
                command.response = (
                    f'Campaign "{campaign.name}" killed. This cannot be undone — '
                    "start a new campaign instead of trying to resume it."
                )
            command.status = CommandStatus.EXECUTED
        except PermanentError as exc:
            command.status = CommandStatus.FAILED
            command.response = str(exc)

    def _dispatch_or_block(
        self, command: CeoCommand, intent: CommandIntent
    ) -> TaskRequest | None:
        needed = _REQUIRED_AGENT[intent]
        if needed not in AGENTS.names():
            command.status = CommandStatus.BLOCKED
            command.required_agents = [needed]
            command.response = (
                f'Understood — this needs the "{needed}" agent, which has not been built '
                "yet. See ARCHITECTURE.md's milestone plan for when it lands."
            )
            return None

        command.status = CommandStatus.DISPATCHED
        command.response = (
            f'Dispatched to the "{needed}" agent. This command is fire-and-forget for now — '
            "check its task's own status for progress."
        )
        return TaskRequest(
            agent=needed,
            task_input=dict(command.params),
            # Distinct from the triggering task's own dedupe key (typically
            # "ceo_command:<id>") — colliding with it would make enqueue() return the
            # original task instead of creating this follow-up.
            dedupe_key=f"ceo_command:{command.id}:dispatch",
        )


AGENT = ChiefOfStaffAgent()
