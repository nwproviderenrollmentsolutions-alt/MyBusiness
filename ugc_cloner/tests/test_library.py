from __future__ import annotations

from pathlib import Path

from providers.mock import MockLLMProvider, MockWebResearchProvider
from ugc_cloner.avatar import AvatarProfile
from ugc_cloner.library import save_framework, save_package
from ugc_cloner.models import ViralFramework
from ugc_cloner.pipeline import run_pipeline


def test_save_framework_writes_labeled_json(library_dir: Path) -> None:
    framework = ViralFramework(name="Stop Doing X", pattern=["a", "b"])
    path = save_framework(framework)
    assert path.exists()
    assert path.parent == library_dir / "frameworks"
    assert "stop-doing-x" in path.name


def test_save_package_writes_json_and_markdown_summary(
    library_dir: Path, locked_avatar: AvatarProfile
) -> None:
    package = run_pipeline(
        product="Widget",
        campaign_objective="sell widgets",
        source_description="a demo video",
        product_category="widgets",
        llm=MockLLMProvider(),
        web=MockWebResearchProvider(),
    )
    json_path = save_package(package, slug="widget-test")
    markdown_path = json_path.with_suffix(".md")
    assert json_path.exists()
    assert markdown_path.exists()
    assert "Widget" in markdown_path.read_text()
