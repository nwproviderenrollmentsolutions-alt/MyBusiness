from __future__ import annotations

import pytest

from ugc_cloner import avatar
from ugc_cloner.models import DEFAULT_NEGATIVE_CONSTRAINTS


def test_no_profile_is_not_locked(avatar_dir: object) -> None:
    assert avatar.load_avatar_profile() is None
    with pytest.raises(avatar.AvatarLockError, match="AVATAR REQUIRED"):
        avatar.require_avatar()


def test_scaffold_creates_blank_template(avatar_dir: object) -> None:
    avatar.scaffold_avatar_dir()
    profile = avatar.load_avatar_profile()
    assert profile is not None
    assert profile.name == ""
    assert profile.identity_lock.enabled is True
    assert profile.identity_lock.replacement_allowed is False
    assert not avatar.is_locked_and_complete(profile)


def test_blank_name_blocks_the_lock(avatar_dir: object) -> None:
    avatar.scaffold_avatar_dir()
    (avatar.REFERENCE_IMAGE_DIR / "headshot.png").write_bytes(b"fake")
    with pytest.raises(avatar.AvatarLockError):
        avatar.require_avatar()


def test_missing_reference_assets_blocks_the_lock(avatar_dir: object) -> None:
    avatar.scaffold_avatar_dir()
    text = avatar.AVATAR_PROFILE_PATH.read_text().replace('name: ""', 'name: "Ada"', 1)
    avatar.AVATAR_PROFILE_PATH.write_text(text)
    with pytest.raises(avatar.AvatarLockError):
        avatar.require_avatar()


def test_replacement_allowed_blocks_the_lock(avatar_dir: object) -> None:
    avatar.scaffold_avatar_dir()
    text = avatar.AVATAR_PROFILE_PATH.read_text()
    text = text.replace('name: ""', 'name: "Ada"', 1)
    text = text.replace("replacement_allowed: false", "replacement_allowed: true", 1)
    avatar.AVATAR_PROFILE_PATH.write_text(text)
    (avatar.REFERENCE_IMAGE_DIR / "headshot.png").write_bytes(b"fake")
    with pytest.raises(avatar.AvatarLockError):
        avatar.require_avatar()


def test_complete_profile_is_locked(locked_avatar: avatar.AvatarProfile) -> None:
    assert locked_avatar.name == "Test Avatar"
    assert locked_avatar.reference_image_paths
    assert set(DEFAULT_NEGATIVE_CONSTRAINTS).issubset(locked_avatar.negative_constraints)
