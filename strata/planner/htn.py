"""TaskGraph serialization, validation, method registry, and LLM-driven decomposition."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

import icontract

from strata.core.errors import LLMTransientError, PlannerError
from strata.core.types import (
    TaskGraph,
    TaskNode,
    task_graph_from_dict,
    task_graph_to_dict,
)
from strata.llm.provider import ChatMessage
from strata.llm.router import LLMRouter
from strata.planner.prompts import DECOMPOSE_SYSTEM_PROMPT, DECOMPOSE_USER_TEMPLATE

_MAX_LLM_RETRIES: Final[int] = 2


# ── Serialization ──


def serialize_graph(graph: TaskGraph) -> str:
    """Serialize a TaskGraph to a JSON string."""
    return json.dumps(task_graph_to_dict(graph), ensure_ascii=False)


@icontract.require(lambda data: len(data.strip()) > 0, "data must be non-empty")
def deserialize_graph(data: str) -> TaskGraph:
    """Deserialize a TaskGraph from a JSON string."""
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise PlannerError(f"invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise PlannerError(f"expected JSON object, got {type(raw).__name__}")

    return task_graph_from_dict(raw)


# ── Validation ──


def validate_graph(graph: TaskGraph) -> Sequence[str]:
    """Return a list of validation errors (empty means valid).

    Checks: duplicate IDs, dangling dependencies, cycles, missing methods
    for compound tasks.
    """
    errors: list[str] = []
    seen_ids: set[str] = set()

    for task in graph.tasks:
        if task.id in seen_ids:
            errors.append(f"duplicate task id: {task.id!r}")
        seen_ids.add(task.id)

    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in seen_ids:
                errors.append(f"task {task.id!r} depends on unknown {dep!r}")

    for task in graph.tasks:
        if task.task_type == "compound" and task.method and task.method not in graph.methods:
            errors.append(f"task {task.id!r} references missing method {task.method!r}")

    cycle_errors = _detect_cycles(graph.tasks)
    errors.extend(cycle_errors)

    return errors


def _detect_cycles(tasks: Sequence[TaskNode]) -> list[str]:
    """Detect cycles in dependency graph via topological sort (Kahn's)."""
    id_set = {t.id for t in tasks}
    in_degree: dict[str, int] = {t.id: 0 for t in tasks}
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}

    for task in tasks:
        for dep in task.depends_on:
            if dep in id_set:
                adj[dep].append(task.id)
                in_degree[task.id] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited = 0

    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited < len(tasks):
        cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
        return [f"cycle detected involving: {', '.join(cycle_nodes)}"]
    return []


# ── Method Registry ──


class MethodRegistry:
    """Maps compound task method names to preconditions and subtask templates."""

    def __init__(self) -> None:
        self._registry: dict[str, tuple[Sequence[str], Sequence[TaskNode]]] = {}

    @icontract.require(lambda name: len(name.strip()) > 0, "method name must be non-empty")
    def register(
        self,
        name: str,
        preconditions: Sequence[str],
        subtasks: Sequence[TaskNode],
    ) -> None:
        self._registry[name] = (tuple(preconditions), tuple(subtasks))

    @icontract.require(
        lambda self, name: name in self._registry,
        "method must be registered",
        error=lambda name: PlannerError(f"unknown method: {name!r}"),
    )
    def get(self, name: str) -> tuple[Sequence[str], Sequence[TaskNode]]:
        return self._registry[name]

    @icontract.require(
        lambda node: node.task_type == "compound",
        "only compound tasks can be expanded",
    )
    @icontract.require(
        lambda self, node: node.method is not None and node.method in self._registry,
        "method must be registered",
        error=lambda node: PlannerError(f"method {node.method!r} not registered"),
    )
    def expand_compound(self, node: TaskNode) -> Sequence[TaskNode]:
        assert node.method is not None  # narrowing for mypy; contract already checked
        _, subtasks = self._registry[node.method]
        return subtasks


# ── LLM-driven goal decomposition ──


@icontract.require(lambda goal: len(goal.strip()) > 0, "goal must be non-empty")
@icontract.require(
    lambda available_actions: len(available_actions) > 0,
    "actions must be non-empty",
)
@icontract.ensure(
    lambda result: len(validate_graph(result)) == 0,
    "resulting TaskGraph must pass validation",
)
def decompose_goal(
    goal: str,
    router: LLMRouter,
    available_actions: Sequence[str],
    context: Mapping[str, object] | None = None,
    action_catalog: str | None = None,
) -> TaskGraph:
    """Decompose a natural-language goal into a TaskGraph using the planner LLM.

    Pass ``action_catalog`` when the caller has a richer, pre-formatted
    description of each action's required / optional ``params`` keys (e.g.
    :func:`strata.harness.actions.format_action_catalog_for_llm`). Without
    it, the LLM only sees action names and will hallucinate parameter keys
    (``"directory"`` instead of the contractual ``"path"``, etc.).
    """
    actions_str = action_catalog if action_catalog is not None else ", ".join(available_actions)
    context_str = json.dumps(dict(context), ensure_ascii=False) if context else "{}"
    user_prompt = DECOMPOSE_USER_TEMPLATE.format(
        goal=goal,
        available_actions=actions_str,
        context=context_str,
    )

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=DECOMPOSE_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]

    last_error: Exception | None = None
    for _attempt in range(_MAX_LLM_RETRIES + 1):
        try:
            response = router.plan(messages, json_mode=True, temperature=0.2)
            graph = deserialize_graph(response.content)
            errors = validate_graph(graph)
            if errors:
                raise PlannerError(f"invalid graph from LLM: {'; '.join(errors)}")
            if graph.goal != goal:
                graph = TaskGraph(goal=goal, tasks=graph.tasks, methods=graph.methods)
            return graph
        except (PlannerError, LLMTransientError) as exc:
            last_error = exc
            continue

    raise PlannerError(
        f"failed to decompose goal after {_MAX_LLM_RETRIES + 1} attempts: {last_error}"
    )
