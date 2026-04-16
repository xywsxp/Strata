"""Sanity checks for the custom Hypothesis strategies added for Phase A."""

from __future__ import annotations

from hypothesis import given

from strata.core.types import ActionResult, TaskNode
from strata.harness.actions import ACTION_PARAM_SCHEMA, ACTION_VOCABULARY

from .strategies import (
    _DeterministicExecutor,
    st_deterministic_mock_executor,
    st_failing_sequence,
    st_invalid_primitive_task,
    st_primitive_task_node,
)


@given(st_primitive_task_node())
def test_primitive_strategy_yields_valid_action(node: TaskNode) -> None:
    assert node.task_type == "primitive"
    assert node.action in ACTION_VOCABULARY
    assert node.action is not None
    required = ACTION_PARAM_SCHEMA[node.action]
    for key in required:
        assert key in node.params, f"required key {key} missing in params"


@given(st_invalid_primitive_task())
def test_invalid_strategy_always_invalid(node: TaskNode) -> None:
    """Either the action is outside the vocabulary, or required params are missing."""
    assert node.task_type == "primitive"
    if node.action not in ACTION_VOCABULARY:
        return
    required = ACTION_PARAM_SCHEMA[node.action]
    if required:
        missing = required - set(node.params.keys())
        assert missing, "expected at least one missing required key"


@given(st_failing_sequence())
def test_failing_sequence_is_non_empty_bool_tuple(pattern: tuple[bool, ...]) -> None:
    assert isinstance(pattern, tuple)
    assert len(pattern) >= 1
    assert all(isinstance(x, bool) for x in pattern)


@given(st_deterministic_mock_executor())
def test_deterministic_executor_follows_pattern(executor: _DeterministicExecutor) -> None:
    node = TaskNode(id="t1", task_type="primitive", action="screenshot", params={})
    result1 = executor.execute(node, {})
    result2 = executor.execute(node, {})
    assert isinstance(result1, ActionResult)
    assert isinstance(result2, ActionResult)
    assert len(executor.calls) == 2
