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


class TestRestoreFromTrash:
    def test_restore_round_trip(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        p = str(tmp_path / "sandbox" / "restore_me.txt")
        fs.write_file(p, "back")
        tp = fs.move_to_trash(p)
        fs.restore_from_trash(tp)
        assert Path(p).read_text() == "back"
        assert not Path(tp).exists()

    def test_restore_rejects_path_outside_trash(self, tmp_path: Path) -> None:
        fs = _make_fs(tmp_path)
        forged = tmp_path / "forged.txt"
        forged.write_text("x")
        with pytest.raises(SandboxViolationError):
            fs.restore_from_trash(str(forged))

    def test_restore_rejects_sidecar_original_outside_sandbox(self, tmp_path: Path) -> None:
        import json

        fs = _make_fs(tmp_path)
        p = str(tmp_path / "sandbox" / "victim.txt")
        fs.write_file(p, "legit")
        tp = fs.move_to_trash(p)
        sidecar = Path(tp + ".meta.json")
        meta = json.loads(sidecar.read_text())
        meta["original_path"] = str(tmp_path / "outside.txt")
        sidecar.write_text(json.dumps(meta))
        with pytest.raises(SandboxViolationError):
            fs.restore_from_trash(tp)

    def test_restore_rejects_missing_original_path(self, tmp_path: Path) -> None:
        import json

        fs = _make_fs(tmp_path)
        p = str(tmp_path / "sandbox" / "foo.txt")
        fs.write_file(p, "x")
        tp = fs.move_to_trash(p)
        sidecar = Path(tp + ".meta.json")
        sidecar.write_text(json.dumps({"trashed_at": 0}))
        with pytest.raises(SandboxViolationError):
            fs.restore_from_trash(tp)
