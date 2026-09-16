"""CEO command-line interface.

A real, working entry point for the CEO today — not the eventual web dashboard
(Milestone 6), but usable now for submitting commands, checking status, and deciding
approvals.

Two safety notes:

* ``stop`` / ``go`` flip the emergency-stop flag directly, bypassing the command queue and
  the interpreter entirely. The panic button must not depend on either working.
* ``command`` submits through Chief of Staff like any other instruction and processes it
  synchronously so the CLI feels responsive. It does not start a background worker — run
  ``ceo work`` (or ``ceo work --forever``) for that.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Sequence

from sqlalchemy import select

from core import approval_gate, flags, task_queue
from core.observability import configure_logging
from core.registry import AGENTS
from core.tools import register_builtin_tools
from db.enums import RunStatus
from db.models.command import CeoCommand
from db.session import session_scope
from worker import runner


def _bootstrap() -> None:
    configure_logging()
    register_builtin_tools()
    AGENTS.load_from_config()


def cmd_command(args: argparse.Namespace) -> None:
    text = " ".join(args.text)
    with session_scope() as session:
        command = CeoCommand(raw_text=text, created_by=args.as_)
        session.add(command)
        session.flush()
        task = task_queue.enqueue(
            session,
            agent="chief_of_staff",
            task_input={"command_id": str(command.id)},
            dedupe_key=f"ceo_command:{command.id}",
        )
        command_id, task_id = command.id, task.id

    run_status = runner.execute_task(task_id, worker_id="cli")

    with session_scope() as session:
        resolved = session.get(CeoCommand, command_id)
        assert resolved is not None, "the command we just created cannot have vanished"
        print(f"[{resolved.status}] {resolved.response}")

    if run_status in (RunStatus.FAILED, RunStatus.TIMED_OUT):
        sys.exit(1)


def cmd_commands(args: argparse.Namespace) -> None:
    with session_scope() as session:
        rows = session.scalars(
            select(CeoCommand).order_by(CeoCommand.created_at.desc()).limit(args.limit)
        ).all()
        if not rows:
            print("No commands yet.")
            return
        for row in rows:
            print(f"{row.created_at:%Y-%m-%d %H:%M}  [{row.status}]  {row.raw_text!r}")
            if row.response:
                print(f"    -> {row.response.splitlines()[0]}")


def cmd_approvals(args: argparse.Namespace) -> None:
    with session_scope() as session:
        pending = approval_gate.pending(session, limit=args.limit)
        if not pending:
            print("No pending approvals.")
            return
        for approval in pending:
            print(f"{approval.id}  [{approval.risk}]  {approval.action_type}  {approval.summary}")


def cmd_approve(args: argparse.Namespace) -> None:
    with session_scope() as session:
        approval = approval_gate.approve(
            session, uuid.UUID(args.approval_id), decided_by=args.as_, notes=args.notes
        )
        print(f"Approved {approval.id}.")


def cmd_reject(args: argparse.Namespace) -> None:
    with session_scope() as session:
        approval = approval_gate.reject(
            session, uuid.UUID(args.approval_id), decided_by=args.as_, notes=args.notes
        )
        print(f"Rejected {approval.id}.")


def cmd_stop(args: argparse.Namespace) -> None:
    with session_scope() as session:
        flags.engage_emergency_stop(
            session, engaged_by=args.as_, reason=args.reason or "CLI emergency stop"
        )
    print("Emergency stop engaged. No outbound action will execute until you run `ceo go`.")


def cmd_go(args: argparse.Namespace) -> None:
    with session_scope() as session:
        flags.release_emergency_stop(
            session, released_by=args.as_, reason=args.reason or "CLI release"
        )
    print("Emergency stop released. Normal policy rules apply again.")


def cmd_work(args: argparse.Namespace) -> None:
    if args.forever:
        runner.run_forever()
    else:
        executed = runner.run_once(worker_id="cli")
        print(f"Processed {executed} task(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ceo", description="CEO command-line interface.")
    parser.add_argument(
        "--as", dest="as_", default="ceo", help="Identity recorded as the actor (default: ceo)"
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_command = sub.add_parser("command", help="Submit a command to the Chief of Staff")
    p_command.add_argument("text", nargs="+")
    p_command.set_defaults(func=cmd_command)

    p_commands = sub.add_parser("commands", help="List recent commands")
    p_commands.add_argument("--limit", type=int, default=10)
    p_commands.set_defaults(func=cmd_commands)

    p_approvals = sub.add_parser("approvals", help="List pending approvals")
    p_approvals.add_argument("--limit", type=int, default=20)
    p_approvals.set_defaults(func=cmd_approvals)

    p_approve = sub.add_parser("approve", help="Approve a pending approval")
    p_approve.add_argument("approval_id")
    p_approve.add_argument("--notes", default=None)
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="Reject a pending approval")
    p_reject.add_argument("approval_id")
    p_reject.add_argument("--notes", default=None)
    p_reject.set_defaults(func=cmd_reject)

    p_stop = sub.add_parser("stop", help="Engage the global emergency stop immediately")
    p_stop.add_argument("--reason", default=None)
    p_stop.set_defaults(func=cmd_stop)

    p_go = sub.add_parser("go", help="Release the global emergency stop")
    p_go.add_argument("--reason", default=None)
    p_go.set_defaults(func=cmd_go)

    p_work = sub.add_parser("work", help="Run the worker to drain the task queue")
    p_work.add_argument("--forever", action="store_true")
    p_work.set_defaults(func=cmd_work)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    _bootstrap()
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
