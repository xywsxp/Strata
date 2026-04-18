"""Rollback engine — task-level undo, checkpoint-level restore, graph-level revert."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import icontract

from strata.core.errors import DebugRollbackError
from strata.core.types import TaskGraph, TaskState
from strata.harness.graph_tracker import GraphTracker
from strata.harness.persistence import Checkpoint, PersistenceManager


@dataclass(frozen=True)
class UndoRecord:
    """One undo-able task completion snapshot."""

    task_id: str
    task_states_before: Mapping[str, TaskState]
    checkpoint_version: int


class RollbackEngine:
    """Three-level rollback: task, checkpoint, graph.

    # CONVENTION: RollbackEngine is stateful — holds an undo stack and
    # references to PersistenceManager + GraphTracker for deeper rollbacks.
    """

    def __init__(
        self,
        persistence: PersistenceManager,
        graph_tracker: GraphTracker,
        max_undo: int = 50,
    ) -> None:
        self._persistence = persistence
        self._graph_tracker = graph_tracker
        self._max_undo = max_undo
        self._undo_stack: list[UndoRecord] = []

    def push_undo(
        self,
        task_id: str,
        task_states_before: Mapping[str, TaskState],
    ) -> None:
        """Record a task completion for potential undo."""
        record = UndoRecord(
            task_id=task_id,
            task_states_before=dict(task_states_before),
            checkpoint_version=self._persistence.current_version,
        )
        self._undo_stack.append(record)
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)

    @icontract.require(
        lambda self, n: n > 0,
        "n must be positive",
    )
    def undo_tasks(self, n: int = 1) -> UndoRecord:
        """Pop the last *n* undo records; return the oldest popped for restore.

        Raises ``DebugRollbackError`` if the undo stack has fewer than *n* records.
        """
        if len(self._undo_stack) < n:
            raise DebugRollbackError(
                f"requested undo of {n} tasks but only {len(self._undo_stack)} on stack"
            )
        target: UndoRecord | None = None
        for _ in range(n):
            target = self._undo_stack.pop()
        assert target is not None
        return target

    def rollback_to_checkpoint(self, version: int) -> Checkpoint:
        """Restore state from a specific checkpoint version.

        Raises ``DebugRollbackError`` if the version does not exist.
        """
        cp = self._persistence.load_checkpoint(version=version)
        if cp is None:
            available = self._persistence.list_versions()
            raise DebugRollbackError(
                f"checkpoint version {version} not found; available: {available}"
            )
        self._undo_stack.clear()
        return cp

    def rollback_graph(self, steps: int = 1) -> TaskGraph:
        """Revert the TaskGraph by *steps* in the GraphTracker history.

        Raises ``DebugRollbackError`` if not enough history.
        """
        history = self._graph_tracker.history()
        if len(history) < steps + 1:
            raise DebugRollbackError(
                f"requested graph rollback of {steps} steps but only "
                f"{len(history)} versions in history"
            )
        target_graph, reason, _ts = history[-(steps + 1)]
        self._graph_tracker.update(target_graph, f"rollback_{steps}_steps")
        return target_graph

    @property
    def undo_depth(self) -> int:
        return len(self._undo_stack)

    def list_undo_stack(self) -> Sequence[UndoRecord]:
        return list(self._undo_stack)
