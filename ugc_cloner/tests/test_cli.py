from __future__ import annotations

from pathlib import Path

import pytest

from ugc_cloner import cli
from ugc_cloner.avatar import AvatarProfile


def test_avatar_status_exits_nonzero_when_missing(
    avatar_dir: object, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.cmd_avatar_status(cli.build_parser().parse_args(["avatar-status"]))
    assert exc_info.value.code == 1
    assert "init-avatar" in capsys.readouterr().out


def test_avatar_status_succeeds_when_locked(
    locked_avatar: AvatarProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.cmd_avatar_status(cli.build_parser().parse_args(["avatar-status"]))
    assert "locked and ready" in capsys.readouterr().out


def test_build_command_writes_a_package(
    locked_avatar: AvatarProfile, library_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli.build_parser().parse_args(
        [
            "build",
            "--product",
            "Widget",
            "--objective",
            "sell widgets",
            "--description",
            "hooks hard, demos the widget, ends on a CTA",
            "--category",
            "widgets",
            "--out",
            "widget-cli-test",
        ]
    )
    cli.cmd_build(args)
    out = capsys.readouterr().out
    assert "QC: PASSED" in out
    assert (library_dir / "packages" / "widget-cli-test.json").exists()


def test_read_text_arg_supports_at_file(tmp_path: Path) -> None:
    path = tmp_path / "transcript.txt"
    path.write_text("hello there")
    assert cli._read_text_arg(f"@{path}") == "hello there"
    assert cli._read_text_arg("literal text") == "literal text"
