"""Local plan adjustment — generate replacement sub-graphs for failed tasks.

Uses topological pruning (extract_local_context) to avoid sending the full
TaskGraph to the LLM, preventing context-window overflow.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import icontract

from strata.core.errors import LLMTransientError, PlannerError
from strata.core.types import TaskGraph, TaskNode, task_node_from_dict, task_node_to_dict

# CONVENTION: extract_local_context 是 planner 的上下文萃取工具，实现落在
# harness.context 只是历史编排。若在模块顶层 import，会经过
# strata.harness.__init__（Phase F 后聚合导出 AgentOrchestrator）→
# AgentOrchestrator import adjuster → adjuster 尚未初始化，触发循环 import。
# 改为函数内延迟 import 打破循环，不引入行为变化。
from strata.llm.provider import ChatMessage
from strata.llm.router import LLMRouter
from strata.planner.htn import validate_graph
from strata.planner.prompts import ADJUST_SYSTEM_PROMPT, ADJUST_USER_TEMPLATE

_MAX_ADJUST_RETRIES: Final[int] = 2
_MAX_REPLACEMENT_TASKS: Final[int] = 3


@dataclass(frozen=True)
class Adjustment:
    original_task_id: str
    replacement_tasks: Sequence[TaskNode]
    strategy: Literal["replace", "insert_before", "insert_after"]


@icontract.require(
    lambda graph, failed_task_id: any(t.id == failed_task_id for t in graph.tasks),
    "failed_task_id must exist in graph",
)
@icontract.ensure(
    lambda result: 1 <= len(result.replacement_tasks) <= _MAX_REPLACEMENT_TASKS,
    "replacement_tasks count must be in [1, 3]",
)
def adjust_plan(
    graph: TaskGraph,
    failed_task_id: str,
    failure_context: Mapping[str, object],
    router: LLMRouter,
    action_catalog: str | None = None,
) -> Adjustment:
    """Generate a local adjustment for a failed task using topological pruning.

    Only the failed node, its siblings, and parent are sent to the LLM —
    never the full graph.

    Pass ``action_catalog`` (e.g. :func:`format_action_catalog_for_llm`) so the
    repair LLM knows the exact ``params`` keys each action demands; without it
    the adjuster will hallucinate parameter names (``file_path`` instead of
    ``path``) and the replacement plan will immediately trip
    :class:`strata.core.errors.ActionParamsError`.
    """
    from strata.harness.context import extract_local_context

    local_ctx = extract_local_context(graph, failed_task_id)
    existing_ids = {t.id for t in graph.tasks}

    failed_task_json = json.dumps(task_node_to_dict(local_ctx.failed_node), ensure_ascii=False)
    siblings_json = json.dumps(
        [task_node_to_dict(s) for s in local_ctx.siblings], ensure_ascii=False
    )
    failure_context_json = json.dumps(dict(failure_context), ensure_ascii=False)

    user_prompt = ADJUST_USER_TEMPLATE.format(
        failed_task_json=failed_task_json,
        siblings_json=siblings_json,
        parent_id=local_ctx.parent_id or "none",
        failure_context_json=failure_context_json,
        existing_ids=", ".join(sorted(existing_ids)),
        action_catalog=action_catalog or "(catalog unavailable — use generic action names)",
    )

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=ADJUST_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]

    last_error: Exception | None = None
    for _attempt in range(_MAX_ADJUST_RETRIES + 1):
        try:
            response = router.plan(messages, json_mode=True, temperature=0.2)
            return _parse_adjustment(response.content, failed_task_id, existing_ids)
        except (PlannerError, LLMTransientError) as exc:
            last_error = exc
            continue

    raise PlannerError(
        f"failed to adjust plan after {_MAX_ADJUST_RETRIES + 1} attempts: {last_error}"
    )


_MARKDOWN_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _strip_markdown_fence(raw: str) -> str:
    """Strip a ``\\`\\`\\`json ... \\`\\`\\``` fence if present.

    Some providers wrap JSON output in markdown fences even when json_mode is
    requested. Strip before parsing; non-fenced input passes through.
    """
    match = _MARKDOWN_FENCE.match(raw)
    if match:
        return match.group(1)
    return raw


def _parse_adjustment(
    raw_json: str,
    original_task_id: str,
    existing_ids: set[str],
) -> Adjustment:
    """Parse LLM response into an Adjustment, validating constraints."""
    try:
        data = json.loads(_strip_markdown_fence(raw_json))
    except json.JSONDecodeError as exc:
        raise PlannerError(f"invalid JSON from LLM: {exc}") from exc

    if not isinstance(data, dict):
        raise PlannerError(f"expected JSON object, got {type(data).__name__}")

    strategy = data.get("strategy", "replace")
    if strategy not in ("replace", "insert_before", "insert_after"):
        raise PlannerError(f"invalid strategy: {strategy!r}")

    raw_tasks = data.get("replacement_tasks", [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlannerError("replacement_tasks must be a non-empty list")

    if len(raw_tasks) > _MAX_REPLACEMENT_TASKS:
        raise PlannerError(
            f"too many replacement tasks: {len(raw_tasks)} > {_MAX_REPLACEMENT_TASKS}"
        )

    replacement_tasks: list[TaskNode] = []
    for raw_t in raw_tasks:
        if not isinstance(raw_t, dict):
            raise PlannerError(f"replacement task must be a dict, got {type(raw_t).__name__}")
        node = task_node_from_dict(raw_t)
        if node.id in existing_ids:
            raise PlannerError(f"replacement task id {node.id!r} conflicts with existing task")
        replacement_tasks.append(node)

    return Adjustment(
        original_task_id=original_task_id,
        replacement_tasks=tuple(replacement_tasks),
        strategy=strategy,
    )


@icontract.require(
    lambda graph, adjustment: any(t.id == adjustment.original_task_id for t in graph.tasks),
    "original_task_id must exist in graph",
)
def apply_adjustment(graph: TaskGraph, adjustment: Adjustment) -> TaskGraph:
    """Apply an Adjustment to a TaskGraph, returning a new graph.

    Strategies:
    - replace: swap the original task with replacement tasks
    - insert_before: insert replacements before the original task
    - insert_after: insert replacements after the original task
    """
    tasks = list(graph.tasks)
    target_idx = next(i for i, t in enumerate(tasks) if t.id == adjustment.original_task_id)
    replacements = list(adjustment.replacement_tasks)

    if adjustment.strategy == "replace":
        tasks[target_idx : target_idx + 1] = replacements
    elif adjustment.strategy == "insert_before":
        tasks[target_idx:target_idx] = replacements
    elif adjustment.strategy == "insert_after":
        tasks[target_idx + 1 : target_idx + 1] = replacements

    new_graph = TaskGraph(goal=graph.goal, tasks=tuple(tasks), methods=graph.methods)

    errors = validate_graph(new_graph)
    if errors:
        raise PlannerError(f"adjusted graph is invalid: {'; '.join(errors)}")

    return new_graph
