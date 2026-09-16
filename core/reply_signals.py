"""Deterministic classification of inbound reply sentiment.

Rule-based, not an LLM, for the same reason lead scoring is (agents/lead_scoring/scoring.py):
a decision that closes or advances a deal must be reproducible and inspectable, not a
matter of what a model felt like saying. It is also the only option available to a mock
LLM provider, which returns fixed, content-free marker text regardless of prompt — there
is nothing in that output to classify.

Negative phrases are checked before positive ones, in a separate pass, rather than relying
on ordering within one list: "not interested" contains "interested", so if a positive scan
ran first and matched substrings, a rejection would be misread as a qualifying signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReplySignal = Literal["positive", "negative", "neutral"]

_NEGATIVE_PHRASES: tuple[str, ...] = (
    "not interested",
    "no longer interested",
    "no thanks",
    "not moving forward",
    "went with someone else",
    "went with a competitor",
    "unsubscribe",
    "remove me",
    "stop contacting",
    "please stop",
    "not a fit",
    "isn't a fit",
    "isn't the right fit",
    "pass on this",
)

_POSITIVE_PHRASES: tuple[str, ...] = (
    "i'm interested",
    "im interested",
    "sounds good",
    "let's talk",
    "lets talk",
    "let's schedule",
    "lets schedule",
    "sign me up",
    "let's proceed",
    "lets proceed",
    "move forward",
    "book a time",
    "schedule a call",
    "let's do it",
    "lets do it",
    "approved",
)


@dataclass(frozen=True)
class ReplyClassification:
    signal: ReplySignal
    matched_phrase: str | None
    rationale: str


def classify_reply(body: str) -> ReplyClassification:
    lowered = body.lower()
    for phrase in _NEGATIVE_PHRASES:
        if phrase in lowered:
            return ReplyClassification(
                "negative", phrase, f"reply matched negative phrase {phrase!r}"
            )
    for phrase in _POSITIVE_PHRASES:
        if phrase in lowered:
            return ReplyClassification(
                "positive", phrase, f"reply matched positive phrase {phrase!r}"
            )
    return ReplyClassification("neutral", None, "no recognized positive or negative phrase")
