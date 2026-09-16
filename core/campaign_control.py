"""Campaign-level control: pause, resume, kill.

These are CEO-issued overrides, not autonomous agent actions, so they execute directly
rather than through the policy/approval gate — the human is already the one giving the
instruction. Every change is audited and emits an event so a future Outreach agent can
react (e.g. cancel in-flight drafts) without this module knowing it exists.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core import audit, event_bus
from core.errors import PermanentError
from core.task_queue import db_now
from db.enums import ActorType, CampaignStatus
from db.models.pipeline import Campaign

_AUDIT_FIELDS = ("status", "killed_at", "killed_by", "kill_reason")


def pause(session: Session, campaign: Campaign, *, by: str) -> Campaign:
    if campaign.status is not CampaignStatus.ACTIVE:
        raise PermanentError(f"cannot pause a campaign that is {campaign.status}")
    before = audit.snapshot(campaign, _AUDIT_FIELDS)
    campaign.status = CampaignStatus.PAUSED
    session.flush()
    audit.record(
        session,
        actor_type=ActorType.HUMAN,
        actor=by,
        action="campaign.paused",
        subject_type="campaign",
        subject_id=campaign.id,
        before=before,
        after=audit.snapshot(campaign, _AUDIT_FIELDS),
    )
    event_bus.emit(
        session,
        event_type="campaign.paused",
        subject_type="campaign",
        subject_id=campaign.id,
        payload={"name": campaign.name},
        emitted_by=by,
    )
    return campaign


def resume(session: Session, campaign: Campaign, *, by: str) -> Campaign:
    if campaign.status is not CampaignStatus.PAUSED:
        raise PermanentError(f"cannot resume a campaign that is {campaign.status}")
    before = audit.snapshot(campaign, _AUDIT_FIELDS)
    campaign.status = CampaignStatus.ACTIVE
    session.flush()
    audit.record(
        session,
        actor_type=ActorType.HUMAN,
        actor=by,
        action="campaign.resumed",
        subject_type="campaign",
        subject_id=campaign.id,
        before=before,
        after=audit.snapshot(campaign, _AUDIT_FIELDS),
    )
    event_bus.emit(
        session,
        event_type="campaign.resumed",
        subject_type="campaign",
        subject_id=campaign.id,
        payload={"name": campaign.name},
        emitted_by=by,
    )
    return campaign


def kill(session: Session, campaign: Campaign, *, by: str, reason: str) -> Campaign:
    """Terminal. A killed campaign cannot be resumed — start a new one instead."""
    if campaign.status is CampaignStatus.KILLED:
        return campaign
    if campaign.status is CampaignStatus.COMPLETED:
        raise PermanentError("cannot kill a campaign that has already completed")
    before = audit.snapshot(campaign, _AUDIT_FIELDS)
    campaign.status = CampaignStatus.KILLED
    campaign.killed_by = by
    campaign.kill_reason = reason
    campaign.killed_at = db_now(session)
    session.flush()
    audit.record(
        session,
        actor_type=ActorType.HUMAN,
        actor=by,
        action="campaign.killed",
        subject_type="campaign",
        subject_id=campaign.id,
        before=before,
        after=audit.snapshot(campaign, _AUDIT_FIELDS),
        reason=reason,
    )
    event_bus.emit(
        session,
        event_type="campaign.killed",
        subject_type="campaign",
        subject_id=campaign.id,
        payload={"name": campaign.name, "reason": reason},
        emitted_by=by,
    )
    return campaign
