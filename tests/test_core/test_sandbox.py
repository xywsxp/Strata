"""Tests for strata.core.sandbox — SandboxGuard path checking."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from strata.core.config import SandboxConfig
from strata.core.errors import SandboxViolationError
from strata.core.sandbox import SandboxGuard


def _make_guard(tmp_path: Path) -> SandboxGuard:
    return SandboxGuard(
        SandboxConfig(
            enabled=True,
            root=str(tmp_path / "sandbox"),
            read_only_paths=(str(tmp_path / "readonly"),),
            ask_for_permission=True,
        )
    )


class TestNormalPath:
    def test_normal_path_passes(self, tmp_path: Path) -> None:
        sb = tmp_path / "sandbox"
        sb.mkdir()
        guard = _make_guard(tmp_path)
        result = guard.check_path(str(sb / "file.txt"))
        assert os.path.isabs(result)
        assert "sandbox" in result

    def test_is_within_sandbox(self, tmp_path: Path) -> None:
        sb = tmp_path / "sandbox"
        sb.mkdir()
        guard = _make_guard(tmp_path)
        assert guard.is_within_sandbox(str(sb / "file.txt"))


class TestDotDotEscape:
    def test_dotdot_escape_blocked(self, tmp_path: Path) -> None:
        sb = tmp_path / "sandbox"
        sb.mkdir()
        guard = _make_guard(tmp_path)
        with pytest.raises(SandboxViolationError, match="outside sandbox"):
            guard.check_path(str(sb / ".." / ".." / "etc" / "passwd"))


class TestSymlinkEscape:
    def test_symlink_escape_blocked(self, tmp_path: Path) -> None:
        sb = tmp_path / "sandbox"
        sb.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = sb / "escape_link"
        link.symlink_to(outside)
        guard = _make_guard(tmp_path)
        with pytest.raises(SandboxViolationError, match="outside sandbox"):
            guard.check_path(str(link / "secret.txt"))


class TestReadOnlyPaths:
    def test_read_only_allows_read(self, tmp_path: Path) -> None:
        ro = tmp_path / "readonly"
        ro.mkdir()
        guard = _make_guard(tmp_path)
        result = guard.check_path(str(ro / "file.txt"), write=False)
        assert os.path.isabs(result)

    def test_read_only_blocks_write(self, tmp_path: Path) -> None:
        ro = tmp_path / "readonly"
        ro.mkdir()
        guard = _make_guard(tmp_path)
        with pytest.raises(SandboxViolationError, match="read-only"):
            guard.check_path(str(ro / "file.txt"), write=True)


class TestNormalizationIdempotent:
    def test_check_idempotent(self, tmp_path: Path) -> None:
        sb = tmp_path / "sandbox"
        sb.mkdir()
        guard = _make_guard(tmp_path)
        first = guard.check_path(str(sb / "a" / ".." / "b"))
        second = guard.check_path(first)
        assert first == second
