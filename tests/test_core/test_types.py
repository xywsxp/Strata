"""Tests for strata.core.types — value objects and serialization."""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given

from strata.core.types import (
    ActionResult,
    CommandResult,
    Coordinate,
    TaskGraph,
    TaskNode,
    task_graph_from_dict,
    task_graph_to_dict,
    task_node_from_dict,
    task_node_to_dict,
)
from tests.strategies import st_task_graph, st_task_node


class TestFrozenImmutability:
    def test_task_node_frozen(self) -> None:
        node = TaskNode(id="t1", task_type="primitive", action="click")
        with pytest.raises(dataclasses.FrozenInstanceError):
            node.id = "t2"  # type: ignore[misc]

    def test_task_graph_frozen(self) -> None:
        graph = TaskGraph(goal="test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            graph.goal = "changed"  # type: ignore[misc]

    def test_coordinate_frozen(self) -> None:
        c = Coordinate(x=1.0, y=2.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.x = 5.0  # type: ignore[misc]

    def test_command_result_frozen(self) -> None:
        cr = CommandResult(stdout="ok", stderr="", returncode=0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            cr.returncode = 1  # type: ignore[misc]

    def test_action_result_frozen(self) -> None:
        ar = ActionResult(success=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ar.success = False  # type: ignore[misc]


class TestTaskGraphEmpty:
    def test_empty_tasks_valid(self) -> None:
        graph = TaskGraph(goal="empty test")
        assert graph.goal == "empty test"
        assert len(graph.tasks) == 0
        assert len(graph.methods) == 0


class TestTaskNodeRoundtrip:
    @given(node=st_task_node())
    def test_prop_task_node_roundtrip(self, node: TaskNode) -> None:
        d = task_node_to_dict(node)
        restored = task_node_from_dict(d)
        assert restored == node

    def test_primitive_node_roundtrip(self) -> None:
        node = TaskNode(
            id="click_btn",
            task_type="primitive",
            action="click",
            params={"x": 100, "y": 200},
        )
        assert task_node_from_dict(task_node_to_dict(node)) == node

    def test_compound_node_roundtrip(self) -> None:
        node = TaskNode(
            id="open_file",
            task_type="compound",
            method="open_file_method",
        )
        assert task_node_from_dict(task_node_to_dict(node)) == node


class TestTaskGraphRoundtrip:
    @given(graph=st_task_graph())
    def test_prop_task_graph_roundtrip(self, graph: TaskGraph) -> None:
        d = task_graph_to_dict(graph)
        restored = task_graph_from_dict(d)
        assert restored == graph

    def test_simple_graph_roundtrip(self) -> None:
        graph = TaskGraph(
            goal="test goal",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="click"),
                TaskNode(id="t2", task_type="primitive", action="type"),
            ),
        )
        assert task_graph_from_dict(task_graph_to_dict(graph)) == graph
