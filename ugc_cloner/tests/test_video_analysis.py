from __future__ import annotations

from ugc_cloner.models import Evidence, Shot
from ugc_cloner.video_analysis import analyze_video


def test_no_shots_gives_unknown_editing_evidence() -> None:
    analysis = analyze_video(source_description="a video happens")
    assert analysis.editing.evidence is Evidence.UNKNOWN
    assert analysis.hook.evidence is Evidence.UNKNOWN


def test_manual_shots_are_observed_and_computed_exactly() -> None:
    shots = [
        Shot(start_seconds=0, end_seconds=2, b_roll=""),
        Shot(start_seconds=2, end_seconds=5, b_roll="product close-up"),
        Shot(start_seconds=5, end_seconds=10, b_roll=""),
    ]
    analysis = analyze_video(source_description="ignored", manual_shots=shots)

    assert analysis.editing.evidence is Evidence.OBSERVED
    assert analysis.editing.total_duration_seconds == 10.0
    # (3 shots - 1 cut boundary... actually cuts = len(shots)-1 = 2) over 10s = 12/min
    assert analysis.editing.cuts_per_minute == 12.0
    assert analysis.editing.broll_percentage == 30.0
    assert analysis.editing.talking_head_percentage == 70.0


def test_llm_hook_classification_parses_first_line() -> None:
    class StubLLM:
        name = "stub"
        is_mock = True

        def complete(self, **kwargs: object) -> object:
            class R:
                text = "curiosity\nOpens on an unexplained result."

            return R()

    analysis = analyze_video(source_description="something happens", llm=StubLLM())  # type: ignore[arg-type]
    assert analysis.hook.hook_type == "curiosity"
    assert analysis.hook.evidence is Evidence.INFERRED


def test_llm_unknown_hook_response_stays_unknown() -> None:
    class StubLLM:
        name = "stub"
        is_mock = True

        def complete(self, **kwargs: object) -> object:
            class R:
                text = "unknown\ninsufficient information"

            return R()

    analysis = analyze_video(source_description="x", llm=StubLLM())  # type: ignore[arg-type]
    assert analysis.hook.hook_type is None
    assert analysis.hook.evidence is Evidence.UNKNOWN
