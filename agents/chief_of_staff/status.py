"""Read-only status reporting for the CEO.

These answer directly from current database state — no task dispatch, no agents involved
beyond the query itself. Kept separate from agent.py so the reporting logic can be tested
without going through the task queue.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import approval_gate
from core.registry import AGENTS
from db.enums import CampaignStatus, TaskStatus
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Campaign, Lead
from db.models.revenue import Customer, Deal
from db.models.runtime import AgentTask


def _count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def decisions_summary(session: Session) -> tuple[str, dict[str, Any]]:
    """What the CEO needs to look at right now: pending approvals and stuck tasks."""
    approvals = approval_gate.pending(session, limit=10)
    stuck_tasks = list(
        session.scalars(
            select(AgentTask)
            .where(AgentTask.status.in_([TaskStatus.FAILED, TaskStatus.DEAD_LETTER]))
            .order_by(AgentTask.updated_at.desc())
            .limit(10)
        )
    )

    lines: list[str] = []
    if not approvals and not stuck_tasks:
        lines.append("Nothing needs your attention right now.")
    else:
        if approvals:
            lines.append(f"{len(approvals)} pending approval(s), highest risk first:")
            lines.extend(f"  [{a.risk}] {a.summary} (approval {a.id})" for a in approvals)
        if stuck_tasks:
            lines.append(f"{len(stuck_tasks)} task(s) failed and need review:")
            lines.extend(
                f"  {t.agent} task {t.id} ({t.status}): {t.last_error}" for t in stuck_tasks
            )

    result: dict[str, Any] = {
        "pending_approvals": [
            {
                "id": str(a.id),
                "risk": str(a.risk),
                "summary": a.summary,
                "action_type": a.action_type,
            }
            for a in approvals
        ],
        "failed_tasks": [
            {"id": str(t.id), "agent": t.agent, "status": str(t.status), "error": t.last_error}
            for t in stuck_tasks
        ],
    }
    return "\n".join(lines), result


def status_summary(session: Session) -> tuple[str, dict[str, Any]]:
    """A snapshot of the business. Honest about what isn't built yet."""
    counts = {
        "opportunities": _count(session, Opportunity),
        "companies": _count(session, Company),
        "prospects": _count(session, Prospect),
        "leads": _count(session, Lead),
        "deals": _count(session, Deal),
        "customers": _count(session, Customer),
    }
    active_campaigns = (
        session.scalar(
            select(func.count())
            .select_from(Campaign)
            .where(Campaign.status == CampaignStatus.ACTIVE)
        )
        or 0
    )
    task_rows = session.execute(select(AgentTask.status, func.count()).group_by(AgentTask.status))
    task_counts = {str(status): count for status, count in task_rows}
    pending_approvals = len(approval_gate.pending(session, limit=1000))

    domain_agents = [name for name in AGENTS.names() if name != "chief_of_staff"]

    lines = [
        f"Opportunities: {counts['opportunities']}  |  Companies: {counts['companies']}  "
        f"|  Prospects: {counts['prospects']}  |  Leads: {counts['leads']}",
        f"Deals: {counts['deals']}  |  Customers: {counts['customers']}  "
        f"|  Active campaigns: {active_campaigns}",
        f"Pending approvals: {pending_approvals}",
    ]
    if domain_agents:
        lines.append(f"Business agents running: {', '.join(domain_agents)}")
    else:
        lines.append(
            "No business agents are running yet — discovery, outreach, and sales agents "
            "arrive in later milestones. These figures will be zero until then."
        )

    result: dict[str, Any] = {
        "counts": counts,
        "active_campaigns": active_campaigns,
        "pending_approvals": pending_approvals,
        "task_counts": task_counts,
        "domain_agents_running": domain_agents,
    }
    return "\n".join(lines), result
