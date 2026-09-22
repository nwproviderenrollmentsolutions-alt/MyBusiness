"""AI UGC Viral Cloner CLI.

    python -m ugc_cloner.cli init-avatar
    python -m ugc_cloner.cli avatar-status
    python -m ugc_cloner.cli build --product "..." --objective "..." --description "..." \\
        --category "..." [--shots shots.json] [--out my-slug]
    python -m ugc_cloner.cli render <viral-library/packages/my-slug.json>

Nothing here needs a database or the Postgres-backed agent runtime the rest of this repo
uses — see ugc_cloner/README.md for why. LLM_PROVIDER / WEB_RESEARCH_PROVIDER /
VIDEO_GEN_PROVIDER / VOICE_PROVIDER in .env select real vs. mock exactly as they do for
the rest of the system (providers/factory.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from providers.factory import (
    get_llm_provider,
    get_video_gen_provider,
    get_voice_provider,
    get_web_research_provider,
)
from ugc_cloner.avatar import (
    AVATAR_DIR,
    AVATAR_PROFILE_PATH,
    AvatarLockError,
    load_avatar_profile,
    require_avatar,
    scaffold_avatar_dir,
)
from ugc_cloner.library import save_package
from ugc_cloner.models import ProductionPackage, Shot, TimelineBeat
from ugc_cloner.pipeline import run_pipeline


def _read_text_arg(value: str) -> str:
    """Supports "@path/to/file" the same way curl does, so a long transcript doesn't have
    to be typed on the command line."""

    if value.startswith("@"):
        return Path(value[1:]).read_text()
    return value


def cmd_init_avatar(_: argparse.Namespace) -> None:
    scaffold_avatar_dir()
    print(f"Avatar scaffolding ready at {AVATAR_DIR}/")
    print(
        f"Fill in {AVATAR_PROFILE_PATH.name} and add at least one file under "
        "reference-images/ or reference-video/ before running 'build'."
    )


def cmd_avatar_status(_: argparse.Namespace) -> None:
    from ugc_cloner.avatar import is_locked_and_complete

    profile = load_avatar_profile()
    if profile is None:
        print("No avatar profile found. Run: python -m ugc_cloner.cli init-avatar")
        sys.exit(1)
    if not is_locked_and_complete(profile):
        print("Avatar profile exists but is not ready:")
        if not profile.identity_lock.enabled or profile.identity_lock.replacement_allowed:
            print("  - identity_lock.enabled must be true and replacement_allowed must be false")
        if not profile.name.strip():
            print("  - avatar_identity.name is blank")
        if not (profile.reference_image_paths or profile.reference_video_paths):
            print("  - no files under reference-images/ or reference-video/")
        sys.exit(1)
    print(f"Avatar '{profile.name}' is locked and ready.")
    print(f"  reference images: {len(profile.reference_image_paths)}")
    print(f"  reference videos: {len(profile.reference_video_paths)}")


def _load_manual_shots(path: Path) -> tuple[list[Shot], list[TimelineBeat]]:
    data = json.loads(path.read_text())
    shots = [Shot.model_validate(item) for item in data.get("shots", [])]
    timeline = [TimelineBeat.model_validate(item) for item in data.get("timeline", [])]
    return shots, timeline


def cmd_build(args: argparse.Namespace) -> None:
    try:
        require_avatar()
    except AvatarLockError as exc:
        print(str(exc))
        sys.exit(1)

    manual_shots: list[Shot] = []
    timeline: list[TimelineBeat] = []
    if args.shots:
        manual_shots, timeline = _load_manual_shots(Path(args.shots))

    package = run_pipeline(
        product=args.product,
        campaign_objective=args.objective,
        source_description=_read_text_arg(args.description),
        product_category=args.category or args.product,
        llm=get_llm_provider(),
        web=get_web_research_provider(),
        manual_shots=manual_shots or None,
        timeline=timeline or None,
    )

    slug = args.out or args.product.lower().replace(" ", "-")
    json_path = save_package(package, slug=slug)
    print(f"Wrote {json_path} (and matching .md summary)")
    print(f"QC: {'PASSED' if package.qc.passed else 'FAILED'}")
    for finding in package.qc.findings:
        print(f"  [{finding.severity.upper()}] {finding.message}")
    if not package.qc.passed:
        sys.exit(1)


def cmd_render(args: argparse.Namespace) -> None:
    """Sends every generation prompt to the configured VideoGenProvider/VoiceProvider.
    Only "mock" is implemented today (see providers/factory.py) — this queues nothing
    real until a real vendor adapter is added and VIDEO_GEN_PROVIDER/VOICE_PROVIDER are
    set in .env."""

    package = ProductionPackage.model_validate_json(Path(args.package).read_text())
    video_gen = get_video_gen_provider()
    voice = get_voice_provider()

    reference_url = None  # no real vendor uploads a reference asset yet — see README.

    for prompt in package.generation_prompts:
        asset = video_gen.generate_shot(
            prompt=prompt.prompt_text,
            avatar_reference_url=reference_url,
            duration_seconds=prompt.duration_seconds,
        )
        print(
            f"shot {prompt.shot_number}: {asset.provider} -> "
            f"{asset.status} ({asset.provider_job_id})"
        )

    for vp in package.voice_prompts:
        voice_asset = voice.synthesize(text=vp.text, voice_id=vp.voice_id)
        print(
            f"voice {vp.shot_number}: {voice_asset.provider} -> "
            f"{voice_asset.status} ({voice_asset.provider_job_id})"
        )

    if video_gen.is_mock or voice.is_mock:
        print(
            "\nNo real render happened — VIDEO_GEN_PROVIDER/VOICE_PROVIDER are 'mock'. "
            "See .env.example and ugc_cloner/README.md to connect a real vendor."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ugc_cloner", description="AI UGC Viral Cloner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_avatar = subparsers.add_parser(
        "init-avatar", help="scaffold avatar/ and the profile template"
    )
    init_avatar.set_defaults(func=cmd_init_avatar)

    avatar_status = subparsers.add_parser(
        "avatar-status", help="check whether the avatar lock is satisfied"
    )
    avatar_status.set_defaults(func=cmd_avatar_status)

    build = subparsers.add_parser(
        "build", help="run the full pipeline and write a production package"
    )
    build.add_argument("--product", required=True)
    build.add_argument("--objective", required=True)
    build.add_argument(
        "--description",
        required=True,
        help="reference video summary/transcript, or @path/to/file",
    )
    build.add_argument("--category", help="defaults to --product")
    build.add_argument(
        "--shots", help="path to a JSON shot-by-shot breakdown (see ugc_cloner/README.md)"
    )
    build.add_argument(
        "--out", help="output slug under viral-library/packages/ (defaults to --product)"
    )
    build.set_defaults(func=cmd_build)

    render = subparsers.add_parser(
        "render", help="send generation prompts to the video/voice providers"
    )
    render.add_argument("package", help="path to a viral-library/packages/<slug>.json file")
    render.set_defaults(func=cmd_render)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
