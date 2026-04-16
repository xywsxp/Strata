"""AgentOrchestrator — the single driver that realises the goal → plan → execute
→ recover → complete main loop.

The Orchestrator is the only component permitted to own the full strategy
stack (LLMRouter / planner functions / LinearRunner / RecoveryPipeline /
PersistenceManager / AuditLogger / ContextManager / VisionLocator /
TerminalHandler / GUILock). All other layers only observe or contribute a
sub-capability. Having a single owner keeps the assembly diagram explicit and
removes the "who constructs whom" ambiguity.

# CONVENTION: 单一 Orchestrator 持有整个策略栈 —— 不做 DI 容器化。
# 组件数 < 15，显式构造比框架更可读；规模扩张至 20+ 时再抽工厂。

# CONVENTION: AgentUI 定义于本模块而非 strata.interaction.cli ——
# 避免 harness 反向依赖 interaction；UI 属于 harness 主循环的控制流端口。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import icontract

from strata import StrataError
from strata.core.config import StrataConfig
from strata.core.errors import (
    GoalDecompositionError,
    OrchestrationError,
    PlanConfirmationAbortedError,
    PlannerError,
)
from strata.core.types import ActionResult, GlobalState, TaskGraph, TaskState
from strata.env.protocols import EnvironmentBundle
from strata.harness.actions import ACTION_VOCABULARY
from strata.harness.executor import PrimitiveTaskExecutor
from strata.harness.scheduler import LinearRunner, TaskExecutor
from strata.harness.state_machine import (
    GlobalEvent,
    StateMachine,
    create_global_state_machine,
)
from strata.llm.router import LLMRouter
from strata.planner.htn import decompose_goal


@runtime_checkable
class AgentUI(Protocol):
    """Narrow UI port the Orchestrator invokes for human interaction.

    The CLI class implements this Protocol structurally (it is not an explicit
    base). Keep the surface minimal so alternative front-ends (TUI / web UI)
    remain simple to adapt.
    """

    @property
    def interrupted(self) -> bool: ...

    def display_plan(self, graph: TaskGraph) -> None: ...

    def confirm_plan(self) -> bool: ...

    def display_progress(self, task_id: str, state: TaskState) -> None: ...

    def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]: ...

    def handle_destructive(self, description: str) -> bool: ...


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of a single :meth:`AgentOrchestrator.run_goal` invocation."""

    final_state: GlobalState
    task_states: Mapping[str, TaskState] = field(default_factory=dict)
    error: StrataError | None = None
    graph: TaskGraph | None = None


