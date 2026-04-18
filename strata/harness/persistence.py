"""Atomic persistence — tmp + fsync + replace, checkpoint save/load."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import icontract

from strata.core._validators import VALID_GLOBAL_STATES, VALID_TASK_STATES, validate_literal
from strata.core.errors import PersistenceSchemaVersionError, SerializationError
from strata.core.types import (
    GlobalState,
    TaskGraph,
    TaskState,
    task_graph_from_dict,
    task_graph_to_dict,
)

CHECKPOINT_SCHEMA_VERSION = 1
_SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


@icontract.invariant(
    lambda self: self.schema_version >= 1,
    "schema_version must be positive",
)
@dataclass(frozen=True)
class Checkpoint:
    global_state: GlobalState
    task_states: Mapping[str, TaskState]
    context: Mapping[str, object]
    task_graph: TaskGraph
    timestamp: float
    schema_version: int = CHECKPOINT_SCHEMA_VERSION


@icontract.require(
    lambda path: Path(path).parent.is_dir(),
    "parent directory must exist",
)
@icontract.ensure(
    lambda path, content: Path(path).read_bytes() == content,
    "file content must match after write",
)
def atomic_write(path: str, content: bytes) -> None:
    """Write *content* to *path* atomically (tmp + fsync + replace).

    Cleanup invariants (guaranteed even on KeyboardInterrupt / SystemExit):
    - The fd is closed exactly once.
    - The tmp file is unlinked iff the final replace did not succeed.
    """
    parent = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    closed = False
    replaced = False
    try:
        try:
            os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
            closed = True
        os.replace(tmp_path, path)
        replaced = True
    finally:
        if not closed:
            with contextlib.suppress(OSError):
                os.close(fd)
        if not replaced:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


def _checkpoint_to_dict(cp: Checkpoint) -> dict[str, object]:
    return {
        "schema_version": cp.schema_version,
        "global_state": cp.global_state,
        "task_states": dict(cp.task_states),
        "context": dict(cp.context),
        "task_graph": task_graph_to_dict(cp.task_graph),
        "timestamp": cp.timestamp,
    }


def _checkpoint_from_dict(d: Mapping[str, object]) -> Checkpoint:
    if "schema_version" not in d:
        raise PersistenceSchemaVersionError(
            "checkpoint missing schema_version field; refusing to load (fail-fast)"
        )
    version_raw = d["schema_version"]
    if not isinstance(version_raw, int) or version_raw not in _SUPPORTED_SCHEMA_VERSIONS:
        raise PersistenceSchemaVersionError(
            f"unsupported checkpoint schema_version={version_raw!r}; "
            f"supported={sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
        )

    task_states_raw = d.get("task_states", {})
    task_states: dict[str, TaskState] = {}
    if isinstance(task_states_raw, dict):
        for k, v in task_states_raw.items():
            task_states[str(k)] = cast(
                TaskState,
                validate_literal(str(v), VALID_TASK_STATES, "task_state", fallback="PENDING"),
            )

    ctx_raw = d.get("context", {})
    context = dict(ctx_raw) if isinstance(ctx_raw, dict) else {}

    graph_raw = d.get("task_graph", {})
    graph_dict = dict(graph_raw) if isinstance(graph_raw, dict) else {}

    try:
        task_graph = task_graph_from_dict(graph_dict)
    except SerializationError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise SerializationError(f"failed to deserialize task_graph: {e}") from e

    return Checkpoint(
        global_state=cast(
            GlobalState,
            validate_literal(
                str(d.get("global_state", "INIT")),
                VALID_GLOBAL_STATES,
                "global_state",
                fallback="INIT",
            ),
        ),
        task_states=task_states,
        context=context,
        task_graph=task_graph,
        timestamp=(
            float(ts_raw) if isinstance((ts_raw := d.get("timestamp")), (int, float)) else 0.0
        ),
        schema_version=version_raw,
    )


class PersistenceManager:
    """Save and load execution checkpoints with optional multi-version history."""

    def __init__(
        self,
        state_dir: str,
        max_checkpoint_history: int = 1,
    ) -> None:
        self._state_dir = state_dir
        self._max_history = max(1, max_checkpoint_history)
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        existing = self._scan_existing_versions()
        self._version = max(existing) if existing else 0

    def _scan_existing_versions(self) -> list[int]:
        """Read versioned checkpoint filenames and return their version numbers."""
        versions: list[int] = []
        for f in Path(self._state_dir).glob("checkpoint_v*.json"):
            try:
                v = int(f.stem.split("_v")[1])
                versions.append(v)
            except (IndexError, ValueError):
                continue
        return versions

    @property
    def _checkpoint_path(self) -> str:
        return os.path.join(self._state_dir, "checkpoint.json")

    def _versioned_path(self, version: int) -> str:
        return os.path.join(self._state_dir, f"checkpoint_v{version}.json")

    @property
    def current_version(self) -> int:
        return self._version

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        data = json.dumps(_checkpoint_to_dict(checkpoint), ensure_ascii=False)
        encoded = data.encode("utf-8")
        atomic_write(self._checkpoint_path, encoded)
        self._version += 1
        if self._max_history > 1:
            atomic_write(self._versioned_path(self._version), encoded)
            self._gc_old_versions()

    def load_checkpoint(self, version: int | None = None) -> Checkpoint | None:
        path = self._versioned_path(version) if version is not None else self._checkpoint_path
        if not os.path.exists(path):
            return None
        raw = Path(path).read_text(encoding="utf-8")
        return _checkpoint_from_dict(json.loads(raw))

    def list_versions(self) -> list[int]:
        """Return sorted list of available checkpoint version numbers."""
        return sorted(self._scan_existing_versions())

    def clear_checkpoint(self) -> None:
        path = self._checkpoint_path
        if os.path.exists(path):
            os.unlink(path)
        for f in Path(self._state_dir).glob("checkpoint_v*.json"):
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
        self._version = 0

    def _gc_old_versions(self) -> None:
        """Remove versions exceeding ``max_checkpoint_history``."""
        versions = self.list_versions()
        while len(versions) > self._max_history:
            oldest = versions.pop(0)
            p = self._versioned_path(oldest)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(p)
