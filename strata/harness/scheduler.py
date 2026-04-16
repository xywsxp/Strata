"""Linear task scheduler with control-flow node interpretation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import icontract

from strata.core.config import StrataConfig
from strata.core.errors import MaxIterationsExceededError
from strata.core.types import ActionResult, TaskGraph, TaskNode


class TaskExecutor(Protocol):
    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult: ...


class LinearScheduler:
    """Execute tasks in graph order, interpreting control-flow nodes."""

    def __init__(self, config: StrataConfig) -> None:
        self._max_loop = config.max_loop_iterations

    @icontract.require(lambda graph: len(graph.tasks) > 0, "graph must have tasks")
    def run(self, graph: TaskGraph, executor: TaskExecutor) -> Mapping[str, ActionResult]:
        context: dict[str, object] = {}
        results: dict[str, ActionResult] = {}
        for task in graph.tasks:
            result = self._execute_task(task, executor, context)
            results[task.id] = result
            if task.output_var and result.data:
                context[task.output_var] = result.data
        return results

    def _execute_task(
        self,
        node: TaskNode,
        executor: TaskExecutor,
        context: dict[str, object],
    ) -> ActionResult:
        if node.task_type == "repeat":
            return self._interpret_repeat(node, executor, context)
        if node.task_type == "if_then":
            return self._interpret_if(node, executor, context)
        if node.task_type == "for_each":
            return self._interpret_foreach(node, executor, context)
        return executor.execute(node, context)

    def _interpret_repeat(
        self,
        node: TaskNode,
        executor: TaskExecutor,
        context: dict[str, object],
    ) -> ActionResult:
        max_iter = min(node.max_iterations or self._max_loop, self._max_loop)
        last_result = ActionResult(success=True)
        for _i in range(max_iter):
            last_result = executor.execute(node, context)
            if not last_result.success:
                break
            cond_var = node.params.get("condition_var")
            if cond_var and not context.get(str(cond_var)):
                break
        else:
            if node.max_iterations and max_iter >= node.max_iterations:
                raise MaxIterationsExceededError(f"repeat node {node.id} hit {max_iter} iterations")
        return last_result

    def _interpret_if(
        self,
        node: TaskNode,
        executor: TaskExecutor,
        context: dict[str, object],
    ) -> ActionResult:
        cond_var = str(node.params.get("condition_var", ""))
        condition = bool(context.get(cond_var, False))
        if condition:
            return executor.execute(node, context)
        return ActionResult(success=True)

    def _interpret_foreach(
        self,
        node: TaskNode,
        executor: TaskExecutor,
        context: dict[str, object],
    ) -> ActionResult:
        max_iter = min(node.max_iterations or self._max_loop, self._max_loop)
        items_var = str(node.params.get("items_var", ""))
        items = context.get(items_var, [])
        if not isinstance(items, (list, tuple)):
            items = []

        last_result = ActionResult(success=True)
        for i, item in enumerate(items):
            if i >= max_iter:
                raise MaxIterationsExceededError(
                    f"for_each node {node.id} exceeded {max_iter} iterations"
                )
            context["_current_item"] = item
            last_result = executor.execute(node, context)
            if not last_result.success:
                break
        return last_result
