"""Tests for strata.env.filesystem — sandboxed file operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from strata.core.config import SandboxConfig
from strata.core.errors import SandboxViolationError
from strata.core.sandbox import SandboxGuard
from strata.env.filesystem import SandboxedFileSystemAdapter


def _make_fs(tmp_path: Path) -> SandboxedFileSystemAdapter:
    sb = tmp_path / "sandbox"
    sb.mkdir()
    trash = tmp_path / "trash"
    trash.mkdir()
    guard = SandboxGuard(
        SandboxConfig(
            enabled=True,
            root=str(sb),
            read_only_paths=(),
            ask_for_permission=True,
        )
    )
    return SandboxedFileSystemAdapter(guard, str(trash))


class TestReadWriteWithinSandbox:
    def test_write_then_read(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        p = str(tmp_path / "sandbox" / "test.txt")
        fs.write_file(p, "hello world")
        content = fs.read_file(p)
        assert content == "hello world"


class TestPathTraversalBlocked:
    def test_dotdot_escape(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        with pytest.raises(SandboxViolationError):
            fs.read_file(str(tmp_path / "sandbox" / ".." / ".." / "etc" / "passwd"))


class TestSymlinkEscape:
    def test_symlink_outside_blocked(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        sb = tmp_path / "sandbox"
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        link = sb / "link"
        link.symlink_to(outside / "secret.txt")

        with pytest.raises(SandboxViolationError):
            fs.read_file(str(link))


class TestMoveToTrash:
    def test_delete_moves_to_trash(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        p = str(tmp_path / "sandbox" / "delete_me.txt")
        fs.write_file(p, "bye")
        trash_path = fs.move_to_trash(p)
        assert not Path(p).exists()
        assert Path(trash_path).exists()
