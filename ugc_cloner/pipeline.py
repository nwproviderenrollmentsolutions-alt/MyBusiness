"""Ties phases 1-9 together end to end. See ugc_cloner/README.md for the full pipeline
diagram; this module is intentionally thin — each phase's logic lives in its own module
so it can be tested and reused independently (e.g. analyzing a video without running the
rest of the pipeline).
"""

from __future__ import annotations

from providers.base import LLMProvider, WebResearchProvider
from ugc_cloner.avatar import require_avatar
from ugc_cloner.blueprint import build_creative_blueprint
from ugc_cloner.models import ProductionPackage, Shot, TimelineBeat
from ugc_cloner.qc import run_quality_control
from ugc_cloner.research import research_viral_formats
from ugc_cloner.script import adapt_script
from ugc_cloner.shots import build_generation_prompts, build_shot_plan, build_voice_prompts
from ugc_cloner.video_analysis import analyze_video


def run_pipeline(
    *,
    product: str,
    campaign_objective: str,
    source_description: str,
    product_category: str,
    llm: LLMProvider,
    web: WebResearchProvider,
    manual_shots: list[Shot] | None = None,
    timeline: list[TimelineBeat] | None = None,
    target_duration_seconds: float = 25.0,
    use_llm_for_analysis: bool = True,
) -> ProductionPackage:
    """Raises ugc_cloner.avatar.AvatarLockError if no locked avatar is on file — this is
    checked first, before any analysis or generation runs."""

    avatar = require_avatar()

    video = analyze_video(
        source_description=source_description,
        manual_shots=manual_shots,
        timeline=timeline,
        llm=llm if use_llm_for_analysis else None,
    )

    frameworks = research_viral_formats(product_category=product_category, web=web, llm=llm)
    framework = frameworks[0]

    blueprint = build_creative_blueprint(
        video=video,
        framework=framework,
        product=product,
        campaign_objective=campaign_objective,
        target_duration_seconds=target_duration_seconds,
    )

    script = adapt_script(blueprint=blueprint, avatar=avatar, llm=llm)
    shot_plans = build_shot_plan(video=video, script=script, blueprint=blueprint)
    generation_prompts = build_generation_prompts(shots=shot_plans, avatar=avatar)
    voice_prompts = build_voice_prompts(shots=shot_plans, avatar=avatar)

    qc = run_quality_control(avatar=avatar, prompts=generation_prompts, script=script, video=video)

    return ProductionPackage(
        blueprint=blueprint,
        script=script,
        shots=shot_plans,
        generation_prompts=generation_prompts,
        voice_prompts=voice_prompts,
        qc=qc,
        avatar_name=avatar.name,
    )
