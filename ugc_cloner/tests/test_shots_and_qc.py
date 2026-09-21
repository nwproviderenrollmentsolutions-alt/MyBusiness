from __future__ import annotations

from ugc_cloner.avatar import AvatarProfile
from ugc_cloner.models import AdaptedScript, CreativeBlueprint, ScriptLine, ViralFramework
from ugc_cloner.qc import run_quality_control
from ugc_cloner.script import STUB_VOICEOVER_MARKER, adapt_script
from ugc_cloner.shots import build_generation_prompts, build_shot_plan
from ugc_cloner.video_analysis import analyze_video


def _blueprint() -> CreativeBlueprint:
    from ugc_cloner.blueprint import build_creative_blueprint

    video = analyze_video(source_description="x")
    framework = ViralFramework(name="Test", pattern=["hook", "demo", "cta"])
    return build_creative_blueprint(
        video=video, framework=framework, product="Widget", campaign_objective="sell widgets"
    )


def test_every_generation_prompt_names_the_avatar_and_carries_negative_constraints(
    locked_avatar: AvatarProfile,
) -> None:
    blueprint = _blueprint()
    script = adapt_script(blueprint=blueprint, avatar=locked_avatar)
    video = analyze_video(source_description="x")
    shots = build_shot_plan(video=video, script=script, blueprint=blueprint)
    prompts = build_generation_prompts(shots=shots, avatar=locked_avatar)

    assert prompts
    for prompt in prompts:
        assert locked_avatar.name in prompt.prompt_text
        assert set(locked_avatar.negative_constraints) == set(prompt.negative_constraints)


def test_qc_passes_for_a_correctly_locked_package(locked_avatar: AvatarProfile) -> None:
    blueprint = _blueprint()
    script = adapt_script(blueprint=blueprint, avatar=locked_avatar)
    video = analyze_video(source_description="x")
    shots = build_shot_plan(video=video, script=script, blueprint=blueprint)
    prompts = build_generation_prompts(shots=shots, avatar=locked_avatar)

    qc = run_quality_control(avatar=locked_avatar, prompts=prompts, script=script, video=video)
    assert qc.passed
    assert any(STUB_VOICEOVER_MARKER in line.voiceover for line in script.lines)
    warnings = [f.message for f in qc.findings if f.severity == "warning"]
    assert any("stub" in message for message in warnings)


def test_qc_blocks_a_prompt_missing_the_avatar_name(locked_avatar: AvatarProfile) -> None:
    script = AdaptedScript(
        lines=[ScriptLine(beat="hook", seconds="0-2", on_screen_text=None, voiceover="hi")],
        cta="try it",
    )
    video = analyze_video(source_description="x")
    from ugc_cloner.models import GenerationPrompt

    tampered_prompt = GenerationPrompt(
        shot_number=1,
        prompt_text="a video of someone talking",  # no avatar name at all
        negative_constraints=list(locked_avatar.negative_constraints),
        avatar_reference_note="",
        duration_seconds=5.0,
    )
    qc = run_quality_control(
        avatar=locked_avatar, prompts=[tampered_prompt], script=script, video=video
    )
    assert not qc.passed
    assert any("does not reference the locked avatar" in f.message for f in qc.findings)
