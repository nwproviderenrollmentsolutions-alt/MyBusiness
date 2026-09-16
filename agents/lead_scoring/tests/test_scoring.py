"""Pure function tests — no database, no agent plumbing."""

from __future__ import annotations

import pytest

from agents.lead_scoring.scoring import (
    DEFAULT_QUALIFYING_THRESHOLD,
    MAX_SCORE,
    ScoringInputs,
    score_lead,
)


def test_ideal_lead_scores_at_the_maximum():
    result = score_lead(
        ScoringInputs(title="Owner", size_band="11-50", has_email=True, has_phone=True)
    )
    assert result.score == MAX_SCORE


def test_worst_case_lead_scores_at_the_minimum():
    result = score_lead(
        ScoringInputs(title=None, size_band=None, has_email=False, has_phone=False)
    )
    # Unknown company size still contributes a small baseline.
    assert result.score == 5


def test_worst_case_is_below_the_qualifying_threshold():
    result = score_lead(
        ScoringInputs(title=None, size_band=None, has_email=False, has_phone=False)
    )
    assert result.score < DEFAULT_QUALIFYING_THRESHOLD


@pytest.mark.parametrize(
    ("title", "expected_points"),
    [
        ("Owner", 50),
        ("Founder & CEO", 50),
        ("General Manager", 45),
        ("Vice President of Sales", 40),
        ("Operations Director", 35),
        ("Office Manager", 25),
        ("Receptionist", 10),
        (None, 0),
    ],
)
def test_title_component_matches_the_strongest_applicable_keyword(title, expected_points):
    result = score_lead(
        ScoringInputs(title=title, size_band=None, has_email=False, has_phone=False)
    )
    assert result.rationale["title"]["points"] == expected_points


@pytest.mark.parametrize(
    ("size_band", "expected_points"),
    [("1-10", 15), ("11-50", 30), ("51-200", 25), ("201-500", 15), ("unrecognized", 5), (None, 5)],
)
def test_size_component(size_band, expected_points):
    result = score_lead(
        ScoringInputs(title=None, size_band=size_band, has_email=False, has_phone=False)
    )
    assert result.rationale["company_size"]["points"] == expected_points


def test_contactability_component_is_additive():
    base = {"title": None, "size_band": None}
    neither = score_lead(ScoringInputs(**base, has_email=False, has_phone=False))
    email_only = score_lead(ScoringInputs(**base, has_email=True, has_phone=False))
    both = score_lead(ScoringInputs(**base, has_email=True, has_phone=True))

    assert email_only.score == neither.score + 10
    assert both.score == neither.score + 20


def test_rationale_explains_every_component():
    result = score_lead(
        ScoringInputs(title="Owner", size_band="11-50", has_email=True, has_phone=False)
    )
    assert set(result.rationale) >= {
        "title",
        "company_size",
        "contactability",
        "total",
        "max_possible",
        "qualifying_threshold",
    }
    assert result.rationale["total"] == result.score


def test_scoring_is_deterministic():
    inputs = ScoringInputs(title="Owner", size_band="11-50", has_email=True, has_phone=True)
    assert score_lead(inputs) == score_lead(inputs)
