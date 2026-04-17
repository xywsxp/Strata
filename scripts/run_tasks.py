"""Batch task executor for OSWorld evaluation.

Loads task definitions from ``tasks/*.toml``, runs each through the agent
orchestrator, optionally executes setup/verify commands, and writes an
aggregated JSON report.

Usage::

    uv run python scripts/run_tasks.py tasks/create-hello-txt.toml
    uv run python scripts/run_tasks.py 'tasks/*.toml'
    uv run python scripts/run_tasks.py --tag smoke
    uv run python scripts/run_tasks.py --config ./config.toml --report-dir reports/
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from strata.core.config import load_config
from strata.env.factory import EnvironmentFactory
from strata.harness.orchestrator import AgentOrchestrator, ExecutionResult
from strata.tasks import TaskFile, TaskFileError

# ── Auto-confirming UI (reused from agent_e2e.py pattern) ──


class _AutoUI:
    """Non-interactive AgentUI: auto-confirms, prints progress."""

    def __init__(self) -> None:
        self._tasks_seen = 0

    @property
    def interrupted(self) -> bool:
        return False

    def display_plan(self, graph: object) -> None:
        from strata.core.types import TaskGraph

        assert isinstance(graph, TaskGraph)
        print(f"    Plan: {len(graph.tasks)} tasks", flush=True)

    def confirm_plan(self) -> bool:
        return True

    def confirm_resume(self, saved_goal: str, task_count: int) -> bool:
        return False

    def display_progress(self, task_id: str, state: object) -> None:
        self._tasks_seen += 1
        print(f"      [{state}] {task_id}", flush=True)

    def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]:
        print(f"      [err] {task_id}: {error}", flush=True)
        return "skip"

    def handle_destructive(self, description: str) -> bool:
        return True


# ── Shell execution helper ──


@dataclass(frozen=True)
class ShellResult:
    stdout: str
    stderr: str
    returncode: int


def _run_shell_host(command: str, timeout: float) -> ShellResult:
    """Run a shell command on the host."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ShellResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(stdout="", stderr="timeout", returncode=-1)


# ── Task report ──


@dataclass(frozen=True)
class TaskReport:
    task_id: str
    goal: str
    verdict: Literal["PASS", "FAIL", "ERROR", "TIMEOUT"]
    duration_s: float
    run_dir: str
    setup_output: str | None
    verify_output: str | None
    error: str | None


