"""Tests for strata.harness.persistence — checkpoint save/load + atomic_write."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from strata.core.errors import PersistenceSchemaVersionError
from strata.core.types import TaskGraph
from strata.harness.persistence import (
    CHECKPOINT_SCHEMA_VERSION,
    Checkpoint,
    PersistenceManager,
    atomic_write,
)


class TestAtomicWrite:
    def test_creates_file(self, tmp_path: Path) -> None:
        p = str(tmp_path / "test.bin")
        atomic_write(p, b"hello")
        assert Path(p).read_bytes() == b"hello"

    def test_no_tmp_residue(self, tmp_path: Path) -> None:
        p = str(tmp_path / "test.bin")
        atomic_write(p, b"data")
        assert not Path(p + ".tmp").exists()


class TestCheckpointRoundtrip:
    def test_save_load(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"))
        cp = Checkpoint(
            global_state="EXECUTING",
            task_states={"t1": "RUNNING"},
            context={"var1": "value1"},
            task_graph=TaskGraph(goal="test"),
            timestamp=time.time(),
        )
        mgr.save_checkpoint(cp)
        loaded = mgr.load_checkpoint()
        assert loaded is not None
        assert loaded.global_state == "EXECUTING"
        assert loaded.task_states["t1"] == "RUNNING"

    def test_load_no_checkpoint(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "empty"))
        assert mgr.load_checkpoint() is None

    def test_default_schema_version(self) -> None:
        cp = Checkpoint(
            global_state="INIT",
            task_states={},
            context={},
            task_graph=TaskGraph(goal="x"),
            timestamp=0.0,
        )
        assert cp.schema_version == CHECKPOINT_SCHEMA_VERSION


class TestCheckpointSchemaVersion:
    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"))
        Path(mgr._checkpoint_path).write_text(
            json.dumps(
                {
                    "global_state": "INIT",
                    "task_states": {},
                    "context": {},
                    "task_graph": {"goal": "x", "tasks": [], "methods": {}},
                    "timestamp": 0.0,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(PersistenceSchemaVersionError):
            mgr.load_checkpoint()

    def test_unknown_schema_version_raises(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"))
        Path(mgr._checkpoint_path).write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "global_state": "INIT",
                    "task_states": {},
                    "context": {},
                    "task_graph": {"goal": "x", "tasks": [], "methods": {}},
                    "timestamp": 0.0,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(PersistenceSchemaVersionError):
            mgr.load_checkpoint()


class TestMultiVersionCheckpoints:
    def _make_cp(self, label: str) -> Checkpoint:
        return Checkpoint(
            global_state="EXECUTING",
            task_states={"t1": "RUNNING"},
            context={"label": label},
            task_graph=TaskGraph(goal=label),
            timestamp=time.time(),
        )

    def test_versioned_save_and_load(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"), max_checkpoint_history=10)
        cp1 = self._make_cp("cp1")
        cp2 = self._make_cp("cp2")
        mgr.save_checkpoint(cp1)
        mgr.save_checkpoint(cp2)
        assert mgr.current_version == 2
        loaded_v1 = mgr.load_checkpoint(version=1)
        loaded_v2 = mgr.load_checkpoint(version=2)
        assert loaded_v1 is not None
        assert loaded_v2 is not None
        assert loaded_v1.context["label"] == "cp1"
        assert loaded_v2.context["label"] == "cp2"

    def test_gc_respects_max_history(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"), max_checkpoint_history=3)
        for i in range(5):
            mgr.save_checkpoint(self._make_cp(f"cp{i}"))
        versions = mgr.list_versions()
        assert len(versions) == 3
        assert versions[0] == 3

    def test_list_versions_empty(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"), max_checkpoint_history=5)
        assert mgr.list_versions() == []

    def test_clear_removes_versioned(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "state"), max_checkpoint_history=5)
        mgr.save_checkpoint(self._make_cp("cp1"))
        mgr.save_checkpoint(self._make_cp("cp2"))
        mgr.clear_checkpoint()
        assert mgr.list_versions() == []
        assert mgr.load_checkpoint() is None
        assert mgr.current_version == 0

    def test_single_history_no_versioned_files(self, tmp_path: Path) -> None:
        """max_checkpoint_history=1 behaves like the old single-checkpoint mode."""
        mgr = PersistenceManager(str(tmp_path / "state"), max_checkpoint_history=1)
        mgr.save_checkpoint(self._make_cp("only"))
        assert mgr.list_versions() == []
        assert mgr.load_checkpoint() is not None

    def test_version_reloaded_from_disk_on_restart(self, tmp_path: Path) -> None:
        """current_version is restored from disk when a new PersistenceManager is created."""
        state_dir = str(tmp_path / "state")
        mgr1 = PersistenceManager(state_dir, max_checkpoint_history=10)
        mgr1.save_checkpoint(self._make_cp("cp1"))
        mgr1.save_checkpoint(self._make_cp("cp2"))
        mgr1.save_checkpoint(self._make_cp("cp3"))
        assert mgr1.current_version == 3

        # Simulate restart — create a new manager pointing at the same dir.
        mgr2 = PersistenceManager(state_dir, max_checkpoint_history=10)
        assert mgr2.current_version == 3, "version must be restored from disk after restart"

    def test_version_starts_at_zero_when_no_files(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "fresh"), max_checkpoint_history=10)
        assert mgr.current_version == 0
