"""The avatar lock: the CEO's approved AI clone is the only human identity this pipeline
is allowed to generate.

``avatar/avatar-profile.md`` is the source of truth (a human-editable Markdown file with
one fenced ``yaml`` block — see AVATAR_PROFILE_TEMPLATE below). Nothing in this package
invents a presenter when that file is missing, incomplete, or has no reference assets: it
raises ``AvatarLockError`` instead, whose message is the exact report the spec requires.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from config.settings import REPO_ROOT
from ugc_cloner.models import AvatarProfile

__all__ = [
    "AVATAR_DIR",
    "AVATAR_PROFILE_PATH",
    "AVATAR_PROFILE_TEMPLATE",
    "AVATAR_REQUIRED_MESSAGE",
    "AvatarLockError",
    "AvatarProfile",
    "is_locked_and_complete",
    "load_avatar_profile",
    "require_avatar",
    "scaffold_avatar_dir",
]

AVATAR_DIR = REPO_ROOT / "avatar"
AVATAR_PROFILE_PATH = AVATAR_DIR / "avatar-profile.md"
REFERENCE_IMAGE_DIR = AVATAR_DIR / "reference-images"
REFERENCE_VIDEO_DIR = AVATAR_DIR / "reference-video"
VOICE_DIR = AVATAR_DIR / "voice"
APPROVED_PROMPTS_DIR = AVATAR_DIR / "approved-prompts"

AVATAR_REQUIRED_MESSAGE = (
    "AVATAR REQUIRED: The approved AI clone reference is missing. Upload or connect the "
    "user's approved avatar reference before generating the presenter."
)

_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)

AVATAR_PROFILE_TEMPLATE = """\
# Avatar profile

This file is the source of truth for the locked AI clone identity. The AI UGC Viral
Cloner refuses to generate a presenter until `identity_lock.enabled` is `true`,
`identity_lock.replacement_allowed` is `false`, `avatar_identity.name` is filled in, and
at least one file has been added under `reference-images/` or `reference-video/`.

```yaml
avatar_identity:
  name: ""

identity_lock:
  enabled: true
  replacement_allowed: false

appearance:
  face: ""
  hair: ""
  skin: ""
  eyes: ""
  facial_features: ""
  body_type: ""
  age_appearance: ""

wardrobe:
  default: ""
  approved_variations: []

voice:
  provider: ""
  voice_id: ""
  characteristics: ""

personality:
  energy: ""
  speaking_style: ""
  facial_expression_style: ""
  gesture_style: ""

camera:
  preferred_framing: ""
  preferred_angles: ""
  preferred_lens_style: ""

negative_constraints:
  - different person
  - different face
  - altered identity
  - random influencer
  - stock actor
  - celebrity likeness
```
"""

_KEEP_FILE = ".gitkeep"


class AvatarLockError(Exception):
    """Raised in place of ever inventing a presenter. Always carries
    AVATAR_REQUIRED_MESSAGE verbatim so callers can surface it unchanged."""

    def __init__(self, message: str = AVATAR_REQUIRED_MESSAGE) -> None:
        super().__init__(message)


def scaffold_avatar_dir() -> None:
    """Creates avatar/ and its subdirectories with the profile template, if not already
    present. Never overwrites an existing profile."""

    for directory in (
        AVATAR_DIR,
        REFERENCE_IMAGE_DIR,
        REFERENCE_VIDEO_DIR,
        VOICE_DIR,
        APPROVED_PROMPTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        keep = directory / _KEEP_FILE
        if directory is not AVATAR_DIR and not any(
            p for p in directory.iterdir() if p.name != _KEEP_FILE
        ):
            keep.touch()

    if not AVATAR_PROFILE_PATH.exists():
        AVATAR_PROFILE_PATH.write_text(AVATAR_PROFILE_TEMPLATE)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        # Path constants can be monkeypatched outside the repo root (tests use a tmp
        # directory) — fall back to the absolute path rather than failing.
        return str(path)


def _reference_assets(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        _display_path(p)
        for p in directory.iterdir()
        if p.is_file() and p.name != _KEEP_FILE and not p.name.startswith(".")
    )


def _parse_profile_yaml(markdown_text: str) -> dict[str, object]:
    match = _YAML_BLOCK.search(markdown_text)
    if match is None:
        return {}
    loaded = yaml.safe_load(match.group(1))
    return loaded if isinstance(loaded, dict) else {}


def load_avatar_profile() -> AvatarProfile | None:
    """Returns None when there is no profile file at all (never raises for that case —
    callers that must have a locked avatar call require_avatar() instead)."""

    if not AVATAR_PROFILE_PATH.exists():
        return None

    raw = _parse_profile_yaml(AVATAR_PROFILE_PATH.read_text())
    identity = raw.get("avatar_identity") or {}
    name = identity.get("name", "") if isinstance(identity, dict) else ""

    try:
        profile = AvatarProfile.model_validate(
            {
                "name": name,
                "identity_lock": raw.get("identity_lock", {}),
                "appearance": raw.get("appearance", {}),
                "wardrobe": raw.get("wardrobe", {}),
                "voice": raw.get("voice", {}),
                "personality": raw.get("personality", {}),
                "camera": raw.get("camera", {}),
                "negative_constraints": raw.get("negative_constraints", []),
            }
        )
    except ValidationError as exc:
        raise AvatarLockError(
            f"{AVATAR_REQUIRED_MESSAGE} (avatar-profile.md failed to parse: {exc})"
        ) from exc

    profile.reference_image_paths = _reference_assets(REFERENCE_IMAGE_DIR)
    profile.reference_video_paths = _reference_assets(REFERENCE_VIDEO_DIR)
    return profile


def is_locked_and_complete(profile: AvatarProfile | None) -> bool:
    if profile is None:
        return False
    if not profile.identity_lock.enabled or profile.identity_lock.replacement_allowed:
        return False
    if not profile.name.strip():
        return False
    return bool(profile.reference_image_paths or profile.reference_video_paths)


def require_avatar() -> AvatarProfile:
    """The single gate every generation-producing code path in this package must call
    first. Raises AvatarLockError (spec's exact report) rather than inventing a
    presenter."""

    profile = load_avatar_profile()
    if not is_locked_and_complete(profile):
        raise AvatarLockError()
    assert profile is not None  # is_locked_and_complete() already excluded None
    return profile
