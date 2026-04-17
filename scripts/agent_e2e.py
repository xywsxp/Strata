"""Real end-to-end driver: loads config.toml, builds EnvironmentBundle (with
live OSWorld GUI if enabled), and drives ``AgentOrchestrator.run_goal`` with
real LLM calls (no mocks anywhere).

# CONVENTION: scripts/agent_e2e.py — 冒烟脚本，非正式 eval：目的是验证
# __main__ → orchestrator → planner(LLM) → executor(env) 全链路真实运行；
# 不做打分统计、不做 PR 合入 CI、失败不抛 SystemExit 以便整批继续跑。

跑法::

    uv run python scripts/agent_e2e.py            # 默认全套 goals
    uv run python scripts/agent_e2e.py 'goal 1' 'goal 2'  # 自定义 goals
"""

from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass
from typing import Literal

from strata.core.config import load_config
from strata.env.factory import EnvironmentFactory
from strata.harness.orchestrator import AgentOrchestrator, ExecutionResult
from strata.interaction.cli import CLI


class AutoUI:
    """Non-interactive :class:`AgentUI` implementation.

    Prints progress to stdout and auto-confirms every prompt — exists because
    this host has no TTY for ``input()``. Never interrupts unless
    :attr:`should_stop` is externally toggled (we never do).
    """

    def __init__(self) -> None:
        self._tasks_seen = 0

    @property
    def interrupted(self) -> bool:
        return False

    def display_plan(self, graph: object) -> None:
        from strata.core.types import TaskGraph

        assert isinstance(graph, TaskGraph)
        print(f"\n  Plan for: {graph.goal}", flush=True)
        print(f"  Tasks: {len(graph.tasks)}", flush=True)
        for i, t in enumerate(graph.tasks, 1):
            action = t.action or t.method or t.task_type
            params_preview = ""
            if t.params:
                items = list(t.params.items())[:3]
                params_preview = "  " + " ".join(f"{k}={v!r}" for k, v in items)
                if len(t.params) > 3:
                    params_preview += " …"
            print(f"    {i:>2}. [{t.id}] {action}{params_preview}", flush=True)

    def confirm_plan(self) -> bool:
        print("  -> AutoUI: auto-confirm plan", flush=True)
        return True

    def confirm_resume(self, saved_goal: str, task_count: int) -> bool:
        print(f"  -> AutoUI: declining resume of {saved_goal!r} ({task_count} tasks)", flush=True)
        return False

    def display_progress(self, task_id: str, state: object) -> None:
        self._tasks_seen += 1
        print(f"    [{state}] {task_id}", flush=True)

    def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]:
        print(f"    [err] {task_id}: {type(error).__name__}: {error}", flush=True)
        print("    -> AutoUI: skip", flush=True)
        return "skip"

    def handle_destructive(self, description: str) -> bool:
        print(f"    [destructive] {description} -> AutoUI: allow", flush=True)
        return True


@dataclass
class GoalOutcome:
    goal: str
    final_state: str
    ok_tasks: int
    failed_tasks: int
    skipped_tasks: int
    duration_s: float
    error: str | None


def _summarize(result: ExecutionResult, elapsed: float, goal: str) -> GoalOutcome:
    ok = sum(1 for s in result.task_states.values() if s == "SUCCEEDED")
    failed = sum(1 for s in result.task_states.values() if s == "FAILED")
    skipped = sum(1 for s in result.task_states.values() if s == "SKIPPED")
    err_msg = f"{type(result.error).__name__}: {result.error}" if result.error else None
    return GoalOutcome(
        goal=goal,
        final_state=result.final_state,
        ok_tasks=ok,
        failed_tasks=failed,
        skipped_tasks=skipped,
        duration_s=elapsed,
        error=err_msg,
    )


DEFAULT_GOALS: tuple[str, ...] = (
    "Create a file at /tmp/strata_e2e_hello.txt containing the single word: hello",
    "List the contents of the /tmp directory and report the count of entries",
    "Read the file /etc/hostname and report its contents",
)


def main(goals: list[str]) -> int:
    cfg = load_config("./config.toml")
    print(f"[+] planner -> {cfg.roles.planner}  (model={cfg.providers[cfg.roles.planner].model})")
    print(f"[+] vision  -> {cfg.roles.vision}  (model={cfg.providers[cfg.roles.vision].model})")
    print(f"[+] OSWorld server = {cfg.osworld.server_url} (enabled={cfg.osworld.enabled})")

    bundle = EnvironmentFactory.create(cfg)
    print(f"[+] bundle.gui       = {type(bundle.gui).__name__}")
    print(f"[+] bundle.terminal  = {type(bundle.terminal).__name__}")
    print(f"[+] bundle.filesystem= {type(bundle.filesystem).__name__}")

    ui = AutoUI()
    orch = AgentOrchestrator(config=cfg, bundle=bundle, ui=ui)
    # sanity wiring ping — ensures CLI implements AgentUI structurally
    _ = CLI  # silence F401 without circular import

    outcomes: list[GoalOutcome] = []
    for goal in goals:
        print("\n" + "=" * 70)
        print(f"[>] Goal: {goal}")
        print("=" * 70)
        t0 = time.monotonic()
        try:
            result = orch.run_goal(goal)
            outcomes.append(_summarize(result, time.monotonic() - t0, goal))
        except Exception as exc:  # noqa: BLE001 — smoke driver, report all
            traceback.print_exc()
            outcomes.append(
                GoalOutcome(
                    goal=goal,
                    final_state="CRASHED",
                    ok_tasks=0,
                    failed_tasks=0,
                    skipped_tasks=0,
                    duration_s=time.monotonic() - t0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    print("\n" + "=" * 70)
    print(" Summary")
    print("=" * 70)
    completed = 0
    for o in outcomes:
        verdict = "PASS" if o.final_state == "COMPLETED" and o.failed_tasks == 0 else "FAIL"
        if verdict == "PASS":
            completed += 1
        tail = f"ok={o.ok_tasks} failed={o.failed_tasks} skipped={o.skipped_tasks}"
        err_suffix = f"  error={o.error}" if o.error else ""
        print(f"  [{verdict}] {o.final_state:<10} {tail:<30} {o.duration_s:6.1f}s  {o.goal}")
        if err_suffix:
            print(err_suffix)
    print(f"\n[=] {completed}/{len(outcomes)} goals reached COMPLETED")
    return 0 if completed == len(outcomes) else 1


if __name__ == "__main__":
    raw = sys.argv[1:] or list(DEFAULT_GOALS)
    sys.exit(main(raw))
