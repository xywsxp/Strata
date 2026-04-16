"""Hypothesis custom strategies for Strata test suite.

All reusable strategies live here. Test files import from this module;
inline strategy definitions in test files are prohibited.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis.strategies import SearchStrategy

from strata.core.types import TaskGraph, TaskNode

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
