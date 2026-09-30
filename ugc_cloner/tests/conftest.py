"""ugc_cloner has no database — its tests need only a scratch filesystem, provided by
redirecting the package's path constants into pytest's tmp_path for the duration of each
test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ugc_cloner import avatar, library


@pytest.fixture
def avatar_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "avatar"
    monkeypatch.setattr(avatar, "AVATAR_DIR", root)
    monkeypatch.setattr(avatar, "AVATAR_PROFILE_PATH", root / "avatar-profile.md")
    monkeypatch.setattr(avatar, "REFERENCE_IMAGE_DIR", root / "reference-images")
    monkeypatch.setattr(avatar, "REFERENCE_VIDEO_DIR", root / "reference-video")
    monkeypatch.setattr(avatar, "VOICE_DIR", root / "voice")
    monkeypatch.setattr(avatar, "APPROVED_PROMPTS_DIR", root / "approved-prompts")
    yield root


@pytest.fixture
def locked_avatar(avatar_dir: Path) -> avatar.AvatarProfile:
    """A fully locked, complete avatar profile — the happy-path starting point."""

    avatar.scaffold_avatar_dir()
    text = avatar.AVATAR_PROFILE_PATH.read_text().replace('name: ""', 'name: "Test Avatar"', 1)
    avatar.AVATAR_PROFILE_PATH.write_text(text)
    (avatar.REFERENCE_IMAGE_DIR / "headshot.png").write_bytes(b"fake-image-bytes")
    return avatar.require_avatar()


@pytest.fixture
def library_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "viral-library"
    monkeypatch.setattr(library, "VIRAL_LIBRARY_DIR", root)
    monkeypatch.setattr(library, "FRAMEWORKS_DIR", root / "frameworks")
    monkeypatch.setattr(library, "PACKAGES_DIR", root / "packages")
    yield root
