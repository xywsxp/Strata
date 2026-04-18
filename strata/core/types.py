"""Core value objects shared across all strata layers.

All dataclasses are frozen (immutable). Container inputs use Sequence/Mapping
rather than list/dict. No bare Any.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast, get_args

from strata.core._validators import VALID_GLOBAL_STATES, VALID_TASK_TYPES, validate_literal
from strata.core.errors import SerializationError

# ── Type aliases ──

GlobalState = Literal[
    "INIT",
    "PLANNING",
    "CONFIRMING",
    "SCHEDULING",
    "EXECUTING",
    "RECOVERING",
    "WAITING_USER",
    "COMPLETED",
    "FAILED",
]

assert frozenset(get_args(GlobalState)) == VALID_GLOBAL_STATES, (
    f"invariant: VALID_GLOBAL_STATES desync — "
    f"frozenset={VALID_GLOBAL_STATES}, Literal args={set(get_args(GlobalState))}"
)

TaskState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"]

LLMRole = Literal["planner", "grounding", "vision", "search"]

VisionActionType = Literal["click", "scroll", "next_page", "not_found"]


# ── Geometry ──


@dataclass(frozen=True)
class Coordinate:
    x: float
    y: float


@dataclass(frozen=True)
class ScreenRegion:
    x: int
    y: int
    width: int
    height: int


# ── Vision ──


@dataclass(frozen=True)
class VisionResponse:
    action_type: VisionActionType
    coordinate: Coordinate | None = None
    scroll_direction: Literal["up", "down", "left", "right"] | None = None
    confidence: float = 0.0
    raw_text: str = ""


# ── Window / App / File ──


@dataclass(frozen=True)
class WindowInfo:
    title: str
    process_id: int
    window_id: str
    position: Coordinate
    size: tuple[int, int]


@dataclass(frozen=True)
class FileInfo:
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: float


@dataclass(frozen=True)
class AppInfo:
    name: str
    identifier: str
    pid: int


# ── Terminal / Action ──


@dataclass(frozen=True)
class CommandResult:
    """Successful shell command outcome.

    Timeouts and silence interruptions are signaled via
    :class:`CommandTimeoutError` / :class:`SilenceTimeoutError`, not via fields
    on this value object — exceptions are the single source of truth.
    """

    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True)
class ActionResult:
    success: bool
    data: Mapping[str, object] | None = None
    error: str | None = None


# ── Task Graph ──


@dataclass(frozen=True)
class TaskNode:
    id: str
    task_type: Literal["primitive", "compound", "repeat", "if_then", "for_each"]
    action: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    method: str | None = None
    depends_on: Sequence[str] = field(default_factory=tuple)
    output_var: str | None = None
    max_iterations: int | None = None


@dataclass(frozen=True)
class TaskGraph:
    goal: str
    tasks: Sequence[TaskNode] = field(default_factory=tuple)
    methods: Mapping[str, Sequence[TaskNode]] = field(default_factory=dict)


# ── Serialization helpers ──


def task_node_to_dict(node: TaskNode) -> dict[str, object]:
    """Serialize a TaskNode to a plain dict suitable for JSON encoding."""
    d: dict[str, object] = {
        "id": node.id,
        "task_type": node.task_type,
    }
    if node.action is not None:
        d["action"] = node.action
    if node.params:
        d["params"] = dict(node.params)
    if node.method is not None:
        d["method"] = node.method
    if node.depends_on:
        d["depends_on"] = list(node.depends_on)
    if node.output_var is not None:
        d["output_var"] = node.output_var
    if node.max_iterations is not None:
        d["max_iterations"] = node.max_iterations
    return d


def task_node_from_dict(data: Mapping[str, object]) -> TaskNode:
    """Deserialize a TaskNode from a plain dict."""
    try:
        raw_depends = data.get("depends_on", ())
        depends_on: tuple[str, ...]
        if isinstance(raw_depends, (list, tuple)):
            depends_on = tuple(str(s) for s in raw_depends)
        else:
            depends_on = ()

        raw_params = data.get("params", {})
        params = dict(raw_params) if isinstance(raw_params, dict) else {}

        max_iter_raw = data.get("max_iterations")
        max_iterations = int(max_iter_raw) if isinstance(max_iter_raw, (int, float)) else None

        if "id" not in data:
            raise SerializationError("TaskNode missing required key 'id'")
        if "task_type" not in data:
            raise SerializationError("TaskNode missing required key 'task_type'")

        return TaskNode(
            id=str(data["id"]),
            task_type=cast(
                Literal["primitive", "compound", "repeat", "if_then", "for_each"],
                validate_literal(str(data["task_type"]), VALID_TASK_TYPES, "task_type"),
            ),
            action=str(data["action"]) if data.get("action") is not None else None,
            params=params,
            method=str(data["method"]) if data.get("method") is not None else None,
            depends_on=depends_on,
            output_var=(str(data["output_var"]) if data.get("output_var") is not None else None),
            max_iterations=max_iterations,
        )
    except SerializationError:
        raise
    except (TypeError, ValueError) as e:
        raise SerializationError(f"failed to deserialize TaskNode: {e}") from e


def task_graph_to_dict(graph: TaskGraph) -> dict[str, object]:
    """Serialize a TaskGraph to a plain dict suitable for JSON encoding."""
    return {
        "goal": graph.goal,
        "tasks": [task_node_to_dict(t) for t in graph.tasks],
        "methods": {
            name: [task_node_to_dict(t) for t in subtasks]
            for name, subtasks in graph.methods.items()
        },
    }


def task_graph_from_dict(data: Mapping[str, object]) -> TaskGraph:
    """Deserialize a TaskGraph from a plain dict."""
    try:
        if "goal" not in data:
            raise SerializationError("TaskGraph missing required key 'goal'")

        raw_tasks = data.get("tasks", ())
        tasks: tuple[TaskNode, ...]
        if isinstance(raw_tasks, (list, tuple)):
            tasks = tuple(task_node_from_dict(t) for t in raw_tasks)
        else:
            tasks = ()

        raw_methods = data.get("methods", {})
        methods: dict[str, Sequence[TaskNode]] = {}
        if isinstance(raw_methods, dict):
            for name, subtask_list in raw_methods.items():
                if isinstance(subtask_list, (list, tuple)):
                    methods[str(name)] = tuple(task_node_from_dict(t) for t in subtask_list)

        return TaskGraph(
            goal=str(data["goal"]),
            tasks=tasks,
            methods=methods,
        )
    except SerializationError:
        raise
    except (TypeError, ValueError) as e:
        raise SerializationError(f"failed to deserialize TaskGraph: {e}") from e
