"""Unified run-directory layout and lifecycle helpers.

All runtime artefacts (checkpoints, audit logs, LLM transcripts, recordings,
screenshots, manifests) live under a single *run root*. Each ``run_goal``
invocation creates a timestamped sub-directory; a ``current`` symlink always
points to the latest run. Old runs are garbage-collected on successful
completion.

``PathsConfig`` is the user-facing knob (``[paths]`` in ``config.toml``).
``RunDirLayout`` is the internal value object consumed by every component that
produces artefacts.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import icontract

# ── Configuration value object ──


@dataclass(frozen=True)
class PathsConfig:
    """User-facing ``[paths]`` configuration."""

    run_root: str
    keep_last_runs: int

    def __post_init__(self) -> None:
        if not self.run_root.strip():
            raise ValueError("paths.run_root must be a non-empty string")
        if self.keep_last_runs < 0:
            raise ValueError("paths.keep_last_runs must be >= 0")


# ── Run directory layout ──


@dataclass(frozen=True)
class RunDirLayout:
    """Frozen path bundle for a single agent run.

    Every component that writes artefacts receives the relevant ``Path`` from
    this object instead of computing its own.
    """

    run_root: Path
    run_dir: Path
    checkpoint_dir: Path
    checkpoint_path: Path
    audit_log_path: Path
    context_dir: Path
    llm_dir: Path
    screenshots_dir: Path
    recordings_dir: Path
    logs_dir: Path
    manifest_path: Path

    @classmethod
    @icontract.require(lambda goal: len(goal.strip()) > 0, "goal must be non-empty")
    def create(cls, paths_config: PathsConfig, goal: str) -> RunDirLayout:
        """Derive a complete layout for *goal* without touching the filesystem."""
        root = Path(paths_config.run_root).expanduser().resolve()
        goal_hash = hashlib.sha256(goal.encode()).hexdigest()[:8]
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
        run_name = f"{stamp}_{goal_hash}"
        run_dir = root / "runs" / run_name

        checkpoint_dir = root
        return cls(
            run_root=root,
            run_dir=run_dir,
            checkpoint_dir=checkpoint_dir,
            checkpoint_path=checkpoint_dir / "checkpoint.json",
            audit_log_path=run_dir / "audit.jsonl",
            context_dir=run_dir / "context_snapshots",
            llm_dir=run_dir / "llm",
            screenshots_dir=run_dir / "screenshots",
            recordings_dir=run_dir / "recordings",
            logs_dir=run_dir / "logs",
            manifest_path=run_dir / "manifest.json",
        )

    @icontract.ensure(
        lambda self: all(
            p.is_dir()
            for p in (
                self.run_dir,
                self.context_dir,
                self.llm_dir,
                self.screenshots_dir,
                self.recordings_dir,
                self.logs_dir,
            )
        ),
        "all sub-directories must exist after ensure_dirs",
    )
    def ensure_dirs(self) -> None:
        """Create the full directory tree (idempotent)."""
        for d in (
            self.run_dir,
            self.checkpoint_dir,
            self.context_dir,
            self.llm_dir,
            self.screenshots_dir,
            self.recordings_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def link_current(self) -> None:
        """Point ``<run_root>/current`` symlink to this run's directory.

        Silently degrades to a stderr warning on platforms or permission
        configurations where symlinks are unsupported.
        """
        link = self.run_root / "current"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(self.run_dir)
        except OSError as exc:
            with contextlib.suppress(Exception):
                print(
                    f"[strata.paths] cannot create symlink {link} -> {self.run_dir}: {exc}",
                    file=sys.stderr,
                )

    def write_manifest(
        self,
        goal: str,
        config_snapshot: Mapping[str, object],
        started_at: float,
    ) -> None:
        """Persist a JSON manifest summarising this run."""
        payload = {
            "goal": goal,
            "run_dir": str(self.run_dir),
            "started_at": started_at,
            "finished_at": time.time(),
            "config_snapshot": dict(config_snapshot),
        }
        try:
            self.manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            with contextlib.suppress(Exception):
                print(
                    f"[strata.paths] cannot write manifest: {exc}",
                    file=sys.stderr,
                )


# ── Garbage collection ──


@icontract.require(lambda keep: keep >= 0, "keep must be non-negative")
def gc_old_runs(run_root: Path, keep: int) -> Sequence[Path]:
    """Delete the oldest run directories under ``run_root/runs/``, keeping *keep*.

    Returns the list of removed directories.  ``keep=0`` means "keep all" (no
    deletion).  If ``run_root/runs/`` does not exist, returns an empty sequence.
    """
    if keep == 0:
        return ()

    runs_dir = run_root / "runs"
    if not runs_dir.is_dir():
        return ()

    entries = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )

    to_remove = entries[: max(0, len(entries) - keep)]
    removed: list[Path] = []
    for d in to_remove:
        try:
            shutil.rmtree(d)
            removed.append(d)
        except OSError as exc:
            with contextlib.suppress(Exception):
                print(
                    f"[strata.paths] gc: cannot remove {d}: {exc}",
                    file=sys.stderr,
                )
    return tuple(removed)