def _run_single(
    task: TaskFile,
    config_path: str,
) -> TaskReport:
    """Execute a single task end-to-end: setup → run_goal → verify."""
    cfg = load_config(config_path)
    bundle = EnvironmentFactory.create(cfg)
    ui = _AutoUI()
    orch = AgentOrchestrator(config=cfg, bundle=bundle, ui=ui)

    t0 = time.monotonic()
    setup_out: str | None = None
    verify_out: str | None = None
    run_dir = ""

    # ── setup ──
    if task.setup:
        for cmd in task.setup.commands:
            sr = _run_shell_host(cmd, timeout=30.0)
            setup_out = (setup_out or "") + sr.stdout + sr.stderr
            if sr.returncode != 0:
                return TaskReport(
                    task_id=task.id,
                    goal=task.goal,
                    verdict="ERROR",
                    duration_s=time.monotonic() - t0,
                    run_dir=run_dir,
                    setup_output=setup_out,
                    verify_output=None,
                    error=f"setup command failed (rc={sr.returncode}): {cmd}",
                )

    # ── run goal ──
    try:
        result: ExecutionResult = orch.run_goal(task.goal)
    except Exception as exc:
        return TaskReport(
            task_id=task.id,
            goal=task.goal,
            verdict="ERROR",
            duration_s=time.monotonic() - t0,
            run_dir=run_dir,
            setup_output=setup_out,
            verify_output=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    if result.final_state != "COMPLETED":
        err_msg = f"{type(result.error).__name__}: {result.error}" if result.error else None
        return TaskReport(
            task_id=task.id,
            goal=task.goal,
            verdict="FAIL",
            duration_s=time.monotonic() - t0,
            run_dir=run_dir,
            setup_output=setup_out,
            verify_output=None,
            error=err_msg,
        )

    # ── verify ──
    if task.verify:
        vr = _run_shell_host(task.verify.command, timeout=30.0)
        verify_out = vr.stdout

        if (
            task.verify.expected_exit_code is not None
            and vr.returncode != task.verify.expected_exit_code
        ):
            return TaskReport(
                task_id=task.id,
                goal=task.goal,
                verdict="FAIL",
                duration_s=time.monotonic() - t0,
                run_dir=run_dir,
                setup_output=setup_out,
                verify_output=verify_out,
                error=(
                    f"verify exit code {vr.returncode} != expected {task.verify.expected_exit_code}"
                ),
            )

        if task.verify.expected_stdout_regex is not None and not re.search(
            task.verify.expected_stdout_regex, vr.stdout
        ):
            return TaskReport(
                task_id=task.id,
                goal=task.goal,
                verdict="FAIL",
                duration_s=time.monotonic() - t0,
                run_dir=run_dir,
                setup_output=setup_out,
                verify_output=verify_out,
                error=(f"verify regex {task.verify.expected_stdout_regex!r} did not match stdout"),
            )

    return TaskReport(
        task_id=task.id,
        goal=task.goal,
        verdict="PASS",
        duration_s=time.monotonic() - t0,
        run_dir=run_dir,
        setup_output=setup_out,
        verify_output=verify_out,
        error=None,
    )


def _write_report(reports: list[TaskReport], report_dir: Path) -> Path:
    """Write aggregated JSON report."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M")
    path = report_dir / f"{stamp}.json"
    payload = {
        "generated_at": stamp,
        "tasks": [
            {
                "task_id": r.task_id,
                "goal": r.goal,
                "verdict": r.verdict,
                "duration_s": round(r.duration_s, 2),
                "run_dir": r.run_dir,
                "error": r.error,
            }
            for r in reports
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OSWorld evaluation tasks")
    parser.add_argument("files", nargs="*", help="Task TOML files (glob-expanded)")
    parser.add_argument("--tag", help="Filter tasks by tag")
    parser.add_argument("--config", default="./config.toml", help="Config path")
    parser.add_argument("--report-dir", default="reports/", help="Report output dir")
    args = parser.parse_args(argv)

    # ── resolve task files ──
    paths: list[Path] = []
    for pattern in args.files:
        expanded = glob.glob(pattern)
        for f in sorted(expanded):
            p = Path(f)
            if p.is_file() and p.suffix == ".toml":
                paths.append(p)

    if not paths:
        tasks_dir = Path("tasks")
        if tasks_dir.is_dir():
            paths = sorted(tasks_dir.glob("*.toml"))

    if not paths:
        print("No task files found.", file=sys.stderr)
        return 2

    try:
        tasks = list(TaskFile.load_many(paths))
    except TaskFileError as exc:
        print(f"Error loading tasks: {exc}", file=sys.stderr)
        return 2

    if args.tag:
        tasks = [t for t in tasks if args.tag in t.tags]
        if not tasks:
            print(f"No tasks match tag {args.tag!r}", file=sys.stderr)
            return 2

    print(f"[=] {len(tasks)} task(s) to run\n")

    reports: list[TaskReport] = []
    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task.id}: {task.goal}")
        try:
            report = _run_single(task, args.config)
        except Exception as exc:
            traceback.print_exc()
            report = TaskReport(
                task_id=task.id,
                goal=task.goal,
                verdict="ERROR",
                duration_s=0.0,
                run_dir="",
                setup_output=None,
                verify_output=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        reports.append(report)
        mark = "✓" if report.verdict == "PASS" else "✗"
        suffix = f"  ({report.error})" if report.error else ""
        print(f"  {mark} {report.verdict} in {report.duration_s:.1f}s{suffix}\n")

    report_path = _write_report(reports, Path(args.report_dir))
    print(f"Report written to {report_path}")

    # ── summary ──
    passed = sum(1 for r in reports if r.verdict == "PASS")
    failed = len(reports) - passed
    print(f"\n[=] {passed}/{len(reports)} PASS, {failed} FAIL/ERROR")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
