"""Command-line interaction loop.

Handles: goal input, plan display, confirmation, progress reporting,
error decisions, and SIGINT for graceful shutdown.
"""

from __future__ import annotations

import signal
from typing import Literal

import icontract

from strata.core.config import StrataConfig
from strata.core.types import TaskGraph, TaskState


class CLI:
    """Interactive CLI for the Strata agent."""

    def __init__(self, config: StrataConfig) -> None:
        self._config = config
        self._interrupted = False
        signal.signal(signal.SIGINT, self._handle_sigint)

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    def run(self) -> None:
        """Main REPL loop: read goal → plan → confirm → execute → repeat."""
        self._print("[Strata] Ready. Type a goal or 'quit' to exit.")
        while not self._interrupted:
            try:
                goal = input("\n[Goal] > ").strip()
            except EOFError:
                break
            if not goal or goal.lower() in ("quit", "exit", "q"):
                break
            self._print(f"[Strata] Received goal: {goal}")
            self._print("[Strata] Planning... (would call decompose_goal here)")

    def display_plan(self, graph: TaskGraph) -> None:
        """Print the task graph to stdout."""
        self._print(f"\n{'=' * 60}")
        self._print(f"  Plan: {graph.goal}")
        self._print(f"  Tasks: {len(graph.tasks)}")
        self._print(f"{'=' * 60}")
        for i, task in enumerate(graph.tasks, 1):
            status = "  " if task.task_type == "primitive" else "  [compound]"
            action_desc = task.action or task.method or task.task_type
            self._print(f"  {i}. [{task.id}]{status} {action_desc}")
            if task.depends_on:
                self._print(f"     depends: {', '.join(task.depends_on)}")
        self._print("")

    @icontract.ensure(lambda result: isinstance(result, bool), "must return bool")
    def confirm_plan(self) -> bool:
        """Ask user to confirm the plan. Returns True for yes, False for no."""
        try:
            answer = input("[Confirm] Execute this plan? (y/n) > ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

    def display_progress(self, task_id: str, state: TaskState) -> None:
        """Print task progress update."""
        icons: dict[str, str] = {
            "PENDING": ".",
            "RUNNING": ">",
            "SUCCEEDED": "+",
            "FAILED": "!",
            "SKIPPED": "-",
        }
        icon = icons.get(state, "?")
        self._print(f"  [{icon}] {task_id}: {state}")

    def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]:
        """Ask user how to handle a task error."""
        self._print(f"\n[Error] Task {task_id} failed: {error}")
        try:
            choice = input("[Action] (r)etry / (s)kip / (a)bort > ").strip().lower()
        except EOFError:
            return "abort"
        if choice in ("r", "retry"):
            return "retry"
        if choice in ("s", "skip"):
            return "skip"
        return "abort"

    def _handle_sigint(self, signum: int, frame: object) -> None:
        self._interrupted = True
        self._print("\n[Strata] Interrupt received. Finishing current task...")

    def _print(self, msg: str) -> None:
        print(msg, flush=True)