class AgentOrchestrator:
    """Owns the full strategy stack and drives the goal lifecycle."""

    @icontract.require(lambda config: config is not None)
    @icontract.require(lambda bundle: bundle is not None)
    @icontract.require(lambda ui: ui is not None)
    def __init__(
        self,
        config: StrataConfig,
        bundle: EnvironmentBundle,
        ui: AgentUI,
        llm_router: LLMRouter | None = None,
        executor: TaskExecutor | None = None,
    ) -> None:
        self._config = config
        self._bundle = bundle
        self._ui = ui
        # CONVENTION: llm_router 可选注入 —— 生产路径由 __main__ 构造一次；
        # 测试可传 mock 免去 OpenAI 客户端构造的网络依赖。
        self._llm_router = llm_router if llm_router is not None else LLMRouter(config)
        self._executor: TaskExecutor = (
            executor if executor is not None else PrimitiveTaskExecutor(bundle=bundle)
        )
        self._runner = LinearRunner(config)
        self._state_machine: StateMachine[GlobalState, GlobalEvent]
        self._last_graph: TaskGraph | None = None

    @icontract.require(
        lambda goal: len(goal.strip()) > 0,
        "goal must be non-empty",
    )
    @icontract.ensure(
        lambda result: result.final_state in ("COMPLETED", "FAILED"),
        "run_goal must end in a terminal state",
    )
    def run_goal(self, goal: str) -> ExecutionResult:
        """Drive a single goal from INIT through to COMPLETED / FAILED.

        The state machine enforces legal transitions; any transition violation
        surfaces as :class:`StateTransitionError` and is caught here to produce
        a FAILED result with the violation as the ``error`` field.
        """
        self._state_machine = create_global_state_machine()
        self._last_graph = None
        task_states: dict[str, TaskState] = {}

        try:
            self._fire("receive_goal")
            graph = self._plan(goal)
            self._last_graph = graph
            self._fire("plan_ready")

            if not self._confirm(graph):
                self._fire("user_abort")
                return ExecutionResult(
                    final_state="FAILED",
                    task_states={},
                    error=PlanConfirmationAbortedError("user rejected the plan"),
                    graph=graph,
                )

            self._fire("user_confirm")
            task_states = self._execute(graph)
            return ExecutionResult(
                final_state="COMPLETED",
                task_states=task_states,
                error=None,
                graph=graph,
            )
        except _OrchestratorAbort as abort:
            return ExecutionResult(
                final_state="FAILED",
                task_states=abort.task_states,
                error=abort.error,
                graph=self._last_graph,
            )
        except StrataError as exc:
            # Any unexpected strata error during the lifecycle ⇒ FAILED.
            return ExecutionResult(
                final_state="FAILED",
                task_states=task_states,
                error=exc,
                graph=self._last_graph,
            )

    # ── lifecycle steps ──

    def _plan(self, goal: str) -> TaskGraph:
        try:
            graph = decompose_goal(
                goal,
                self._llm_router,
                ACTION_VOCABULARY,
                context={},
            )
        except PlannerError as exc:
            self._fire("unrecoverable")
            raise _OrchestratorAbort(
                error=GoalDecompositionError(f"decompose_goal failed: {exc}"),
                task_states={},
            ) from exc

        self._assert_graph_actions_in_vocab(graph)
        return graph

    def _confirm(self, graph: TaskGraph) -> bool:
        self._ui.display_plan(graph)
        if self._ui.interrupted:
            return False
        return self._ui.confirm_plan()

    def _execute(self, graph: TaskGraph) -> dict[str, TaskState]:
        task_states: dict[str, TaskState] = {t.id: "PENDING" for t in graph.tasks}

        # Schedule -> Execute in one shot for Phase B. Phase C will insert
        # per-task transitions, recovery dispatch, and WAITING_USER.
        self._fire("task_dispatched")
        results = self._runner.run(graph, self._executor)
        for task_id, result in results.items():
            task_states[task_id] = _task_state_from_result(result)
            self._ui.display_progress(task_id, task_states[task_id])

        any_failed = any(state == "FAILED" for state in task_states.values())
        if any_failed:
            # Phase B: any single-task failure ⇒ goal FAILED.  Phase C will
            # route through RECOVERING before giving up.
            self._fire("task_failed")
            # RECOVERING -> unrecoverable (no recovery wired yet in Phase B)
            self._fire("unrecoverable")
            raise _OrchestratorAbort(
                error=OrchestrationError(
                    f"{sum(1 for s in task_states.values() if s == 'FAILED')} task(s) failed"
                ),
                task_states=task_states,
            )

        self._fire("task_done")
        self._fire("all_done")
        return task_states

    # ── state machine helpers ──

    def _fire(self, event: GlobalEvent) -> None:
        self._state_machine.transition(event)

    def _assert_graph_actions_in_vocab(self, graph: TaskGraph) -> None:
        vocab = set(ACTION_VOCABULARY)
        for task in graph.tasks:
            if task.task_type == "primitive" and task.action not in vocab:
                raise GoalDecompositionError(
                    f"planner produced unknown action {task.action!r} for task {task.id!r}"
                )

    @property
    def state(self) -> GlobalState:
        """Return the current state-machine state (``INIT`` before run_goal)."""
        if not hasattr(self, "_state_machine") or self._state_machine is None:
            return "INIT"
        return self._state_machine.state


# ── internal helpers ──


class _OrchestratorAbort(Exception):
    """Internal signal raised inside lifecycle steps to short-circuit run_goal.

    Not a :class:`StrataError` — this is purely an internal control-flow carrier
    that is caught inside :meth:`AgentOrchestrator.run_goal` and converted into
    a failed :class:`ExecutionResult`; it must never escape the orchestrator.
    """

    def __init__(
        self,
        error: StrataError,
        task_states: Mapping[str, TaskState] | None = None,
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.task_states: Mapping[str, TaskState] = task_states or {}


def _task_state_from_result(result: ActionResult) -> TaskState:
    return "SUCCEEDED" if result.success else "FAILED"


__all__ = [
    "AgentOrchestrator",
    "AgentUI",
    "ExecutionResult",
]
