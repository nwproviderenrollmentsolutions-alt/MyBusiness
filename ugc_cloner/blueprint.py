"""Phase 4/5 — creative scoring and the creative blueprint.

Scoring is rule-based, not an LLM call — the same reasoning as agents/lead_scoring/
elsewhere in this repo: a rating that feeds a production decision needs to be
reproducible and inspectable, not a fresh guess every run. Every ScoredAttribute carries
the rule that produced it in evidence_note, so a HIGH can always be checked against what
was actually observed in the source video.
"""

from __future__ import annotations

from ugc_cloner.models import (
    CreativeBlueprint,
    CreativeScorecard,
    Evidence,
    Rating,
    ScoredAttribute,
    VideoAnalysis,
    ViralFramework,
)


def _rate(
    condition_high: bool, condition_medium: bool, note_high: str, note_low: str
) -> ScoredAttribute:
    if condition_high:
        return ScoredAttribute(rating=Rating.HIGH, evidence_note=note_high)
    if condition_medium:
        return ScoredAttribute(rating=Rating.MEDIUM, evidence_note=note_high)
    return ScoredAttribute(rating=Rating.LOW, evidence_note=note_low)


def score_creative(video: VideoAnalysis) -> CreativeScorecard:
    has_shots = bool(video.shots)
    hook_known = video.hook.hook_type is not None and video.hook.evidence is not Evidence.UNKNOWN
    broll_shots = [s for s in video.shots if s.b_roll.strip()]
    text_shots = [s for s in video.shots if s.on_screen_text.strip()]
    last_shot_has_cta_cue = bool(
        video.shots and ("cta" in video.shots[-1].on_screen_text.lower() or video.timeline)
    )

    if not hook_known:
        hook_strength = ScoredAttribute(
            rating=Rating.UNKNOWN,
            evidence_note="hook type not classified — no OBSERVED or INFERRED evidence",
        )
    else:
        hook_strength = ScoredAttribute(
            rating=Rating.HIGH if video.hook.evidence is Evidence.OBSERVED else Rating.MEDIUM,
            evidence_note=f"hook classified as '{video.hook.hook_type}' ({video.hook.evidence})",
        )

    return CreativeScorecard(
        hook_strength=hook_strength,
        clarity=_rate(
            len(text_shots) >= max(1, len(video.shots) // 2),
            bool(text_shots),
            "on-screen text present in most shots",
            "little to no on-screen text observed",
        ),
        curiosity=hook_strength,
        demonstration_strength=_rate(
            len(broll_shots) >= 2,
            bool(broll_shots),
            "multiple b-roll/demonstration shots observed",
            "no demonstration or b-roll shots observed",
        ),
        visual_change_frequency=_rate(
            (video.editing.cuts_per_minute or 0) >= 20,
            (video.editing.cuts_per_minute or 0) >= 8,
            f"{video.editing.cuts_per_minute} cuts/min observed",
            "cut rate unknown or low",
        ),
        pacing=_rate(
            has_shots and video.editing.evidence is Evidence.OBSERVED,
            has_shots,
            "shot-level timing observed and used to compute pacing",
            "no shot-level timing available",
        ),
        product_visibility=_rate(
            len(text_shots) >= 1 and has_shots,
            has_shots,
            "product referenced in on-screen text or shot notes",
            "no product reference observed in the source",
        ),
        cta_clarity=_rate(
            last_shot_has_cta_cue,
            has_shots,
            "a closing beat/shot exists for the CTA",
            "no closing beat observed — CTA will need to be authored from scratch",
        ),
        format_reusability=ScoredAttribute(
            rating=Rating.HIGH,
            evidence_note="framework is being deliberately reused by this pipeline",
        ),
    )


def build_creative_blueprint(
    *,
    video: VideoAnalysis,
    framework: ViralFramework,
    product: str,
    campaign_objective: str,
    target_duration_seconds: float = 25.0,
) -> CreativeBlueprint:
    concept = (
        f"Recreate the '{framework.name}' structure for {product}, preserving the "
        f"source's {video.hook.hook_type or 'unclassified'} hook and pacing, spoken "
        "entirely by the locked avatar."
    )
    return CreativeBlueprint(
        concept=concept,
        viral_framework=framework,
        scorecard=score_creative(video),
        product=product,
        campaign_objective=campaign_objective,
        target_duration_seconds=target_duration_seconds,
    )
