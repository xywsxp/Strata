"""Tests for strata.debug.rollback — RollbackEngine."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from strata.core.errors import DebugRollbackError
from strata.core.types import TaskGraph, TaskNode
from strata.debug.rollback import RollbackEngine
from strata.harness.graph_tracker import GraphTracker
from strata.harness.persistence import Checkpoint, PersistenceManager


def _make_cp(label: str) -> Checkpoint:
    return Checkpoint(
        global_state="EXECUTING",
        task_states={"t1": "RUNNING"},
        context={"label": label},
        task_graph=TaskGraph(goal=label),
        timestamp=time.time(),
    )


def _make_graph(goal: str) -> TaskGraph:
    return TaskGraph(
        goal=goal,
        tasks=(TaskNode(id="t1", task_type="primitive", action="click"),),
    )


class TestTaskUndo:
    def test_push_and_undo(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"), max_checkpoint_history=10)
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        engine.push_undo("t1", {"t1": "RUNNING"})
        engine.push_undo("t2", {"t1": "SUCCEEDED", "t2": "RUNNING"})
        assert engine.undo_depth == 2

        record = engine.undo_tasks(1)
        assert record.task_id == "t2"
        assert engine.undo_depth == 1

    def test_undo_multiple(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"), max_checkpoint_history=10)
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        for i in range(5):
            engine.push_undo(f"t{i}", {f"t{i}": "RUNNING"})

        record = engine.undo_tasks(3)
        assert record.task_id == "t2"
        assert engine.undo_depth == 2

    def test_undo_empty_raises(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"))
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)
        with pytest.raises(DebugRollbackError, match="only 0 on stack"):
            engine.undo_tasks(1)

    def test_undo_overflow_caps_stack(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"))
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt, max_undo=3)

        for i in range(5):
            engine.push_undo(f"t{i}", {})
        assert engine.undo_depth == 3


class TestCheckpointRollback:
    def test_rollback_to_version(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"), max_checkpoint_history=10)
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        mgr.save_checkpoint(_make_cp("v1"))
        mgr.save_checkpoint(_make_cp("v2"))

        cp = engine.rollback_to_checkpoint(1)
        assert cp.context["label"] == "v1"
        assert engine.undo_depth == 0

    def test_rollback_missing_version_raises(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"), max_checkpoint_history=10)
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        with pytest.raises(DebugRollbackError, match="not found"):
            engine.rollback_to_checkpoint(999)


class TestGraphRollback:
    def test_revert_graph_one_step(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"))
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        g1 = _make_graph("g1")
        g2 = _make_graph("g2")
        gt.update(g1, "initial")
        gt.update(g2, "replan")

        reverted = engine.rollback_graph(1)
        assert reverted.goal == "g1"

    def test_revert_graph_not_enough_history_raises(self, tmp_path: Path) -> None:
        mgr = PersistenceManager(str(tmp_path / "s"))
        gt = GraphTracker()
        engine = RollbackEngine(mgr, gt)

        gt.update(_make_graph("only"), "initial")

        with pytest.raises(DebugRollbackError, match="only 1 versions"):
            engine.rollback_graph(1)
