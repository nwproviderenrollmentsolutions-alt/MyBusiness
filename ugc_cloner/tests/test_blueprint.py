from __future__ import annotations

from ugc_cloner.blueprint import build_creative_blueprint, score_creative
from ugc_cloner.models import (
    Evidence,
    HookAnalysis,
    Rating,
    Shot,
    VideoAnalysis,
    ViralFramework,
)


def _framework() -> ViralFramework:
    return ViralFramework(name="Test Framework", pattern=["hook", "demo", "cta"])


def test_unclassified_hook_scores_unknown_not_low() -> None:
    analysis = VideoAnalysis(source_description="x", hook=HookAnalysis(evidence=Evidence.UNKNOWN))
    scorecard = score_creative(analysis)
    assert scorecard.hook_strength.rating is Rating.UNKNOWN


def test_observed_hook_and_broll_score_high() -> None:
    shots = [
        Shot(start_seconds=0, end_seconds=2, on_screen_text="hook!"),
        Shot(start_seconds=2, end_seconds=6, b_roll="product demo", on_screen_text="look at this"),
        Shot(start_seconds=6, end_seconds=9, b_roll="result shot"),
    ]
    analysis = VideoAnalysis(
        source_description="x",
        hook=HookAnalysis(hook_type="curiosity", evidence=Evidence.OBSERVED),
        shots=shots,
    )
    scorecard = score_creative(analysis)
    assert scorecard.hook_strength.rating is Rating.HIGH
    assert scorecard.demonstration_strength.rating is Rating.HIGH
    assert scorecard.format_reusability.rating is Rating.HIGH


def test_blueprint_concept_names_product_and_framework() -> None:
    analysis = VideoAnalysis(source_description="x", hook=HookAnalysis())
    blueprint = build_creative_blueprint(
        video=analysis,
        framework=_framework(),
        product="Replit",
        campaign_objective="drive signups",
    )
    assert "Replit" in blueprint.concept
    assert "Test Framework" in blueprint.concept
    assert blueprint.product == "Replit"
