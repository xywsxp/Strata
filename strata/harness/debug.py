"""Debug helper: structured logging for the inner action loop.

``ActionDebugLogger`` writes machine-readable JSON Lines alongside
the human-readable audit log.  Each entry captures the action dispatched,
pre/post screenshots (optional base64), and the result.

This module is **intentionally minimal** — no image processing, no
screenshot capture; callers pass raw bytes (or ``None``).
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Final

import icontract

# ── Entry schema ──

_SCHEMA_VERSION: Final[int] = 1


# ── Logger ──


class ActionDebugLogger:
    """Write one JSON-Lines entry per primitive action execution.

    File is created lazily on first ``log`` call.  If *run_dir* is ``None``,
    all writes are silently dropped (zero side-effects mode).
    """

    def __init__(self, run_dir: Path | None = None) -> None:
        self._run_dir = run_dir
        self._path: Path | None = None

    def _ensure_open(self) -> Path | None:
        if self._run_dir is None:
            return None
        if self._path is None:
            self._path = self._run_dir / "debug_actions.jsonl"
            self._path.parent.mkdir(parents=True, exist_ok=True)
        return self._path

    @icontract.require(lambda action: len(action.strip()) > 0, "action must be non-empty")
    def log(
        self,
        action: str,
        params: dict[str, object] | None = None,
        result: str | None = None,
        error: str | None = None,
        pre_screenshot: bytes | None = None,
        post_screenshot: bytes | None = None,
    ) -> None:
        """Append a debug entry.  No-op when run_dir is None."""
        path = self._ensure_open()
        if path is None:
            return

        entry: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "timestamp": time.time(),
            "action": action,
            "params": params or {},
            "result": result,
            "error": error,
        }
        if pre_screenshot is not None:
            entry["pre_screenshot_b64"] = base64.b64encode(pre_screenshot).decode("ascii")
        if post_screenshot is not None:
            entry["post_screenshot_b64"] = base64.b64encode(post_screenshot).decode("ascii")

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


class NullActionDebugLogger(ActionDebugLogger):
    """Zero-side-effect stand-in for tests."""

    def __init__(self) -> None:
        super().__init__(run_dir=None)

    def log(
        self,
        action: str,
        params: dict[str, object] | None = None,
        result: str | None = None,
        error: str | None = None,
        pre_screenshot: bytes | None = None,
        post_screenshot: bytes | None = None,
    ) -> None:
        """Silently drop all entries."""
