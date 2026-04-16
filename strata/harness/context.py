"""Working memory — variable bindings + fact slots.

Minimal version for Phase 4. Full sliding-window + compression logic
deferred to Phase 7 Step 7.2.

Also contains topological pruning for adjust_plan context extraction:
instead of passing the full TaskGraph, extract only the failed node,
its siblings, and its parent — sufficient local scope for LLM repair.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import icontract

from strata.core.config import MemoryConfig
from strata.core.types import TaskGraph, TaskNode


@dataclass(frozen=True)
class ContextFact:
    key: str
    value: str
    timestamp: float


class WorkingMemory:
    """Variable bindings + FIFO fact slot."""

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._vars: dict[str, object] = {}
        self._facts: list[ContextFact] = []

    @icontract.require(lambda key: len(key.strip()) > 0, "key must be non-empty")
    def set_var(self, key: str, value: object) -> None:
        self._vars[key] = value

    def get_var(self, key: str) -> object | None:
        return self._vars.get(key)

    @icontract.require(lambda key: len(key.strip()) > 0, "key must be non-empty")
    def add_fact(self, key: str, value: str) -> None:
        self._facts.append(ContextFact(key=key, value=value, timestamp=time.time()))
        while len(self._facts) > self._config.max_facts_in_slot:
            self._facts.pop(0)

    def get_facts(self) -> Sequence[ContextFact]:
        return tuple(self._facts)

    def get_variables(self) -> Mapping[str, object]:
        return dict(self._vars)

    def clear(self) -> None:
        self._vars.clear()
        self._facts.clear()


# ── Topological pruning for adjust_plan ──


@dataclass(frozen=True)
class LocalContext:
    """Minimal local scope extracted from a TaskGraph for LLM repair.

    Contains only the failed node, its direct siblings, and its parent
    (if identifiable). This prevents context-window overflow and the
    "lost in the middle" attention dilution problem.
    """

    failed_node: TaskNode
    siblings: Sequence[TaskNode]
    parent_id: str | None


def extract_local_context(graph: TaskGraph, failed_task_id: str) -> LocalContext:
    """Topological pruning: extract the failed node and its local neighborhood.

    Instead of passing the entire TaskGraph (which grows non-linearly with HTN
    decomposition depth), we extract only:
    - The failed node itself
    - Its direct siblings (same-level peers sharing the same parent method)
    - The parent compound task ID (if identifiable)

    This provides sufficient context for the LLM to propose a local fix.
    """
    task_by_id: dict[str, TaskNode] = {t.id: t for t in graph.tasks}

    if failed_task_id not in task_by_id:
        raise ValueError(f"task {failed_task_id} not in graph")

    failed_node = task_by_id[failed_task_id]

    parent_id: str | None = None
    siblings: list[TaskNode] = []

    for method_name, subtasks in graph.methods.items():
        subtask_ids = {t.id for t in subtasks}
        if failed_task_id in subtask_ids:
            parent_candidates = [t for t in graph.tasks if t.method == method_name]
            if parent_candidates:
                parent_id = parent_candidates[0].id
            siblings = [t for t in subtasks if t.id != failed_task_id]
            break

    if not siblings:
        idx = next(
            (i for i, t in enumerate(graph.tasks) if t.id == failed_task_id),
            -1,
        )
        if idx >= 0:
            lo = max(0, idx - 1)
            hi = min(len(graph.tasks), idx + 2)
            siblings = [graph.tasks[i] for i in range(lo, hi) if i != idx]

    return LocalContext(
        failed_node=failed_node,
        siblings=tuple(siblings),
        parent_id=parent_id,
    )
