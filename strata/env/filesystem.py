"""Sandboxed file-system adapter.

All path operations are delegated to SandboxGuard.check_path() — this adapter
contains zero path-checking logic of its own.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

import icontract

from strata.core.errors import SandboxViolationError
from strata.core.sandbox import SandboxGuard
from strata.core.types import FileInfo


class SandboxedFileSystemAdapter:
    """IFileSystemAdapter implementation with sandbox enforcement."""

    def __init__(self, guard: SandboxGuard, trash_dir: str) -> None:
        self._guard = guard
        self._trash_dir = os.path.expanduser(trash_dir)
        Path(self._trash_dir).mkdir(parents=True, exist_ok=True)

    def read_file(self, path: str) -> str:
        checked = self._guard.check_path(path, write=False)
        return Path(checked).read_text(encoding="utf-8")

    @icontract.ensure(
        lambda path, content: Path(
            os.path.realpath(os.path.expanduser(path))
        ).read_text()
        == content,
        "written content must match",
    )
    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        checked = self._guard.check_path(path, write=True)
        Path(checked).parent.mkdir(parents=True, exist_ok=True)
        Path(checked).write_text(content, encoding=encoding)

    def list_directory(
        self, path: str, pattern: str | None = None
    ) -> Sequence[FileInfo]:
        checked = self._guard.check_path(path, write=False)
        p = Path(checked)
        entries = list(p.glob(pattern)) if pattern else list(p.iterdir())
        return tuple(self._file_info(e) for e in entries)

    def move_to_trash(self, path: str) -> str:
        checked = self._guard.check_path(path, write=True)
        src = Path(checked)
        if not src.exists():
            raise FileNotFoundError(f"no such file: {checked}")
        ts = int(time.time() * 1000)
        trash_name = f"{src.name}.{ts}"
        trash_dest = Path(self._trash_dir) / trash_name
        src.rename(trash_dest)

        sidecar = trash_dest.with_suffix(trash_dest.suffix + ".meta.json")
        sidecar.write_text(
            json.dumps({"original_path": checked, "trashed_at": ts}),
            encoding="utf-8",
        )
        return str(trash_dest)

    @icontract.ensure(lambda result: result is None, "副作用语义锚：无返回值")
    def restore_from_trash(self, trash_path: str) -> None:
        tp_checked = self._check_in_trash(trash_path)
        tp = Path(tp_checked)
        sidecar = tp.with_suffix(tp.suffix + ".meta.json")
        if not sidecar.exists():
            raise FileNotFoundError(f"no sidecar metadata for {trash_path}")
        meta = json.loads(sidecar.read_text())
        original = meta.get("original_path")
        if not isinstance(original, str) or not original.strip():
            raise SandboxViolationError(
                f"sidecar original_path missing or invalid in {sidecar}"
            )
        original_checked = self._guard.check_path(original, write=True)
        tp.rename(original_checked)
        sidecar.unlink()

    def _check_in_trash(self, path: str) -> str:
        """Verify *path* resolves to within the trash directory.

        The trash directory is a sandbox-adjacent trusted zone; attempting to
        restore a file whose path resolves outside it is treated as a sandbox
        boundary violation (attackers must not use restore to read arbitrary
        system files as "trash").
        """
        resolved = os.path.realpath(os.path.expanduser(path))
        trash_root = os.path.realpath(self._trash_dir)
        if resolved == trash_root or resolved.startswith(trash_root + os.sep):
            return resolved
        raise SandboxViolationError(
            f"trash path {resolved} is outside trash dir {trash_root}"
        )

    def get_file_info(self, path: str) -> FileInfo:
        checked = self._guard.check_path(path, write=False)
        return self._file_info(Path(checked))

    @staticmethod
    def _file_info(p: Path) -> FileInfo:
        stat = p.stat()
        return FileInfo(
            path=str(p),
            name=p.name,
            is_dir=p.is_dir(),
            size=stat.st_size,
            modified_at=stat.st_mtime,
        )
