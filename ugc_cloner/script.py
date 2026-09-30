"""Phase 6 — adapt the script to the user's product, brand, and locked avatar."""

from __future__ import annotations

from providers.base import LLMProvider
from ugc_cloner.models import AdaptedScript, AvatarProfile, CreativeBlueprint, ScriptLine

STUB_VOICEOVER_MARKER = "[DRAFT VOICEOVER — connect a real LLM provider to generate copy]"

_DEFAULT_BEATS = ["hook", "problem/context", "demonstration", "payoff", "cta"]


def _draft_line_with_llm(
    llm: LLMProvider, *, beat: str, blueprint: CreativeBlueprint, avatar: AvatarProfile
) -> str:
    response = llm.complete(
        system=(
            "You write one short, spoken voiceover line for a UGC-style vertical video "
            f"ad, in the '{beat}' beat of the script. Speaking style: "
            f"{avatar.personality.speaking_style or 'natural, direct'}, energy: "
            f"{avatar.personality.energy or 'conversational'}. Reply with only the line, "
            "no quotes, no stage directions."
        ),
        prompt=(
            f"Product: {blueprint.product}\n"
            f"Campaign objective: {blueprint.campaign_objective}\n"
            f"Concept: {blueprint.concept}\n"
            f"Framework pattern: {' -> '.join(blueprint.viral_framework.pattern)}"
        ),
        max_tokens=120,
    )
    text = response.text.strip()
    return text or STUB_VOICEOVER_MARKER


def adapt_script(
    *,
    blueprint: CreativeBlueprint,
    avatar: AvatarProfile,
    llm: LLMProvider | None = None,
) -> AdaptedScript:
    beats = blueprint.viral_framework.pattern or _DEFAULT_BEATS
    lines: list[ScriptLine] = []
    span = blueprint.target_duration_seconds / max(1, len(beats))

    for index, beat in enumerate(beats):
        start = round(index * span, 1)
        end = round((index + 1) * span, 1)
        voiceover = (
            _draft_line_with_llm(llm, beat=beat, blueprint=blueprint, avatar=avatar)
            if llm is not None
            else STUB_VOICEOVER_MARKER
        )
        lines.append(
            ScriptLine(
                beat=beat,
                seconds=f"{start}-{end}",
                on_screen_text=None,
                voiceover=voiceover,
            )
        )

    cta = (
        _draft_line_with_llm(llm, beat="cta", blueprint=blueprint, avatar=avatar)
        if llm is not None
        else f"{STUB_VOICEOVER_MARKER} (cta)"
    )
    return AdaptedScript(lines=lines, cta=cta)
