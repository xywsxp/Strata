"""Command-line interaction loop.

Handles: goal input, plan display, confirmation, progress reporting,
error decisions, and SIGINT for graceful shutdown.

# CONVENTION: high 档 auto_confirm 下 handle_error 默认返回 skip 决策
# — 可由 StrataConfig.max_loop_iterations 等后续配置覆盖；当前固定 skip。
"""

from __future__ import annotations

import contextlib
import signal
import types
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Literal

import icontract

from strata.core.config import StrataConfig
from strata.core.types import TaskGraph, TaskState
from strata.env.protocols import EnvironmentBundle

if TYPE_CHECKING:
    from strata.harness.orchestrator import AgentOrchestrator

AutoConfirmLevel = Literal["none", "low", "medium", "high"]

SigintHandler = Callable[[int, types.FrameType | None], None]


@contextlib.contextmanager
def _sigint_scope(handler: SigintHandler) -> Iterator[None]:
    """Install a SIGINT handler for the duration of the with-block, then
    restore the previous handler on exit. Replaces the previous global
    :func:`signal.signal` side-effect at CLI construction.
    """
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


class CLI:
    """Interactive CLI for the Strata agent."""

    def __init__(
        self,
        config: StrataConfig,
        bundle: EnvironmentBundle | None = None,
    ) -> None:
        # CONVENTION: bundle 形参历史遗留 — 由 Orchestrator 持有；此处保留供测试
        # 传 None 兼容旧构造路径，不在生产中被使用。
        self._config = config
        self._bundle = bundle
        self._interrupted = False
        self._first_plan = True

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    @property
    def auto_confirm_level(self) -> AutoConfirmLevel:
        return self._config.auto_confirm_level

    @icontract.require(
        lambda orchestrator: orchestrator is not None,
        "orchestrator must be provided",
    )
    def run(self, orchestrator: AgentOrchestrator) -> None:
        """Main REPL loop: read goal → hand off to orchestrator → repeat.

        SIGINT is captured via :func:`_sigint_scope` so the handler is only
        installed for the duration of the loop. The orchestrator drives the
        full plan/confirm/execute/recover lifecycle; this method is purely the
        ``input()`` ↔ Orchestrator adapter.
        """
        with _sigint_scope(self._handle_sigint):
            self._print("[Strata] Ready. Type a goal or 'quit' to exit.")
            while not self._interrupted:
                try:
                    goal = input("\n[Goal] > ").strip()
                except EOFError:
                    break
                if not goal or goal.lower() in ("quit", "exit", "q"):
                    break
                self._print(f"[Strata] Received goal: {goal}")
                result = orchestrator.run_goal(goal)
                if result.final_state == "COMPLETED":
                    self._print(f"[Strata] Goal completed: {goal}")
                else:
                    err = result.error
                    err_msg = f"{type(err).__name__}: {err}" if err is not None else "unknown error"
                    self._print(f"[Strata] Goal failed: {err_msg}")

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
        """Ask user to confirm the plan, gated by ``auto_confirm_level``:

        * ``none``  — always ask.
        * ``low``   — ask only the first time; subsequent plans auto-confirm.
        * ``medium``— auto-confirm (yes).
        * ``high``  — auto-confirm (yes).
        """
        level = self.auto_confirm_level
        if level in ("medium", "high"):
            return True
        if level == "low" and not self._first_plan:
            return True
        self._first_plan = False
        try:
            answer = input("[Confirm] Execute this plan? (y/n) > ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

    def handle_destructive(self, description: str) -> bool:
        """Ask the user to allow a destructive action.

        * ``none`` / ``low`` / ``medium`` — warn and ask (force confirmation).
        * ``high`` — warn and auto-allow.
        """
        self._print(f"[Warning] Destructive action: {description}")
        if self.auto_confirm_level == "high":
            self._print("[Warning] auto_confirm_level=high -> auto-allowed")
            return True
        try:
            answer = input("[Confirm] Allow? (y/n) > ").strip().lower()
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
        """Ask the user how to handle a task error, gated by level:

        * ``none`` / ``low`` — ask.
        * ``medium`` — return ``retry`` once; on a subsequent call for the
          same task, caller is expected to surface another error and we ask.
          (Current implementation: return ``retry`` on first call, then ask.)
        * ``high`` — return ``skip`` by default.
        """
        level = self.auto_confirm_level
        if level == "high":
            self._print(f"[Error] Task {task_id} failed: {error} — auto_confirm=high, skipping")
            return "skip"
        if level == "medium" and not getattr(self, "_retried_" + task_id, False):
            setattr(self, "_retried_" + task_id, True)
            self._print(f"[Error] Task {task_id} failed: {error} — auto-retrying once")
            return "retry"
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

    def _handle_sigint(self, signum: int, frame: types.FrameType | None) -> None:
        self._interrupted = True
        self._print("\n[Strata] Interrupt received. Finishing current task...")

    def _print(self, msg: str) -> None:
        print(msg, flush=True)
