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
