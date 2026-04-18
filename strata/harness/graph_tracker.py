"""Dynamic graph version tracker with Mermaid export.

``GraphTracker`` holds the current ``TaskGraph`` and a history stack.
Every ``update`` bumps the version and optionally writes a JSON + Mermaid
snapshot to disk.  ``NullGraphTracker`` is a zero-side-effect stand-in for
tests that don't care about graph history.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import icontract

from strata.core.types import TaskGraph, TaskState, task_graph_to_dict

# ── Mermaid rendering helpers ──

_STATE_COLORS: Mapping[str, str] = {
    "PENDING": "#adb5bd",
    "RUNNING": "#74c0fc",
    "SUCCEEDED": "#69db7c",
    "FAILED": "#ff6b6b",
    "SKIPPED": "#ffe066",
}


def _sanitize_mermaid_id(raw: str) -> str:
    """Replace characters illegal in Mermaid node IDs with underscores."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in raw)


# ── GraphTracker ──


class GraphTracker:
    """Mutable container tracking the current TaskGraph and its version history.

    CONVENTION: GraphTracker is a mutable state container — not frozen.
    Similar semantics to WorkingMemory.
    """

    def __init__(self, run_dir: Path | None = None) -> None:
        self._current: TaskGraph | None = None
        self._version: int = 0
        self._history: list[tuple[TaskGraph, str, float]] = []
        self._run_dir = run_dir
        if run_dir is not None:
            (run_dir / "graph").mkdir(parents=True, exist_ok=True)

    def current(self) -> TaskGraph | None:
        """Return the current graph, or None before first update."""
        return self._current

    def version(self) -> int:
        """Return the current version number (0 before any update)."""
        return self._version

    @icontract.require(lambda new_graph: new_graph is not None, "graph must not be None")
    @icontract.ensure(lambda self: self._version > 0, "version must be positive after update")
    def update(self, new_graph: TaskGraph, reason: str) -> None:
        """Replace the current graph, bump version, record in history."""
        self._version += 1
        self._current = new_graph
        self._history.append((new_graph, reason, time.time()))

    def history(self) -> Sequence[tuple[TaskGraph, str, float]]:
        """Return the full version history as (graph, reason, timestamp) tuples."""
        return list(self._history)

    def export_snapshot(self, task_states: Mapping[str, TaskState]) -> None:
        """Write JSON + Mermaid files for the current version to run_dir/graph/."""
        if self._run_dir is None or self._current is None:
            return
        graph_dir = self._run_dir / "graph"
        v = self._version

        # JSON snapshot
        json_path = graph_dir / f"v{v}.json"
        json_path.write_text(
            json.dumps(task_graph_to_dict(self._current), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Mermaid snapshot
        mermaid_path = graph_dir / f"v{v}.mermaid"
        mermaid_path.write_text(self.render_mermaid(task_states), encoding="utf-8")

        # Task states snapshot
        states_path = graph_dir / f"v{v}_states.json"
        states_path.write_text(
            json.dumps(dict(task_states), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def render_mermaid(self, task_states: Mapping[str, TaskState]) -> str:
        """Render the current graph as a Mermaid flowchart string."""
        if self._current is None:
            return "graph TD\n"
        lines: list[str] = ["graph TD"]
        for task in self._current.tasks:
            sid = _sanitize_mermaid_id(task.id)
            state = task_states.get(task.id, "PENDING")
            color = _STATE_COLORS.get(state, "#adb5bd")
            label = f"{task.id}({task.task_type})"
            lines.append(f'    {sid}["{label}"]')
            lines.append(f"    style {sid} fill:{color}")
        for task in self._current.tasks:
            sid = _sanitize_mermaid_id(task.id)
            for dep in task.depends_on:
                dep_id = _sanitize_mermaid_id(dep)
                lines.append(f"    {dep_id} --> {sid}")
        return "\n".join(lines) + "\n"


class NullGraphTracker(GraphTracker):
    """In-memory-only tracker — version/history kept in RAM, disk I/O silently skipped.

    Used when no ``run_dir`` is available (tests, headless debug runs).
    ``update`` and ``history`` work identically to ``GraphTracker``; only
    ``export_snapshot`` is suppressed so no files are written.
    """

    def __init__(self) -> None:
        super().__init__(run_dir=None)

    def update(self, new_graph: TaskGraph, reason: str) -> None:
        """Track version/history in memory; skip disk I/O."""
        self._version += 1
        self._current = new_graph
        self._history.append((new_graph, reason, time.time()))

    def export_snapshot(self, task_states: Mapping[str, TaskState]) -> None:
        """No-op — NullGraphTracker never writes to disk."""
