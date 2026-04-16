"""Atomic persistence — tmp + fsync + replace, checkpoint save/load."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import icontract

from strata.core.types import (
    GlobalState,
    TaskGraph,
    TaskState,
    task_graph_from_dict,
    task_graph_to_dict,
)


@dataclass(frozen=True)
class Checkpoint:
    global_state: GlobalState
    task_states: Mapping[str, TaskState]
    context: Mapping[str, object]
    task_graph: TaskGraph
    timestamp: float


@icontract.require(
    lambda path: Path(path).parent.is_dir(),
    "parent directory must exist",
)
@icontract.ensure(
    lambda path, content: Path(path).read_bytes() == content,
    "file content must match after write",
)
@icontract.ensure(
    lambda path: not Path(path + ".tmp").exists(),
    "no .tmp residue after write",
)
def atomic_write(path: str, content: bytes) -> None:
    """Write *content* to *path* atomically (tmp + fsync + replace)."""
    parent = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        os.write(fd, content)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp_path, path)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _checkpoint_to_dict(cp: Checkpoint) -> dict[str, object]:
    return {
        "global_state": cp.global_state,
        "task_states": dict(cp.task_states),
        "context": dict(cp.context),
        "task_graph": task_graph_to_dict(cp.task_graph),
        "timestamp": cp.timestamp,
    }


def _checkpoint_from_dict(d: Mapping[str, object]) -> Checkpoint:
    task_states_raw = d.get("task_states", {})
    task_states: dict[str, TaskState] = {}
    if isinstance(task_states_raw, dict):
        for k, v in task_states_raw.items():
            task_states[str(k)] = str(v)  # type: ignore[assignment]

    ctx_raw = d.get("context", {})
    context = dict(ctx_raw) if isinstance(ctx_raw, dict) else {}

    graph_raw = d.get("task_graph", {})
    graph_dict = dict(graph_raw) if isinstance(graph_raw, dict) else {}

    return Checkpoint(
        global_state=str(d.get("global_state", "INIT")),  # type: ignore[arg-type]
        task_states=task_states,
        context=context,
        task_graph=task_graph_from_dict(graph_dict),
        timestamp=float(d.get("timestamp", 0.0)),  # type: ignore[arg-type]
    )


class PersistenceManager:
    """Save and load execution checkpoints."""

    def __init__(self, state_dir: str) -> None:
        self._state_dir = state_dir
        Path(state_dir).mkdir(parents=True, exist_ok=True)

    @property
    def _checkpoint_path(self) -> str:
        return os.path.join(self._state_dir, "checkpoint.json")

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        data = json.dumps(_checkpoint_to_dict(checkpoint), ensure_ascii=False)
        atomic_write(self._checkpoint_path, data.encode("utf-8"))

    def load_checkpoint(self) -> Checkpoint | None:
        path = self._checkpoint_path
        if not os.path.exists(path):
            return None
        raw = Path(path).read_text(encoding="utf-8")
        return _checkpoint_from_dict(json.loads(raw))

    def clear_checkpoint(self) -> None:
        path = self._checkpoint_path
        if os.path.exists(path):
            os.unlink(path)
