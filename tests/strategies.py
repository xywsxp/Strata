"""Hypothesis custom strategies for Strata test suite.

All reusable strategies live here. Test files import from this module;
inline strategy definitions in test files are prohibited.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from typing import Literal, cast

import hypothesis.strategies as st
from hypothesis.strategies import SearchStrategy

from strata.core._validators import VALID_GLOBAL_STATES
from strata.core.types import ActionResult, CommandResult, Coordinate, TaskGraph, TaskNode
from strata.harness.actions import ACTION_PARAM_SCHEMA, ACTION_VOCABULARY
from strata.harness.persistence import Checkpoint

_ALPHA_NUM = st.characters(categories=["L", "N"])
_ALPHA = st.characters(categories=["L"])


@st.composite
def st_task_node(
    draw: st.DrawFn,
    task_type: str | None = None,
) -> TaskNode:
    """Generate a TaskNode with valid field combinations."""
    tt = task_type or draw(
        st.sampled_from(["primitive", "compound", "repeat", "if_then", "for_each"])
    )
    node_id = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))

    action = draw(st.text(min_size=1, max_size=20)) if tt == "primitive" else None
    method = draw(st.text(min_size=1, max_size=20)) if tt == "compound" else None
    max_iter = (
        draw(st.integers(min_value=1, max_value=100)) if tt in ("repeat", "for_each") else None
    )

    params: dict[str, object] = {}
    if draw(st.booleans()):
        params = draw(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=10, alphabet=_ALPHA),
                values=st.one_of(st.text(max_size=50), st.integers(), st.booleans()),
                max_size=3,
            )
        )

    output_var = draw(st.none() | st.text(min_size=1, max_size=15, alphabet=_ALPHA))

    return TaskNode(
        id=node_id,
        task_type=cast(Literal["primitive", "compound", "repeat", "if_then", "for_each"], tt),
        action=action,
        params=params,
        method=method,
        depends_on=(),
        output_var=output_var,
        max_iterations=max_iter,
    )


@st.composite
def st_task_graph(draw: st.DrawFn) -> TaskGraph:
    """Generate a linear TaskGraph with 1-10 TaskNodes (no DAG dependencies)."""
    goal = draw(st.text(min_size=1, max_size=50))
    n = draw(st.integers(min_value=1, max_value=10))
    nodes: list[TaskNode] = []
    used_ids: set[str] = set()
    for i in range(n):
        node = draw(st_task_node())
        unique_id = f"{node.id}_{i}"
        while unique_id in used_ids:
            unique_id = f"{unique_id}_x"
        used_ids.add(unique_id)
        nodes.append(
            TaskNode(
                id=unique_id,
                task_type=node.task_type,
                action=node.action,
                params=node.params,
                method=node.method,
                depends_on=(),
                output_var=node.output_var,
                max_iterations=node.max_iterations,
            )
        )
    return TaskGraph(goal=goal, tasks=tuple(nodes))


def st_task_node_strategy() -> SearchStrategy[TaskNode]:
    """Convenience wrapper returning the composite strategy."""
    return st_task_node()


def st_task_graph_strategy() -> SearchStrategy[TaskGraph]:
    """Convenience wrapper returning the composite strategy."""
    return st_task_graph()


# ── Sandbox path strategy ──


@st.composite
def st_sandbox_path(
    draw: st.DrawFn,
) -> tuple[str, str, bool]:
    """Generate (sandbox_root, path, should_escape) tuples for SandboxGuard testing.

    Creates a real temporary directory as the sandbox root, then generates
    either a safe intra-sandbox path or an escaping path with ``..`` traversal.
    """
    sandbox_root = tempfile.mkdtemp(prefix="strata_sb_")

    escape = draw(st.booleans())
    if escape:
        depth = draw(st.integers(min_value=2, max_value=5))
        path = os.path.join(*([os.pardir] * depth), "etc", "passwd")
        return (sandbox_root, path, True)
    else:
        segments = draw(
            st.lists(
                st.text(
                    alphabet=st.characters(categories=["L", "N"]),
                    min_size=1,
                    max_size=8,
                ),
                min_size=1,
                max_size=4,
            )
        )
        path = os.path.join(*segments)
        return (sandbox_root, path, False)


# ── Coordinate strategy ──


@st.composite
def st_coordinate(
    draw: st.DrawFn,
    max_x: float = 1920.0,
    max_y: float = 1080.0,
) -> Coordinate:
    """Generate a Coordinate within screen bounds."""
    x = draw(st.floats(min_value=0.0, max_value=max_x, allow_nan=False))
    y = draw(st.floats(min_value=0.0, max_value=max_y, allow_nan=False))
    return Coordinate(x=x, y=y)


# ── CommandResult strategy ──


@st.composite
def st_command_result(draw: st.DrawFn) -> CommandResult:
    """Generate a successful CommandResult.

    Timeouts / silence interruptions are exceptions, not fields — this strategy
    models only the success / non-zero-exit outcomes that `run_command` may
    legitimately return.
    """
    return CommandResult(
        stdout=draw(st.text(max_size=200)),
        stderr=draw(st.text(max_size=200)),
        returncode=draw(st.integers(min_value=-128, max_value=255)),
    )


# ── Action vocabulary strategies ──


def st_action_name(valid: bool = True) -> SearchStrategy[str]:
    """Draw an action name.

    When ``valid`` is True, samples uniformly from :data:`ACTION_VOCABULARY`.
    When ``valid`` is False, samples random ASCII strings deliberately chosen
    to fall outside the vocabulary (prefix ``__bad_`` guarantees disjointness).
    """
    if valid:
        return st.sampled_from(ACTION_VOCABULARY)
    return st.text(
        min_size=1,
        max_size=15,
        alphabet=_ALPHA_NUM,
    ).map(lambda s: f"__bad_{s}")


def _params_for_action(draw: st.DrawFn, action: str) -> Mapping[str, object]:
    """Fill the required params for ``action`` with typed dummy values."""
    required = ACTION_PARAM_SCHEMA[action]
    out: dict[str, object] = {}
    for key in sorted(required):
        if key in ("x", "y"):
            out[key] = float(draw(st.floats(min_value=0.0, max_value=1000.0, allow_nan=False)))
        elif key in ("delta_x", "delta_y"):
            out[key] = int(draw(st.integers(min_value=-100, max_value=100)))
        elif key == "keys":
            out[key] = draw(
                st.lists(
                    st.text(min_size=1, max_size=5, alphabet=_ALPHA),
                    min_size=1,
                    max_size=3,
                )
            )
        elif key == "encoding":
            out[key] = "utf-8"
        elif key == "timeout":
            out[key] = 30.0
        else:
            out[key] = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))
    return out


@st.composite
def st_primitive_task_node(
    draw: st.DrawFn,
    action: str | None = None,
) -> TaskNode:
    """Generate a primitive TaskNode with a valid action + required params."""
    chosen = action if action is not None else draw(st.sampled_from(ACTION_VOCABULARY))
    node_id = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))
    params = _params_for_action(draw, chosen)
    return TaskNode(
        id=node_id,
        task_type="primitive",
        action=chosen,
        params=params,
        method=None,
        depends_on=(),
        output_var=None,
        max_iterations=None,
    )


@st.composite
def st_invalid_primitive_task(draw: st.DrawFn) -> TaskNode:
    """Generate an invalid primitive TaskNode.

    Two failure modes: (a) action name is outside the vocabulary; (b) action
    is valid but required params are dropped. Chosen randomly.
    """
    node_id = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))
    drop_params = draw(st.booleans())
    if drop_params:
        action = draw(st.sampled_from(ACTION_VOCABULARY))
        required = ACTION_PARAM_SCHEMA[action]
        if not required:
            bad_name = draw(st_action_name(valid=False))
            return TaskNode(
                id=node_id,
                task_type="primitive",
                action=bad_name,
                params={},
            )
        return TaskNode(
            id=node_id,
            task_type="primitive",
            action=action,
            params={},
        )
    bad_name = draw(st_action_name(valid=False))
    return TaskNode(
        id=node_id,
        task_type="primitive",
        action=bad_name,
        params={},
    )


def st_failing_sequence(max_length: int = 10) -> SearchStrategy[Sequence[bool]]:
    """Draw a non-empty bool sequence mapping to executor success/failure trace."""
    return st.lists(st.booleans(), min_size=1, max_size=max_length).map(tuple)


class _DeterministicExecutor:
    """Mock TaskExecutor returning pre-scripted success/failure results.

    The executor honours the Protocol in ``strata.harness.scheduler`` purely
    structurally: the ``execute`` method signature matches. Each call pops
    the next boolean from the pattern; past the pattern's end the last value
    repeats.
    """

    def __init__(self, pattern: Sequence[bool]) -> None:
        self._pattern: tuple[bool, ...] = tuple(pattern) if pattern else (True,)
        self._cursor: int = 0
        self.calls: list[TaskNode] = []

    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        self.calls.append(task)
        idx = min(self._cursor, len(self._pattern) - 1)
        self._cursor += 1
        ok = self._pattern[idx]
        return ActionResult(
            success=ok,
            data={"call_index": idx} if ok else None,
            error=None if ok else f"deterministic failure at call {idx}",
        )


@st.composite
def st_deterministic_mock_executor(
    draw: st.DrawFn,
    success_pattern: Sequence[bool] | None = None,
) -> _DeterministicExecutor:
    """Draw a deterministic mock executor given (or sampled) success pattern."""
    pattern = success_pattern if success_pattern is not None else draw(st_failing_sequence())
    return _DeterministicExecutor(pattern)


# ── Checkpoint strategy (migrated from test_properties.py) ──

_GLOBAL_STATES = st.sampled_from(sorted(VALID_GLOBAL_STATES))
_TASK_STATES = st.sampled_from(["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"])


@st.composite
def st_checkpoint(draw: st.DrawFn) -> Checkpoint:
    """Generate a Checkpoint with valid global/task states and a matching TaskGraph."""
    gs = draw(_GLOBAL_STATES)
    n_tasks = draw(st.integers(min_value=0, max_value=5))
    task_states = {f"t{i}": draw(_TASK_STATES) for i in range(n_tasks)}
    ctx_keys = draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=8, alphabet=st.characters(categories=["L"])),
            values=st.text(max_size=20),
            max_size=3,
        )
    )
    context: dict[str, object] = {k: v for k, v in ctx_keys.items()}
    nodes = tuple(
        TaskNode(id=f"t{i}", task_type="primitive", action="click") for i in range(n_tasks)
    )
    graph = TaskGraph(goal="test", tasks=nodes)
    return Checkpoint(
        global_state=gs,  # type: ignore[arg-type]
        task_states=task_states,  # type: ignore[arg-type]
        context=context,
        task_graph=graph,
        timestamp=draw(st.floats(min_value=0.0, max_value=1e12, allow_nan=False)),
    )


# ── sudo command strategy ──


@st.composite
def st_sudo_command(draw: st.DrawFn) -> str:
    """Generate a shell command that may contain sudo in various positions.

    Returns commands with sudo as the first token, after a pipe operator,
    or after a semicolon/&& — covering the pipeline patterns that
    _sanitize_sudo must handle.
    """
    base_cmd = draw(st.text(min_size=1, max_size=30, alphabet=_ALPHA_NUM))
    pattern = draw(st.sampled_from(["simple", "pipe", "chain"]))
    if pattern == "simple":
        has_n = draw(st.booleans())
        return f"sudo {'-n ' if has_n else ''}{base_cmd}"
    elif pattern == "pipe":
        prefix = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))
        has_n = draw(st.booleans())
        return f"{prefix} | sudo {'-n ' if has_n else ''}{base_cmd}"
    else:
        prefix = draw(st.text(min_size=1, max_size=20, alphabet=_ALPHA_NUM))
        sep = draw(st.sampled_from([";", "&&", "||"]))
        has_n = draw(st.booleans())
        return f"{prefix} {sep} sudo {'-n ' if has_n else ''}{base_cmd}"


# ── Literal value strategy ──


def st_valid_literal_value(valid: frozenset[str]) -> SearchStrategy[str]:
    """Draw a value from a valid frozenset of literal strings."""
    return st.sampled_from(sorted(valid))
