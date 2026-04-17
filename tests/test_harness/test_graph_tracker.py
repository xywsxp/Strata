"""Tests for strata.harness.graph_tracker — GraphTracker + NullGraphTracker."""

from __future__ import annotations

from pathlib import Path

from strata.core.types import TaskGraph, TaskNode
from strata.harness.graph_tracker import GraphTracker, NullGraphTracker


def _simple_graph(goal: str = "test") -> TaskGraph:
    return TaskGraph(
        goal=goal,
        tasks=(
            TaskNode(id="t1", task_type="primitive", action="click"),
            TaskNode(id="t2", task_type="primitive", action="type_text", depends_on=("t1",)),
            TaskNode(id="t3", task_type="primitive", action="press_key", depends_on=("t2",)),
        ),
    )


class TestGraphTracker:
    def test_initial_state(self) -> None:
        tracker = GraphTracker()
        assert tracker.current() is None
        assert tracker.version() == 0
        assert len(tracker.history()) == 0

    def test_update_bumps_version(self) -> None:
        tracker = GraphTracker()
        g = _simple_graph()
        tracker.update(g, "initial plan")
        assert tracker.version() == 1
        assert tracker.current() is g

    def test_history_records_all_updates(self) -> None:
        tracker = GraphTracker()
        g1 = _simple_graph("goal1")
        g2 = _simple_graph("goal2")
        tracker.update(g1, "plan 1")
        tracker.update(g2, "replan")
        history = tracker.history()
        assert len(history) == 2
        assert history[0][0] is g1
        assert history[0][1] == "plan 1"
        assert history[1][0] is g2
        assert history[1][1] == "replan"

    def test_export_creates_files(self, tmp_path: Path) -> None:
        tracker = GraphTracker(run_dir=tmp_path)
        g = _simple_graph()
        tracker.update(g, "initial")
        tracker.export_snapshot({"t1": "SUCCEEDED", "t2": "RUNNING", "t3": "PENDING"})
        assert (tmp_path / "graph" / "v1.json").exists()
        assert (tmp_path / "graph" / "v1.mermaid").exists()
        assert (tmp_path / "graph" / "v1_states.json").exists()

    def test_render_mermaid_format(self) -> None:
        tracker = GraphTracker()
        g = _simple_graph()
        tracker.update(g, "initial")
        mermaid = tracker.render_mermaid({"t1": "SUCCEEDED", "t2": "RUNNING", "t3": "PENDING"})
        assert mermaid.startswith("graph TD\n")
        assert "t1" in mermaid
        assert "-->" in mermaid

    def test_version_monotonic(self) -> None:
        tracker = GraphTracker()
        for i in range(5):
            tracker.update(_simple_graph(f"goal-{i}"), f"reason-{i}")
        assert tracker.version() == 5
        for i, (_, reason, _) in enumerate(tracker.history()):
            assert reason == f"reason-{i}"


class TestNullGraphTracker:
    def test_no_side_effects(self, tmp_path: Path) -> None:
        tracker = NullGraphTracker()
        g = _simple_graph()
        tracker.update(g, "initial")
        tracker.export_snapshot({"t1": "PENDING"})
        # No files should be created anywhere
        assert not list(tmp_path.iterdir())

    def test_tracks_version_in_memory(self) -> None:
        tracker = NullGraphTracker()
        tracker.update(_simple_graph(), "test")
        assert tracker.version() == 1
        assert tracker.current() is not None
