"""Tests for strata.planner.htn — serialization, validation, registry, decomposition."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import icontract
import pytest
from hypothesis import given

from strata.core.errors import PlannerError
from strata.core.types import TaskGraph, TaskNode
from strata.llm.provider import ChatResponse
from strata.planner.htn import (
    MethodRegistry,
    decompose_goal,
    deserialize_graph,
    serialize_graph,
    validate_graph,
)
from tests.strategies import st_task_graph

# ── Serialization ──


class TestSerializeDeserialize:
    def test_simple_graph(self) -> None:
        g = TaskGraph(
            goal="test",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="click"),
                TaskNode(id="t2", task_type="primitive", action="type"),
            ),
        )
        s = serialize_graph(g)
        raw = json.loads(s)
        assert raw["goal"] == "test"
        assert len(raw["tasks"]) == 2

    def test_roundtrip(self) -> None:
        g = TaskGraph(
            goal="roundtrip test",
            tasks=(
                TaskNode(id="a", task_type="primitive", action="click"),
                TaskNode(id="b", task_type="compound", method="m1"),
            ),
            methods={
                "m1": (TaskNode(id="m1s1", task_type="primitive", action="type"),),
            },
        )
        assert deserialize_graph(serialize_graph(g)) == g


class TestDeserializeErrors:
    def test_empty_string_rejected(self) -> None:
        with pytest.raises((PlannerError, icontract.ViolationError)):
            deserialize_graph("")

    def test_invalid_json(self) -> None:
        with pytest.raises(PlannerError, match="invalid JSON"):
            deserialize_graph("{bad json")

    def test_non_object_json(self) -> None:
        with pytest.raises(PlannerError, match="expected JSON object"):
            deserialize_graph("[1, 2, 3]")


# ── Hypothesis property: roundtrip ──


@given(g=st_task_graph())
def test_prop_graph_serialize_roundtrip(g: TaskGraph) -> None:
    assert deserialize_graph(serialize_graph(g)) == g


# ── Validation ──


class TestValidation:
    def test_valid_graph(self) -> None:
        g = TaskGraph(
            goal="ok",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="click"),
                TaskNode(id="t2", task_type="primitive", action="type", depends_on=("t1",)),
            ),
        )
        assert validate_graph(g) == []

    def test_duplicate_ids(self) -> None:
        g = TaskGraph(
            goal="dup",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="click"),
                TaskNode(id="t1", task_type="primitive", action="type"),
            ),
        )
        errors = validate_graph(g)
        assert any("duplicate" in e for e in errors)

    def test_dangling_dependency(self) -> None:
        g = TaskGraph(
            goal="dang",
            tasks=(TaskNode(id="t1", task_type="primitive", action="click", depends_on=("nope",)),),
        )
        errors = validate_graph(g)
        assert any("unknown" in e for e in errors)

    def test_cycle_detected(self) -> None:
        g = TaskGraph(
            goal="cycle",
            tasks=(
                TaskNode(id="a", task_type="primitive", action="x", depends_on=("b",)),
                TaskNode(id="b", task_type="primitive", action="y", depends_on=("a",)),
            ),
        )
        errors = validate_graph(g)
        assert any("cycle" in e for e in errors)

    def test_missing_method(self) -> None:
        g = TaskGraph(
            goal="missing",
            tasks=(TaskNode(id="t1", task_type="compound", method="nonexistent"),),
        )
        errors = validate_graph(g)
        assert any("missing method" in e for e in errors)


# ── Hypothesis property: duplicate IDs detected ──


@given(g=st_task_graph())
def test_prop_validate_catches_duplicate_ids(g: TaskGraph) -> None:
    if len(g.tasks) < 2:
        return
    first = g.tasks[0]
    dup_task = TaskNode(
        id=first.id,
        task_type="primitive",
        action="dup_action",
    )
    dup_graph = TaskGraph(goal=g.goal, tasks=(*g.tasks, dup_task))
    errors = validate_graph(dup_graph)
    assert len(errors) > 0


# ── MethodRegistry ──


class TestMethodRegistry:
    def test_register_and_get(self) -> None:
        reg = MethodRegistry()
        sub = (TaskNode(id="s1", task_type="primitive", action="click"),)
        reg.register("open_app", ("app_installed",), sub)
        pre, tasks = reg.get("open_app")
        assert pre == ("app_installed",)
        assert tasks == sub

    def test_expand_compound(self) -> None:
        reg = MethodRegistry()
        sub = (TaskNode(id="s1", task_type="primitive", action="click"),)
        reg.register("open_app", (), sub)
        compound = TaskNode(id="c1", task_type="compound", method="open_app")
        expanded = reg.expand_compound(compound)
        assert expanded == sub

    def test_get_unknown_raises(self) -> None:
        reg = MethodRegistry()
        with pytest.raises(PlannerError):
            reg.get("unknown")


# ── decompose_goal (mock LLM) ──


def _make_mock_router(response_json: str) -> MagicMock:
    mock_router = MagicMock()
    mock_router.plan.return_value = ChatResponse(
        content=response_json,
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        finish_reason="stop",
    )
    return mock_router


class TestDecomposeGoal:
    def test_with_mock_llm(self) -> None:
        graph_data = {
            "goal": "open firefox",
            "tasks": [
                {"id": "t1", "task_type": "primitive", "action": "launch_app"},
            ],
            "methods": {},
        }
        router = _make_mock_router(json.dumps(graph_data))
        result = decompose_goal("open firefox", router, ["launch_app", "click"])
        assert result.goal == "open firefox"
        assert len(result.tasks) == 1

    def test_invalid_llm_response_raises(self) -> None:
        router = _make_mock_router("not valid json")
        with pytest.raises(PlannerError, match="failed to decompose"):
            decompose_goal("open firefox", router, ["click"])

    def test_goal_mismatch_corrected(self) -> None:
        graph_data = {
            "goal": "wrong goal",
            "tasks": [
                {"id": "t1", "task_type": "primitive", "action": "click"},
            ],
            "methods": {},
        }
        router = _make_mock_router(json.dumps(graph_data))
        result = decompose_goal("right goal", router, ["click"])
        assert result.goal == "right goal"
