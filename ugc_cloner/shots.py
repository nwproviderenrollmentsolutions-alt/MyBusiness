"""Phase 7/8 — shot-by-shot recreation and vendor-agnostic generation prompts.

Every GenerationPrompt embeds the avatar's identity lock and negative constraints
directly in its text, rather than trusting a downstream caller to remember to apply them
separately. This is the mechanical enforcement of the spec's CORE RULE at the one point
where a real vendor call would actually happen.
"""

from __future__ import annotations

from ugc_cloner.models import (
    AdaptedScript,
    AvatarProfile,
    CreativeBlueprint,
    GenerationPrompt,
    ShotPlan,
    VideoAnalysis,
    VoicePrompt,
)


def build_shot_plan(
    *, video: VideoAnalysis, script: AdaptedScript, blueprint: CreativeBlueprint
) -> list[ShotPlan]:
    beats = blueprint.viral_framework.pattern or [line.beat for line in script.lines]
    span = blueprint.target_duration_seconds / max(1, len(beats))
    source_shots = video.shots

    plans: list[ShotPlan] = []
    for index, beat in enumerate(beats):
        line = script.lines[index] if index < len(script.lines) else None
        source = source_shots[index] if index < len(source_shots) else None
        start = round(index * span, 1)
        end = round((index + 1) * span, 1)
        plans.append(
            ShotPlan(
                shot_number=index + 1,
                beat=beat,
                start_seconds=start,
                end_seconds=end,
                camera_angle=source.camera_angle if source else "medium, eye-level",
                framing=source.framing if source else "vertical 9:16, chest-up",
                action=(
                    f"Avatar delivers the '{beat}' beat"
                    + (f": {source.gesture}" if source and source.gesture else "")
                ),
                on_screen_text=line.on_screen_text if line else None,
                voiceover_line=line.voiceover if line else None,
            )
        )
    return plans


def _avatar_reference_note(avatar: AvatarProfile) -> str:
    assets = avatar.reference_image_paths + avatar.reference_video_paths
    if assets:
        return f"Use the approved avatar reference: {assets[0]} (identity locked)."
    return "Use the approved avatar reference on file (identity locked)."


def build_generation_prompts(
    *, shots: list[ShotPlan], avatar: AvatarProfile
) -> list[GenerationPrompt]:
    reference_note = _avatar_reference_note(avatar)
    prompts: list[GenerationPrompt] = []
    for shot in shots:
        text_overlay = f' On-screen text: "{shot.on_screen_text}".' if shot.on_screen_text else ""
        wardrobe = avatar.wardrobe.default or "the avatar's default approved wardrobe"
        prompt_text = (
            f"Presenter: {avatar.name} (the CEO's approved AI clone — this exact "
            f"identity, no substitution). {reference_note} Wardrobe: {wardrobe}. "
            f"Camera: {shot.camera_angle}, {shot.framing}. Action: {shot.action}."
            f"{text_overlay} Duration: {shot.end_seconds - shot.start_seconds:.1f}s."
        )
        prompts.append(
            GenerationPrompt(
                shot_number=shot.shot_number,
                prompt_text=prompt_text,
                negative_constraints=list(avatar.negative_constraints),
                avatar_reference_note=reference_note,
                duration_seconds=round(shot.end_seconds - shot.start_seconds, 1),
            )
        )
    return prompts


def build_voice_prompts(*, shots: list[ShotPlan], avatar: AvatarProfile) -> list[VoicePrompt]:
    return [
        VoicePrompt(
            shot_number=shot.shot_number,
            text=shot.voiceover_line or "",
            voice_id=avatar.voice.voice_id or None,
        )
        for shot in shots
        if shot.voiceover_line
    ]
