"""Tests for strata.harness.recovery — 5-level recovery pipeline."""

from __future__ import annotations

from collections.abc import Sequence

from strata.core.config import get_default_config
from strata.core.errors import PlannerError
from strata.core.types import TaskNode
from strata.harness.recovery import RecoveryLevel, RecoveryPipeline


def _mock_adjuster(failed_task: TaskNode, error: Exception) -> Sequence[TaskNode]:
    return [failed_task]


def _failing_adjuster(failed_task: TaskNode, error: Exception) -> Sequence[TaskNode]:
    raise PlannerError("adjuster failed")


def _empty_adjuster(failed_task: TaskNode, error: Exception) -> Sequence[TaskNode]:
    return []


_TASK = TaskNode(id="t1", task_type="primitive", action="click")


class TestRecoveryEscalation:
    def test_first_attempt_retries(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _mock_adjuster)
        action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), 0)
        assert action.level == RecoveryLevel.RETRY

    def test_third_attempt_replans(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _mock_adjuster)
        action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), 3)
        assert action.level == RecoveryLevel.REPLAN
        assert len(action.replacement_tasks) > 0

    def test_fifth_attempt_user(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _mock_adjuster)
        action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), 5)
        assert action.level == RecoveryLevel.USER_INTERVENTION

    def test_adjuster_failure_escalates_to_skip(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _failing_adjuster)
        action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), 3)
        assert action.level == RecoveryLevel.SKIP

    def test_adjuster_empty_treated_as_failure(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _empty_adjuster)
        action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), 3)
        assert action.level == RecoveryLevel.SKIP

    def test_monotonic_escalation(self) -> None:
        pipeline = RecoveryPipeline(get_default_config(), _mock_adjuster)
        levels = []
        for i in range(6):
            action = pipeline.attempt_recovery(_TASK, RuntimeError("fail"), i)
            levels.append(action.level.value)
        for i in range(1, len(levels)):
            assert levels[i] >= levels[i - 1]
