"""Tests for strata.paths — RunDirLayout + gc_old_runs."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import icontract
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from strata.core.paths import PathsConfig, RunDirLayout, gc_old_runs


class TestPathsConfig:
    def test_rejects_empty_run_root(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            PathsConfig(run_root="  ", keep_last_runs=5)

    def test_rejects_negative_keep(self) -> None:
        with pytest.raises(ValueError, match=">= 0"):
            PathsConfig(run_root="/tmp/x", keep_last_runs=-1)

    def test_valid_config(self) -> None:
        cfg = PathsConfig(run_root="/tmp/test-root", keep_last_runs=0)
        assert cfg.run_root == "/tmp/test-root"
        assert cfg.keep_last_runs == 0


class TestRunDirLayout:
    def test_create_all_paths_under_run_dir(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "test goal")

        assert layout.run_root == tmp_path
        assert layout.run_dir.parent == tmp_path / "runs"
        assert layout.audit_log_path.parent == layout.run_dir
        assert layout.llm_dir.parent == layout.run_dir
        assert layout.screenshots_dir.parent == layout.run_dir
        assert layout.recordings_dir.parent == layout.run_dir
        assert layout.logs_dir.parent == layout.run_dir
        assert layout.manifest_path.parent == layout.run_dir

    def test_create_rejects_empty_goal(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        with pytest.raises(icontract.ViolationError):
            RunDirLayout.create(cfg, "   ")

    def test_ensure_creates_directory_tree(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "ensure test")
        layout.ensure_dirs()

        assert layout.run_dir.is_dir()
        assert layout.llm_dir.is_dir()
        assert layout.screenshots_dir.is_dir()
        assert layout.recordings_dir.is_dir()
        assert layout.context_dir.is_dir()
        assert layout.logs_dir.is_dir()

    def test_ensure_idempotent(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "idempotent test")
        layout.ensure_dirs()
        layout.ensure_dirs()
        assert layout.run_dir.is_dir()

    def test_link_current_points_to_latest(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "link test")
        layout.ensure_dirs()
        layout.link_current()

        link = tmp_path / "current"
        assert link.is_symlink()
        assert link.resolve() == layout.run_dir.resolve()

    def test_link_current_replaces_old_symlink(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout1 = RunDirLayout.create(cfg, "goal one")
        layout1.ensure_dirs()
        layout1.link_current()

        layout2 = RunDirLayout.create(cfg, "goal two")
        layout2.ensure_dirs()
        layout2.link_current()

        link = tmp_path / "current"
        assert link.resolve() == layout2.run_dir.resolve()

    def test_write_manifest_round_trip(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "manifest test")
        layout.ensure_dirs()

        started = time.time()
        layout.write_manifest("manifest test", {"key": "value"}, started)

        data = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
        assert data["goal"] == "manifest test"
        assert data["config_snapshot"] == {"key": "value"}
        assert data["started_at"] == started
        assert "finished_at" in data

    def test_checkpoint_dir_at_run_root_level(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        layout = RunDirLayout.create(cfg, "checkpoint test")
        assert layout.checkpoint_dir == tmp_path
        assert layout.checkpoint_path == tmp_path / "checkpoint.json"

    def test_different_goals_produce_different_run_dirs(self, tmp_path: Path) -> None:
        cfg = PathsConfig(run_root=str(tmp_path), keep_last_runs=5)
        l1 = RunDirLayout.create(cfg, "goal alpha")
        l2 = RunDirLayout.create(cfg, "goal beta")
        assert l1.run_dir != l2.run_dir


class TestGCOldRuns:
    def test_gc_keeps_latest_k(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        for i in range(5):
            d = runs_dir / f"run_{i:03d}"
            d.mkdir()
            (d / "marker.txt").write_text(str(i))
            time.sleep(0.05)

        removed = gc_old_runs(tmp_path, keep=3)
        assert len(removed) == 2
        remaining = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
        assert len(remaining) == 3

    def test_gc_keep_zero_means_keep_all(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        for i in range(3):
            (runs_dir / f"run_{i}").mkdir()
        removed = gc_old_runs(tmp_path, keep=0)
        assert len(removed) == 0
        assert len(list(runs_dir.iterdir())) == 3

    def test_gc_nonexistent_runs_dir_returns_empty(self, tmp_path: Path) -> None:
        removed = gc_old_runs(tmp_path, keep=2)
        assert removed == ()

    def test_gc_rejects_negative_keep(self) -> None:
        with pytest.raises(icontract.ViolationError):
            gc_old_runs(Path("/tmp"), keep=-1)

    @given(
        n=st.integers(min_value=0, max_value=15),
        k=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=30)
    def test_gc_monotonic(self, n: int, k: int) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            runs_dir = root / "runs"
            runs_dir.mkdir()

            for i in range(n):
                d = runs_dir / f"run_{i:04d}"
                d.mkdir()
                os.utime(d, (i, i))

            removed_first = gc_old_runs(root, keep=k)
            assert len(removed_first) == max(0, n - k)

            removed_second = gc_old_runs(root, keep=k)
            assert len(removed_second) == 0
