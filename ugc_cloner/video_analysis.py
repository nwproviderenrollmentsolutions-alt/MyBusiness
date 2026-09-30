"""Phase 1 — analyze the reference video.

There is no frame-level computer-vision model wired into this pipeline, so a raw video
file cannot be analyzed directly. Two inputs are supported instead:

* ``manual_shots`` — a shot-by-shot breakdown the caller already has (a transcript with
  timestamps, or notes taken while watching the video). Every field built from this is
  OBSERVED, because a person watched the real video to produce it.
* ``source_description`` — free text (a summary, a transcript with no timestamps). An LLM
  is used, if one is configured, to extract a hook classification and a performance
  framework from it; everything derived this way is INFERRED, and anything the LLM
  declines to infer is UNKNOWN. A mock/no-op LLM yields UNKNOWN across the board rather
  than a guess dressed up as an observation.
"""

from __future__ import annotations

from providers.base import LLMProvider
from ugc_cloner.models import (
    EditingMetrics,
    Evidence,
    HookAnalysis,
    HookType,
    PerformanceProfile,
    Shot,
    TimelineBeat,
    VideoAnalysis,
)

_HOOK_TYPES: tuple[HookType, ...] = (
    "curiosity",
    "shock",
    "contrarian",
    "problem",
    "transformation",
    "demonstration",
    "confession",
    "story",
    "question",
    "unexpected_result",
    "before_after",
    "secret",
    "list",
    "challenge",
)

_PERFORMANCE_FIELDS = (
    "energy_level",
    "speaking_speed",
    "sentence_length",
    "gesture_frequency",
    "facial_expression_frequency",
    "eye_contact",
    "confidence_level",
    "conversational_style",
)


def _editing_metrics_from_shots(shots: list[Shot]) -> EditingMetrics:
    if not shots:
        return EditingMetrics(evidence=Evidence.UNKNOWN)
    total = max(shot.end_seconds for shot in shots) - min(shot.start_seconds for shot in shots)
    if total <= 0:
        return EditingMetrics(evidence=Evidence.UNKNOWN)
    avg_len = sum(shot.end_seconds - shot.start_seconds for shot in shots) / len(shots)
    broll_seconds = sum(
        (shot.end_seconds - shot.start_seconds) for shot in shots if shot.b_roll.strip()
    )
    talking_head_seconds = total - broll_seconds
    return EditingMetrics(
        total_duration_seconds=round(total, 1),
        cuts_per_minute=round((len(shots) - 1) / (total / 60), 1) if total > 0 else None,
        average_shot_length_seconds=round(avg_len, 2),
        broll_percentage=round(100 * broll_seconds / total, 1),
        talking_head_percentage=round(100 * talking_head_seconds / total, 1),
        evidence=Evidence.OBSERVED,
    )


def _classify_hook_with_llm(llm: LLMProvider, source_description: str) -> HookAnalysis:
    options = ", ".join(_HOOK_TYPES)
    response = llm.complete(
        system=(
            "You are a short-form video analyst. Respond with exactly two lines and "
            "nothing else. Line 1: one word from this list that best classifies the "
            f"video's hook: {options}. Line 2: a one-sentence explanation of the "
            "curiosity mechanism or emotional trigger. If the input gives no real basis "
            "for a judgment, answer 'unknown' on line 1 and 'insufficient information' "
            "on line 2."
        ),
        prompt=source_description,
        max_tokens=200,
    )
    lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
    hook_type: HookType | None = None
    mechanism: str | None = None
    if lines:
        candidate = lines[0].strip().lower().rstrip(".")
        if candidate in _HOOK_TYPES:
            hook_type = candidate
    if len(lines) > 1:
        mechanism = lines[1]
    if hook_type is None:
        return HookAnalysis(evidence=Evidence.UNKNOWN)
    return HookAnalysis(
        curiosity_mechanism=mechanism,
        emotional_trigger=mechanism,
        hook_type=hook_type,
        evidence=Evidence.INFERRED,
    )


def _performance_with_llm(llm: LLMProvider, source_description: str) -> PerformanceProfile:
    fields = "\n".join(_PERFORMANCE_FIELDS)
    response = llm.complete(
        system=(
            "You are a short-form video analyst describing presentation STYLE only — "
            "never the presenter's identity or appearance. Reply with exactly one line "
            "per item below, formatted 'field: short phrase'. If the input gives no real "
            f"basis for a field, write 'field: unknown'.\n{fields}"
        ),
        prompt=source_description,
        max_tokens=300,
    )
    values: dict[str, str] = {}
    for line in response.text.strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        if key in _PERFORMANCE_FIELDS:
            values[key] = value.strip()
    return PerformanceProfile(
        **{field: values.get(field, "") for field in _PERFORMANCE_FIELDS}
    )


def analyze_video(
    *,
    source_description: str,
    manual_shots: list[Shot] | None = None,
    timeline: list[TimelineBeat] | None = None,
    llm: LLMProvider | None = None,
) -> VideoAnalysis:
    shots = manual_shots or []
    editing = _editing_metrics_from_shots(shots)

    if llm is not None:
        hook = _classify_hook_with_llm(llm, source_description)
        performance = _performance_with_llm(llm, source_description)
    else:
        hook = HookAnalysis(evidence=Evidence.UNKNOWN)
        performance = PerformanceProfile()

    return VideoAnalysis(
        source_description=source_description,
        hook=hook,
        timeline=timeline or [],
        shots=shots,
        editing=editing,
        performance=performance,
    )
