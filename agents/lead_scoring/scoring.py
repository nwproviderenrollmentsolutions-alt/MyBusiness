"""Deterministic, rule-based lead scoring.

Pure functions, no database and no provider calls, so the rubric itself is testable in
isolation from the agent plumbing around it. Rule-based rather than LLM-based on purpose:
a score used to gate a lead into or out of the funnel should be inspectable and
reproducible — the same inputs always produce the same score and the same rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The theoretical ceiling: 50 (title) + 30 (company size) + 20 (contactability).
MAX_SCORE = 100

#: Below this, a lead is disqualified rather than passed downstream. Chosen so that a
#: recognized decision-maker title alone (the single strongest signal) is not sufficient
#: on its own — company fit or contactability must also contribute something.
DEFAULT_QUALIFYING_THRESHOLD = 40

#: Checked in order; first match wins. Keywords that are substrings of another keyword
#: here ("president" is contained in "vice president") must come *after* the more
#: specific phrase, or "Vice President of Sales" would score as if the title were
#: "President" — the more specific match has to get first refusal.
_DECISION_MAKER_WEIGHTS: list[tuple[str, int]] = [
    ("owner", 50),
    ("founder", 50),
    ("ceo", 50),
    ("vice president", 40),
    ("vp", 40),
    ("president", 50),
    ("general manager", 45),
    ("gm", 45),
    ("director", 35),
    ("manager", 25),
]

_SIZE_BAND_WEIGHTS: dict[str, int] = {
    # A very small shop may lack budget; a large one likely has procurement layers a
    # one-person company can't navigate. The middle bands are the sweet spot.
    "1-10": 15,
    "11-50": 30,
    "51-200": 25,
    "201-500": 15,
}
_UNKNOWN_SIZE_BAND_WEIGHT = 5


@dataclass(frozen=True)
class ScoringInputs:
    title: str | None
    size_band: str | None
    has_email: bool
    has_phone: bool


@dataclass(frozen=True)
class ScoreResult:
    score: int
    rationale: dict[str, Any]


def _title_component(title: str | None) -> tuple[int, str]:
    if not title:
        return 0, "no title on file"
    lowered = title.lower()
    for keyword, weight in _DECISION_MAKER_WEIGHTS:
        if keyword in lowered:
            return weight, f"title {title!r} matches decision-maker keyword {keyword!r}"
    return 10, f"title {title!r} present but not a recognized decision-maker role"


def _size_component(size_band: str | None) -> tuple[int, str]:
    if size_band is None:
        return _UNKNOWN_SIZE_BAND_WEIGHT, "company size unknown"
    weight = _SIZE_BAND_WEIGHTS.get(size_band, _UNKNOWN_SIZE_BAND_WEIGHT)
    return weight, f"company size band {size_band!r}"


def _contact_component(has_email: bool, has_phone: bool) -> tuple[int, str]:
    points = (10 if has_email else 0) + (10 if has_phone else 0)
    email_part = "email on file" if has_email else "no email"
    phone_part = "phone on file" if has_phone else "no phone"
    return points, f"{email_part}, {phone_part}"


def score_lead(inputs: ScoringInputs) -> ScoreResult:
    title_points, title_reason = _title_component(inputs.title)
    size_points, size_reason = _size_component(inputs.size_band)
    contact_points, contact_reason = _contact_component(inputs.has_email, inputs.has_phone)
    total = title_points + size_points + contact_points

    rationale = {
        "title": {"points": title_points, "max": 50, "reason": title_reason},
        "company_size": {"points": size_points, "max": 30, "reason": size_reason},
        "contactability": {"points": contact_points, "max": 20, "reason": contact_reason},
        "total": total,
        "max_possible": MAX_SCORE,
        "qualifying_threshold": DEFAULT_QUALIFYING_THRESHOLD,
    }
    return ScoreResult(score=total, rationale=rationale)
