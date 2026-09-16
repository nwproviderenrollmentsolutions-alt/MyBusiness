"""All ORM models. Importing this package registers every table on Base.metadata."""

from db.models.command import CeoCommand
from db.models.market import Company, Opportunity, Prospect
from db.models.pipeline import Campaign, Conversation, Lead, LeadScore, Message
from db.models.revenue import Customer, Deal, Proposal
from db.models.runtime import AgentRun, AgentTask, Approval, AuditLog, OutboxEvent, SystemFlag
from db.models.support import KpiSnapshot, Suppression

__all__ = [
    "AgentRun",
    "AgentTask",
    "Approval",
    "AuditLog",
    "Campaign",
    "CeoCommand",
    "Company",
    "Conversation",
    "Customer",
    "Deal",
    "KpiSnapshot",
    "Lead",
    "LeadScore",
    "Message",
    "Opportunity",
    "OutboxEvent",
    "Proposal",
    "Prospect",
    "Suppression",
    "SystemFlag",
]
