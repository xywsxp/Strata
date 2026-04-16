"""Tests for strata.planner.adjuster — local plan adjustment."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import icontract
import pytest
from hypothesis import given, settings

from strata.core.errors import PlannerError
from strata.core.types import TaskGraph, TaskNode
from strata.llm.provider import ChatResponse
from strata.planner.adjuster import Adjustment, adjust_plan, apply_adjustment
from tests.strategies import st_task_graph


def _make_mock_router(response_json: str) -> MagicMock:
    mock_router = MagicMock()
    mock_router.plan.return_value = ChatResponse(
        content=response_json,
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        finish_reason="stop",
    )
    return mock_router


def _sample_graph() -> TaskGraph:
    return TaskGraph(
        goal="test",
        tasks=(
            TaskNode(id="t1", task_type="primitive", action="click"),
            TaskNode(id="t2", task_type="primitive", action="type"),
            TaskNode(id="t3", task_type="primitive", action="scroll"),
        ),
    )


# ── adjust_plan with mock LLM ──


class TestAdjustPlan:
    def test_replace_strategy(self) -> None:
        adj_data = {
            "strategy": "replace",
            "replacement_tasks": [
                {"id": "new1", "task_type": "primitive", "action": "right_click"},
            ],
        }
        router = _make_mock_router(json.dumps(adj_data))
        result = adjust_plan(_sample_graph(), "t2", {"error": "click failed"}, router)
        assert result.strategy == "replace"
        assert len(result.replacement_tasks) == 1
        assert result.replacement_tasks[0].id == "new1"

    def test_insert_before_strategy(self) -> None:
        adj_data = {
            "strategy": "insert_before",
            "replacement_tasks": [
                {"id": "prep1", "task_type": "primitive", "action": "wait"},
            ],
        }
        router = _make_mock_router(json.dumps(adj_data))
        result = adjust_plan(_sample_graph(), "t1", {}, router)
        assert result.strategy == "insert_before"

    def test_insert_after_strategy(self) -> None:
        adj_data = {
            "strategy": "insert_after",
            "replacement_tasks": [
                {"id": "cleanup1", "task_type": "primitive", "action": "verify"},
            ],
        }
        router = _make_mock_router(json.dumps(adj_data))
        result = adjust_plan(_sample_graph(), "t3", {}, router)
        assert result.strategy == "insert_after"

    def test_unknown_task_raises(self) -> None:
        router = MagicMock()
        with pytest.raises(icontract.ViolationError):
            adjust_plan(_sample_graph(), "nonexistent", {}, router)

    def test_invalid_response_raises(self) -> None:
        router = _make_mock_router("not json at all")
        with pytest.raises(PlannerError, match="failed to adjust"):
            adjust_plan(_sample_graph(), "t1", {}, router)

    def test_conflicting_id_raises(self) -> None:
        adj_data = {
            "strategy": "replace",
            "replacement_tasks": [
                {"id": "t1", "task_type": "primitive", "action": "dup"},
            ],
        }
        router = _make_mock_router(json.dumps(adj_data))
        with pytest.raises(PlannerError, match="failed to adjust"):
            adjust_plan(_sample_graph(), "t2", {}, router)

    def test_too_many_replacements_raises(self) -> None:
        adj_data = {
            "strategy": "replace",
            "replacement_tasks": [
                {"id": f"new{i}", "task_type": "primitive", "action": "x"} for i in range(5)
            ],
        }
        router = _make_mock_router(json.dumps(adj_data))
        with pytest.raises(PlannerError, match="failed to adjust"):
            adjust_plan(_sample_graph(), "t1", {}, router)


# ── apply_adjustment ──


class TestApplyAdjustment:
    def test_replace(self) -> None:
        g = _sample_graph()
        adj = Adjustment(
            original_task_id="t2",
            replacement_tasks=(TaskNode(id="r1", task_type="primitive", action="new_action"),),
            strategy="replace",
        )
        result = apply_adjustment(g, adj)
        ids = [t.id for t in result.tasks]
        assert "t2" not in ids
        assert "r1" in ids
        assert ids == ["t1", "r1", "t3"]

    def test_insert_before(self) -> None:
        g = _sample_graph()
        adj = Adjustment(
            original_task_id="t2",
            replacement_tasks=(TaskNode(id="b1", task_type="primitive", action="prep"),),
            strategy="insert_before",
        )
        result = apply_adjustment(g, adj)
        ids = [t.id for t in result.tasks]
        assert ids == ["t1", "b1", "t2", "t3"]

    def test_insert_after(self) -> None:
        g = _sample_graph()
        adj = Adjustment(
            original_task_id="t2",
            replacement_tasks=(TaskNode(id="a1", task_type="primitive", action="verify"),),
            strategy="insert_after",
        )
        result = apply_adjustment(g, adj)
        ids = [t.id for t in result.tasks]
        assert ids == ["t1", "t2", "a1", "t3"]

    def test_nonexistent_target_raises(self) -> None:
        g = _sample_graph()
        adj = Adjustment(
            original_task_id="nope",
            replacement_tasks=(TaskNode(id="r1", task_type="primitive", action="x"),),
            strategy="replace",
        )
        with pytest.raises(icontract.ViolationError):
            apply_adjustment(g, adj)

    def test_apply_validates_result(self) -> None:
        g = TaskGraph(
            goal="test",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="click", depends_on=("t2",)),
                TaskNode(id="t2", task_type="primitive", action="type"),
            ),
        )
        adj = Adjustment(
            original_task_id="t2",
            replacement_tasks=(TaskNode(id="r1", task_type="primitive", action="new"),),
            strategy="replace",
        )
        with pytest.raises(PlannerError, match="adjusted graph is invalid"):
            apply_adjustment(g, adj)


# ── Hypothesis property: other tasks preserved ──


@given(g=st_task_graph())
@settings(max_examples=30)
def test_prop_apply_adjustment_preserves_other_tasks(g: TaskGraph) -> None:
    from strata.planner.htn import validate_graph

    if len(g.tasks) < 2:
        return
    if validate_graph(g):
        return  # skip graphs that are already invalid

    target = g.tasks[0]
    replacement = TaskNode(
        id=f"_replacement_{target.id}_unique",
        task_type="primitive",
        action="replacement_action",
    )
    adj = Adjustment(
        original_task_id=target.id,
        replacement_tasks=(replacement,),
        strategy="replace",
    )
    result = apply_adjustment(g, adj)

    original_other_ids = {t.id for t in g.tasks if t.id != target.id}
    result_ids = {t.id for t in result.tasks}
    assert original_other_ids.issubset(result_ids)
