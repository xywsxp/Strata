"""Working memory, sliding-window context, audit logging, and topological pruning.

WorkingMemory: variable bindings + FIFO fact slot.
ContextManager: sliding window over recent actions + compression trigger.
AuditLogger: JSON Lines audit trail with atomic append and sensitive redaction.
Topological pruning: extract_local_context for adjust_plan.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import icontract

from strata.core.config import MemoryConfig
from strata.core.types import TaskGraph, TaskNode
from strata.grounding.filter import redact


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


# ── Sliding-window context manager ──


class ContextManager:
    """Sliding window over recent task actions + compression trigger."""

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._memory = WorkingMemory(config)
        self._window: list[dict[str, object]] = []

    @property
    def memory(self) -> WorkingMemory:
        return self._memory

    def add_entry(self, entry: Mapping[str, object]) -> None:
        """Add an action entry to the sliding window."""
        self._window.append(dict(entry))
        while len(self._window) > self._config.sliding_window_size:
            self._window.pop(0)

    @icontract.require(lambda key: len(key.strip()) > 0, "key must be non-empty")
    def add_fact(self, key: str, value: str) -> None:
        self._memory.add_fact(key, value)

    @icontract.ensure(
        lambda self, result: len(result) <= self._config.max_facts_in_slot,
        "facts count within limit",
    )
    def get_facts(self) -> Sequence[ContextFact]:
        return self._memory.get_facts()

    @icontract.ensure(
        lambda self, result: len(result) <= self._config.sliding_window_size,
        "window size within limit",
    )
    def get_window(self) -> Sequence[Mapping[str, object]]:
        return list(self._window)

    def compress(self, snapshot_dir: str | None = None) -> None:
        """Save a snapshot of current context and trim old entries.

        Uses atomic write via the persistence module for safety.
        """
        if snapshot_dir is None:
            snapshot_dir = str(Path.home() / ".strata" / "context_snapshots")

        from strata.harness.persistence import atomic_write

        Path(snapshot_dir).mkdir(parents=True, exist_ok=True)
        snapshot_path = str(Path(snapshot_dir) / f"ctx_{int(time.time())}.json")
        snapshot_data = json.dumps(
            {
                "window": self._window,
                "facts": [
                    {"key": f.key, "value": f.value, "timestamp": f.timestamp}
                    for f in self._memory.get_facts()
                ],
                "variables": {k: str(v) for k, v in self._memory.get_variables().items()},
            },
            ensure_ascii=False,
        )
        atomic_write(snapshot_path, snapshot_data.encode("utf-8"))

    def clear(self) -> None:
        self._memory.clear()
        self._window.clear()


# ── Audit logger ──


class AuditLogger:
    """JSON Lines audit trail with sensitive-info redaction."""

    def __init__(self, log_path: str) -> None:
        self._log_path = log_path
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        task_id: str,
        action: str,
        params: Mapping[str, object],
        result: str,
        user_confirmed: bool = False,
    ) -> None:
        """Append one JSON line to the audit log."""
        entry = {
            "timestamp": time.time(),
            "task_id": redact(task_id),
            "action": redact(action),
            "params": {k: redact(str(v)) for k, v in params.items()},
            "result": redact(result),
            "user_confirmed": user_confirmed,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line)


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
