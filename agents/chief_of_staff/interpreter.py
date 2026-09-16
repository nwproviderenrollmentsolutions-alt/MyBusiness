"""Deterministic interpretation of CEO commands.

This is regex-based pattern matching, not an LLM. Two reasons:

1. **Control.** Which tasks get created is a security-relevant decision. A model freely
   deciding "the CEO probably means create these five tasks" would be an uncontrolled path
   to task creation — exactly what the tool registry and policy engine exist to prevent
   everywhere else. A deterministic interpreter is auditable: given the same text, it
   always produces the same intent.
2. **Honesty about scope.** Milestone 2 has no domain agents to dispatch to yet (Discovery,
   Outreach, etc. arrive in Milestone 3+). Most commands can only be classified and marked
   BLOCKED. There is no benefit to sophisticated NLU pointed at agents that don't exist.

Replacing this with an LLM-based interpreter later is a drop-in change: everything downstream
depends only on ``interpret(text) -> ParsedCommand``, not on how it's implemented.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from db.enums import CommandIntent


class ParsedCommand(BaseModel):
    intent: CommandIntent
    params: dict[str, str | int] = Field(default_factory=dict)


_HELP_TEXT = (
    "I didn't recognize that command. Try one of:\n"
    '  "find our best market opportunity"\n'
    '  "find <N> qualified <segment> prospects"\n'
    '  "start a campaign for <offer>"\n'
    '  "pause / resume / kill campaign <name>"\n'
    '  "show today\'s decisions"\n'
    '  "show status"\n'
    '  "engage / release the kill switch"'
)


def _kill_switch_engage(match: re.Match[str]) -> dict[str, str | int]:
    return {}


def _kill_switch_release(match: re.Match[str]) -> dict[str, str | int]:
    return {}


def _campaign_name(match: re.Match[str]) -> dict[str, str | int]:
    return {"campaign_name": match.group("name").strip().rstrip(".")}


def _find_leads(match: re.Match[str]) -> dict[str, str | int]:
    params: dict[str, str | int] = {"segment": match.group("segment").strip()}
    if match.group("count"):
        params["count"] = int(match.group("count"))
    return params


def _start_campaign(match: re.Match[str]) -> dict[str, str | int]:
    return {"offer": match.group("offer").strip().rstrip(".")}


def _no_params(match: re.Match[str]) -> dict[str, str | int]:
    return {}


_Extractor = Callable[[re.Match[str]], dict[str, str | int]]
_Rule = tuple[re.Pattern[str], CommandIntent, _Extractor]

# Ordered: first match wins. More specific patterns (campaign control, kill switch) are
# listed before the generic ones they could otherwise be swallowed by.
_PATTERNS: list[_Rule] = [
    (
        re.compile(r"\b(engage|activate|trigger|enable)\b.*\bkill\s*switch\b", re.I),
        CommandIntent.ENGAGE_KILL_SWITCH,
        _kill_switch_engage,
    ),
    (
        re.compile(r"\bstop\s+everything\b|\bhalt\s+everything\b|\bemergency\s+stop\b", re.I),
        CommandIntent.ENGAGE_KILL_SWITCH,
        _kill_switch_engage,
    ),
    (
        re.compile(r"\b(release|disable|deactivate)\b.*\bkill\s*switch\b", re.I),
        CommandIntent.RELEASE_KILL_SWITCH,
        _kill_switch_release,
    ),
    (
        re.compile(r"\bresume\s+(?:everything|operations|the\s+system)\b", re.I),
        CommandIntent.RELEASE_KILL_SWITCH,
        _kill_switch_release,
    ),
    (
        re.compile(r"^\s*pause\s+campaign\s+(?P<name>.+)$", re.I),
        CommandIntent.PAUSE_CAMPAIGN,
        _campaign_name,
    ),
    (
        re.compile(r"^\s*resume\s+campaign\s+(?P<name>.+)$", re.I),
        CommandIntent.RESUME_CAMPAIGN,
        _campaign_name,
    ),
    (
        re.compile(r"^\s*(?:kill|stop)\s+campaign\s+(?P<name>.+)$", re.I),
        CommandIntent.KILL_CAMPAIGN,
        _campaign_name,
    ),
    (
        re.compile(r"^\s*start\s+(?:a\s+)?campaign\s+for\s+(?P<offer>.+)$", re.I),
        CommandIntent.START_CAMPAIGN,
        _start_campaign,
    ),
    (
        re.compile(
            r"^\s*find\s+(?P<count>\d+)\s+(?:qualified\s+)?(?P<segment>.+?)\s+"
            r"(?:prospects?|leads?)\b",
            re.I,
        ),
        CommandIntent.FIND_LEADS,
        _find_leads,
    ),
    (
        re.compile(r"\bfind\b.*\bopportunit(?:y|ies)\b", re.I),
        CommandIntent.FIND_OPPORTUNITIES,
        _no_params,
    ),
    (
        re.compile(r"\b(decision|approval)s?\b", re.I),
        CommandIntent.SHOW_DECISIONS,
        _no_params,
    ),
    (
        re.compile(r"\b(status|kpi|pipeline|dashboard|how are we doing)\b", re.I),
        CommandIntent.SHOW_STATUS,
        _no_params,
    ),
]


def interpret(text: str) -> ParsedCommand:
    text = text.strip()
    for pattern, intent, extract in _PATTERNS:
        match = pattern.search(text)
        if match:
            return ParsedCommand(intent=intent, params=extract(match))
    return ParsedCommand(intent=CommandIntent.UNKNOWN)


def help_text() -> str:
    return _HELP_TEXT
