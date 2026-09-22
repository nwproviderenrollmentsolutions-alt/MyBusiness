from __future__ import annotations

import pytest

from providers.mock import MockLLMProvider, MockWebResearchProvider
from ugc_cloner.avatar import AvatarLockError, AvatarProfile
from ugc_cloner.pipeline import run_pipeline


def test_pipeline_refuses_without_a_locked_avatar(avatar_dir: object) -> None:
    with pytest.raises(AvatarLockError, match="AVATAR REQUIRED"):
        run_pipeline(
            product="Widget",
            campaign_objective="sell widgets",
            source_description="a demo video",
            product_category="widgets",
            llm=MockLLMProvider(),
            web=MockWebResearchProvider(),
        )


def test_pipeline_runs_end_to_end_with_a_locked_avatar_and_mocks(
    locked_avatar: AvatarProfile,
) -> None:
    package = run_pipeline(
        product="Widget",
        campaign_objective="sell widgets",
        source_description="hooks with a surprising result, demos the widget, ends on a CTA",
        product_category="widgets",
        llm=MockLLMProvider(),
        web=MockWebResearchProvider(),
    )

    assert package.avatar_name == "Test Avatar"
    assert package.qc.passed
    assert package.generation_prompts
    assert all(package.avatar_name in p.prompt_text for p in package.generation_prompts)
    # Mock web research must never surface as a fabricated observed fact.
    assert package.blueprint.viral_framework.evidence != "OBSERVED"
