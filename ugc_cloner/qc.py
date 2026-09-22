"""Phase 9 — quality control.

Deterministic checks, not another LLM call: the two things that must never slip through
are the avatar lock and a fabricated-looking claim, and both are things code can check
exactly rather than ask a model to judge.
"""

from __future__ import annotations

from ugc_cloner.models import (
    AdaptedScript,
    AvatarProfile,
    Evidence,
    GenerationPrompt,
    QCFinding,
    QCResult,
    VideoAnalysis,
)
from ugc_cloner.script import STUB_VOICEOVER_MARKER


def run_quality_control(
    *,
    avatar: AvatarProfile,
    prompts: list[GenerationPrompt],
    script: AdaptedScript,
    video: VideoAnalysis,
) -> QCResult:
    findings: list[QCFinding] = []

    if not avatar.identity_lock.enabled or avatar.identity_lock.replacement_allowed:
        findings.append(
            QCFinding(severity="blocker", message="avatar identity lock is not enforced")
        )

    for prompt in prompts:
        if avatar.name not in prompt.prompt_text:
            findings.append(
                QCFinding(
                    severity="blocker",
                    message=(
                        f"shot {prompt.shot_number} prompt does not reference the "
                        "locked avatar"
                    ),
                )
            )
        missing = [c for c in avatar.negative_constraints if c not in prompt.negative_constraints]
        if missing:
            findings.append(
                QCFinding(
                    severity="blocker",
                    message=(
                        f"shot {prompt.shot_number} prompt is missing negative "
                        f"constraints: {missing}"
                    ),
                )
            )

    if not script.cta.strip():
        findings.append(QCFinding(severity="blocker", message="script has no CTA"))

    if any(STUB_VOICEOVER_MARKER in line.voiceover for line in script.lines):
        findings.append(
            QCFinding(
                severity="warning",
                message=(
                    "voiceover copy is a stub — no LLM provider was connected to "
                    "draft real lines"
                ),
            )
        )

    if video.editing.evidence is not Evidence.OBSERVED:
        findings.append(
            QCFinding(
                severity="warning",
                message=(
                    "editing metrics are not OBSERVED — no shot-by-shot breakdown "
                    "was supplied for the source video"
                ),
            )
        )

    if video.hook.evidence is Evidence.UNKNOWN:
        findings.append(
            QCFinding(
                severity="warning",
                message="hook could not be classified from the given input",
            )
        )

    passed = not any(f.severity == "blocker" for f in findings)
    return QCResult(passed=passed, findings=findings)
