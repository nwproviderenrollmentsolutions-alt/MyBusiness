"""Persists frameworks and production packages to viral-library/, per the spec's "build a
library of frameworks" instruction. File-based, like avatar/ — this pipeline has no
database tables of its own (see ugc_cloner/README.md for why)."""

from __future__ import annotations

import re
from pathlib import Path

from config.settings import REPO_ROOT
from ugc_cloner.models import ProductionPackage, ViralFramework

VIRAL_LIBRARY_DIR = REPO_ROOT / "viral-library"
FRAMEWORKS_DIR = VIRAL_LIBRARY_DIR / "frameworks"
PACKAGES_DIR = VIRAL_LIBRARY_DIR / "packages"


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def save_framework(framework: ViralFramework) -> Path:
    FRAMEWORKS_DIR.mkdir(parents=True, exist_ok=True)
    path = FRAMEWORKS_DIR / f"{_slugify(framework.name)}.json"
    path.write_text(framework.model_dump_json(indent=2))
    return path


def save_package(package: ProductionPackage, *, slug: str) -> Path:
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    json_path = PACKAGES_DIR / f"{slug}.json"
    json_path.write_text(package.model_dump_json(indent=2))

    summary_lines = [
        f"# {package.blueprint.product} — {package.blueprint.viral_framework.name}",
        "",
        f"Avatar: {package.avatar_name}",
        f"QC: {'PASSED' if package.qc.passed else 'FAILED'}",
        "",
        "## Script",
        "",
    ]
    for line in package.script.lines:
        summary_lines.append(f"- **{line.beat}** ({line.seconds}s): {line.voiceover}")
    summary_lines += ["", f"**CTA:** {package.script.cta}", "", "## QC findings", ""]
    if package.qc.findings:
        for finding in package.qc.findings:
            summary_lines.append(f"- [{finding.severity.upper()}] {finding.message}")
    else:
        summary_lines.append("- none")

    markdown_path = PACKAGES_DIR / f"{slug}.md"
    markdown_path.write_text("\n".join(summary_lines) + "\n")
    return json_path
