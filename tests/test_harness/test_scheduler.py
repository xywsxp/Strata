"""Tests for strata.harness.scheduler — LinearRunner."""

from __future__ import annotations

from collections.abc import Mapping

from strata.core.config import get_default_config
from strata.core.types import ActionResult, TaskGraph, TaskNode
from strata.harness.scheduler import LinearRunner


class _MockExecutor:
    def __init__(self, results: dict[str, ActionResult] | None = None) -> None:
        self._results = results or {}
        self.call_order: list[str] = []

    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        self.call_order.append(task.id)
        return self._results.get(task.id, ActionResult(success=True))


class TestLinearThreeTasks:
    def test_sequential_execution(self) -> None:
        graph = TaskGraph(
            goal="test",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="a"),
                TaskNode(id="t2", task_type="primitive", action="b"),
                TaskNode(id="t3", task_type="primitive", action="c"),
            ),
        )
        executor = _MockExecutor()
        scheduler = LinearRunner(get_default_config())
        results = scheduler.run(graph, executor)
        assert executor.call_order == ["t1", "t2", "t3"]
        assert len(results) == 3


class TestRepeatMaxIterations:
    def test_repeat_limited(self) -> None:
        node = TaskNode(
            id="r1",
            task_type="repeat",
            max_iterations=3,
            params={"condition_var": "keep_going"},
        )
        graph = TaskGraph(goal="repeat test", tasks=(node,))

        class _CountExec:
            def __init__(self) -> None:
                self.count = 0

            def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
                self.count += 1
                return ActionResult(success=True)

        executor = _CountExec()
        scheduler = LinearRunner(get_default_config())
        scheduler.run(graph, executor)
        assert executor.count <= 3
