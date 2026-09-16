from __future__ import annotations

import pytest

from agents.chief_of_staff.interpreter import interpret
from db.enums import CommandIntent


@pytest.mark.parametrize(
    ("text", "intent", "params"),
    [
        (
            "Find 100 qualified HVAC prospects.",
            CommandIntent.FIND_LEADS,
            {"segment": "HVAC", "count": 100},
        ),
        ("Find our best market opportunity.", CommandIntent.FIND_OPPORTUNITIES, {}),
        (
            "Start a campaign for this approved offer.",
            CommandIntent.START_CAMPAIGN,
            {"offer": "this approved offer"},
        ),
        ("Show me today's most important decisions.", CommandIntent.SHOW_DECISIONS, {}),
        ("What's our status?", CommandIntent.SHOW_STATUS, {}),
        (
            "Pause campaign Q1 HVAC Outreach",
            CommandIntent.PAUSE_CAMPAIGN,
            {"campaign_name": "Q1 HVAC Outreach"},
        ),
        (
            "Resume campaign Q1 HVAC Outreach",
            CommandIntent.RESUME_CAMPAIGN,
            {"campaign_name": "Q1 HVAC Outreach"},
        ),
        (
            "Kill campaign Q1 HVAC Outreach.",
            CommandIntent.KILL_CAMPAIGN,
            {"campaign_name": "Q1 HVAC Outreach"},
        ),
        ("Engage the kill switch", CommandIntent.ENGAGE_KILL_SWITCH, {}),
        ("Stop everything right now", CommandIntent.ENGAGE_KILL_SWITCH, {}),
        ("Release the kill switch", CommandIntent.RELEASE_KILL_SWITCH, {}),
        ("check for replies", CommandIntent.CHECK_REPLIES, {}),
        ("Any new replies?", CommandIntent.CHECK_REPLIES, {}),
        ("check the inbox", CommandIntent.CHECK_REPLIES, {}),
        ("asdkjfh nonsense gibberish", CommandIntent.UNKNOWN, {}),
    ],
)
def test_interpret_matches_expected_intent(text, intent, params):
    parsed = interpret(text)
    assert parsed.intent is intent
    assert parsed.params == params


def test_kill_switch_takes_priority_over_campaign_kill_when_both_words_present():
    """'kill switch' and 'kill campaign' share the word 'kill' but must never cross-match."""
    assert interpret("engage the kill switch").intent is CommandIntent.ENGAGE_KILL_SWITCH
    assert interpret("kill campaign west-coast").intent is CommandIntent.KILL_CAMPAIGN


def test_find_leads_without_a_count_still_classifies_as_unknown_or_leads():
    """No digit means the FIND_LEADS pattern can't extract a count, so it falls through."""
    parsed = interpret("find some good prospects")
    assert parsed.intent is not CommandIntent.FIND_LEADS


def test_interpretation_is_deterministic():
    text = "Find 50 qualified dental clinic leads"
    assert interpret(text) == interpret(text)


def test_leading_and_trailing_whitespace_is_ignored():
    parsed = interpret("   Find our best market opportunity.   ")
    assert parsed.intent is CommandIntent.FIND_OPPORTUNITIES


def test_empty_text_is_unknown():
    assert interpret("").intent is CommandIntent.UNKNOWN
    assert interpret("   ").intent is CommandIntent.UNKNOWN
