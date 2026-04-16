"""Hypothesis custom strategies for Strata test suite.

All reusable strategies live here. Test files import from this module;
inline strategy definitions in test files are prohibited.
"""

from __future__ import annotations

import os
import tempfile

import hypothesis.strategies as st
from hypothesis.strategies import SearchStrategy

from strata.core.types import CommandResult, Coordinate, TaskGraph, TaskNode

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
        task_type=tt,  # type: ignore[arg-type]
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
    """Generate CommandResult with valid field combinations.

    Ensures timed_out and interrupted_by_silence are never both True.
    """
    timed_out = draw(st.booleans())
    interrupted = False if timed_out else draw(st.booleans())
    return CommandResult(
        stdout=draw(st.text(max_size=200)),
        stderr=draw(st.text(max_size=200)),
        returncode=draw(st.integers(min_value=-128, max_value=255)),
        timed_out=timed_out,
        interrupted_by_silence=interrupted,
    )
