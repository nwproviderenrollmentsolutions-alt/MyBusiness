"""Pure function tests — no database, no agent plumbing."""

from __future__ import annotations

import pytest

from core.reply_signals import classify_reply


def test_expressed_interest_is_positive():
    result = classify_reply("MOCK: yes, I'm interested, let's talk")
    assert result.signal == "positive"


def test_explicit_rejection_is_negative():
    result = classify_reply("MOCK: no thanks, not interested right now")
    assert result.signal == "negative"


def test_not_interested_is_not_misread_as_interested():
    """'not interested' contains 'interested' — the negative pass must win."""
    result = classify_reply("Thanks, but not interested at this time.")
    assert result.signal == "negative"


def test_unrelated_reply_is_neutral():
    result = classify_reply("Can you resend the attachment from last week?")
    assert result.signal == "neutral"


@pytest.mark.parametrize(
    "body",
    [
        "Sounds good, let's proceed.",
        "Great, sign me up!",
        "Approved — let's move forward.",
        "Can we book a time to discuss further?",
    ],
)
def test_positive_phrases_are_recognized(body: str):
    assert classify_reply(body).signal == "positive"


@pytest.mark.parametrize(
    "body",
    [
        "We went with a competitor, sorry.",
        "Please unsubscribe me from this list.",
        "This isn't a fit for us right now.",
    ],
)
def test_negative_phrases_are_recognized(body: str):
    assert classify_reply(body).signal == "negative"


def test_neutral_result_has_no_matched_phrase():
    result = classify_reply("What's your availability next week?")
    assert result.matched_phrase is None
