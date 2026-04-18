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

import contextlib
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import icontract

from strata import StrataError
from strata.core.config import StrataConfig
from strata.core.errors import (
    GoalDecompositionError,
    OrchestrationError,
    PersistenceSchemaVersionError,
    PlanConfirmationAbortedError,
    PlannerError,
    SerializationError,
)
from strata.core.paths import RunDirLayout, gc_old_runs
from strata.core.types import ActionResult, GlobalState, TaskGraph, TaskNode, TaskState
from strata.env.protocols import EnvironmentBundle
from strata.grounding.terminal_handler import TerminalHandler
from strata.grounding.validator import ActionValidator
from strata.grounding.vision_locator import VisionLocator
from strata.harness.actions import ACTION_VOCABULARY, format_action_catalog_for_llm
from strata.harness.context import AuditLogger, ContextManager
from strata.harness.executor import PrimitiveTaskExecutor
from strata.harness.graph_tracker import GraphTracker, NullGraphTracker
from strata.harness.gui_lock import GUILock
from strata.harness.persistence import Checkpoint, PersistenceManager
from strata.harness.recovery import RecoveryAction, RecoveryLevel, RecoveryPipeline
from strata.harness.scheduler import LinearRunner, TaskExecutor
from strata.harness.state_machine import (
    GlobalEvent,
    StateMachine,
    create_global_state_machine,
)
from strata.llm.router import LLMRouter
from strata.observability.recorder import (
    NullRecorder,
    TrajectoryRecorder,
)
from strata.observability.transcript import ChatTranscriptSink
from strata.planner.adjuster import Adjustment, adjust_plan, apply_adjustment
from strata.planner.htn import decompose_goal


# CONVENTION: strata.harness.orchestrator — AgentUI Protocol 定义在 harness 而非
# interaction 层，避免 harness → interaction 的反向依赖；UI 是控制流的一部分，
# 其形状由 orchestrator 决定，由前端（CLI / 未来的 TUI）结构化实现。
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

    def confirm_resume(self, saved_goal: str, task_count: int) -> bool: ...

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


# CONVENTION: strata.harness.orchestrator — Orchestrator 显式持有整个策略栈
# (planner/runner/recovery/persistence/context/audit/grounding)，故意不引入 DI
# 容器：组件数量 <15，显式构造比框架层反射更可读、对 mypy 更友好。若未来超过
# 20 个组件再考虑工厂模式。
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
        layout: RunDirLayout | None = None,
        transcript_sink: ChatTranscriptSink | None = None,
        recorder: TrajectoryRecorder | None = None,
    ) -> None:
        self._config = config
        self._bundle = bundle
        self._ui = ui
        self._layout = layout
        self._transcript_sink = transcript_sink
        self._recorder = recorder

        # CONVENTION: debug 组件条件构造——enabled=false 时不 import aiohttp，
        # _debug_controller=None 使 _fire/await_step 短路为零开销。
        # 构造顺序：debug controller → LLMRouter（注入 interceptor）→ 其余。
        if config.debug.enabled:
            from strata.debug.controller import DebugController
            from strata.debug.server import DebugServer

            self._debug_controller: DebugController | None = DebugController(
                config.debug,
                interrupt_check=lambda: self._ui.interrupted,
            )
        else:
            self._debug_controller = None

        # CONVENTION: llm_router 可选注入 —— 生产路径由 __main__ 构造一次；
        # 测试可传 mock 免去 OpenAI 客户端构造的网络依赖。
        self._llm_router = (
            llm_router
            if llm_router is not None
            else LLMRouter(
                config,
                sink=transcript_sink,
                prompt_interceptor=self._debug_controller,
            )
        )
        self._context = ContextManager(config.memory)
        state_dir = _default_state_dir()
        self._audit_logger = AuditLogger(config.audit_log)
        self._persistence = PersistenceManager(
            state_dir,
            max_checkpoint_history=config.debug.max_checkpoint_history,
        )
        # CONVENTION: 生产路径下所有 grounding 组件由 Orchestrator 构造并注入
        # PrimitiveTaskExecutor；测试可直接传 executor 绕过构造链。
        if executor is None:
            self._gui_lock = GUILock(config.gui)
            self._action_validator = ActionValidator(bundle.gui)
            self._vision_locator = VisionLocator(bundle.gui, self._llm_router, config.gui)
            self._terminal_handler = TerminalHandler(bundle.terminal, config.terminal)
            self._executor: TaskExecutor = PrimitiveTaskExecutor(
                bundle=bundle,
                vision_locator=self._vision_locator,
                terminal_handler=self._terminal_handler,
                gui_lock=self._gui_lock,
                action_validator=self._action_validator,
                audit_logger=self._audit_logger,
            )
        else:
            self._executor = executor
        self._runner = LinearRunner(config)
        self._recovery = RecoveryPipeline(config, self._adjuster)
        self._state_machine: StateMachine[GlobalState, GlobalEvent]
        self._last_graph: TaskGraph | None = None
        self._graph_tracker: GraphTracker = NullGraphTracker()
        self._attempt_counts: dict[str, int] = {}
        self._task_states: dict[str, TaskState] = {}
        self._current_goal: str = ""

        if config.debug.enabled:
            from strata.debug.server import DebugServer

            self._debug_server: DebugServer | None = DebugServer(
                controller=self._debug_controller,  # type: ignore[arg-type]
                config=config.debug,
                gui=bundle.gui,
                graph_fn=lambda: self._last_graph,
                task_states_fn=lambda: dict(self._task_states),
            )
            self._debug_server.start()
        else:
            self._debug_server = None

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

        Startup checkpoint recovery (Q5 scheme a): if a valid checkpoint is
        present the user is asked whether to resume; on yes the goal/task list
        is restored and execution skips PLANNING/CONFIRMING; on no the
        checkpoint is cleared and a fresh run proceeds.
        """
        self._state_machine = create_global_state_machine()
        self._last_graph = None
        self._attempt_counts = {}
        self._task_states = {}
        self._current_goal = goal
        self._context.clear()
        task_states: dict[str, TaskState] = {}

        run_layout = self._prepare_run_layout(goal)
        self._active_recorder = self._build_recorder(run_layout)
        started_at = time.time()

        run_id = run_layout.run_dir.name if run_layout else "unknown"
        with contextlib.suppress(Exception):
            self._active_recorder.start(run_id)

        try:
            resumed = self._try_resume()
            if resumed is None:
                self._fire("receive_goal")
                graph = self._plan(goal)
                self._last_graph = graph
                self._graph_tracker.update(graph, "initial_plan")
                self._task_states = {t.id: "PENDING" for t in graph.tasks}
                self._save_checkpoint()
                self._fire("plan_ready")

                if not self._confirm(graph):
                    self._fire("user_abort")
                    self._persistence.clear_checkpoint()
                    return ExecutionResult(
                        final_state="FAILED",
                        task_states={},
                        error=PlanConfirmationAbortedError("user rejected the plan"),
                        graph=graph,
                    )

                self._fire("user_confirm")
            else:
                graph = resumed

            task_states = self._execute(graph)
            self._persistence.clear_checkpoint()
            final: GlobalState = "COMPLETED"
            result = ExecutionResult(
                final_state=final,
                task_states=task_states,
                error=None,
                graph=graph,
            )
        except _OrchestratorAbort as abort:
            final = "FAILED"
            result = ExecutionResult(
                final_state=final,
                task_states=abort.task_states,
                error=abort.error,
                graph=self._last_graph,
            )
        except StrataError as exc:
            final = "FAILED"
            result = ExecutionResult(
                final_state=final,
                task_states=task_states,
                error=exc,
                graph=self._last_graph,
            )

        with contextlib.suppress(Exception):
            self._active_recorder.note_event("run_end", {"final_state": final})
            self._active_recorder.stop()
        self._finalize_run(run_layout, goal, started_at, final)
        return result

    # ── run layout helpers ──

    def _prepare_run_layout(self, goal: str) -> RunDirLayout | None:
        """Create (or reuse injected) RunDirLayout for this run.

        Returns ``None`` if layout creation fails (the run continues without
        artefact directories — observability degrades, execution does not).
        """
        if self._layout is not None:
            with contextlib.suppress(OSError):
                self._layout.ensure_dirs()
                self._layout.link_current()
            return self._layout
        try:
            layout = RunDirLayout.create(self._config.paths, goal)
            layout.ensure_dirs()
            layout.link_current()
            return layout
        except Exception:
            return None

    def _build_recorder(self, layout: RunDirLayout | None) -> TrajectoryRecorder:
        """Return the active recorder for this run.

        Priority: injected > auto-constructed (if OSWorld enabled) > NullRecorder.
        Recorder construction is delegated to callers via __init__ injection;
        orchestrator no longer imports OSWorldFFmpegRecorder directly.
        """
        if self._recorder is not None:
            return self._recorder
        if layout is not None and self._config.osworld.enabled:
            try:
                from strata.env.osworld_client import OSWorldHTTPClient
                from strata.observability.recorder import OSWorldFFmpegRecorder

                runner = OSWorldHTTPClient(
                    base_url=self._config.osworld.server_url,
                    timeout=self._config.osworld.request_timeout,
                )
                return OSWorldFFmpegRecorder(
                    runner=runner,
                    screen_size=self._config.osworld.screen_size,
                    out_dir=layout.recordings_dir,
                    fps=30,
                )
            except Exception:
                pass
        return NullRecorder()

    def _finalize_run(
        self,
        layout: RunDirLayout | None,
        goal: str,
        started_at: float,
        final_state: str,
    ) -> None:
        """Best-effort manifest write + GC after run_goal completes."""
        if layout is None:
            return
        with contextlib.suppress(Exception):
            layout.write_manifest(goal, {"final_state": final_state}, started_at)
        if final_state == "COMPLETED":
            with contextlib.suppress(Exception):
                gc_old_runs(layout.run_root, self._config.paths.keep_last_runs)

    # ── lifecycle steps ──

    def _resolve_os_type(self) -> str:
        """Return OS type for plan context.

        OSWorld enabled → config.osworld.os_type; else → platform.system().
        """
        if self._config.osworld.enabled:
            return self._config.osworld.os_type
        import platform

        return platform.system() or "Linux"

    def _plan(self, goal: str) -> TaskGraph:
        # CONVENTION: 把环境约束（OS / sandbox root / 只读路径）塞进 context，
        # 让 LLM 避免规划出会被 SandboxGuard 拒绝的路径。
        plan_context: dict[str, object] = {
            "os_type": self._resolve_os_type(),
            "sandbox_enabled": self._config.sandbox.enabled,
            "sandbox_root": self._config.sandbox.root,
            "read_only_paths": list(self._config.sandbox.read_only_paths),
        }
        try:
            graph = decompose_goal(
                goal,
                self._llm_router,
                ACTION_VOCABULARY,
                context=plan_context,
                action_catalog=format_action_catalog_for_llm(),
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
        """Walk the task list with full per-task recovery. Entered in SCHEDULING.

        The task list is kept as a local mutable ``list[TaskNode]`` so that a
        REPLAN recovery can splice replacement nodes in place. The persistent
        ``self._last_graph`` is rebuilt whenever a REPLAN mutates the sequence
        so that any later adjuster call has a consistent graph to look at.

        # CONVENTION: attempt_count 按 task.id 独立累加；REPLAN 产出的新 id
        # 从 0 起步（RecoveryPipeline 单调升级前提）。
        """
        tasks: list[TaskNode] = list(graph.tasks)
        # Prefer pre-populated states from resume; otherwise initialise PENDING.
        task_states: dict[str, TaskState] = (
            dict(self._task_states) if self._task_states else {t.id: "PENDING" for t in tasks}
        )
        for t in tasks:
            task_states.setdefault(t.id, "PENDING")
        context: dict[str, object] = {}

        idx = 0
        while idx < len(tasks):
            if self._ui.interrupted:
                self._transition_to_failed_via_waiting()
                raise _OrchestratorAbort(
                    error=OrchestrationError("interrupted by user"),
                    task_states=task_states,
                )

            task = tasks[idx]
            self._fire("task_dispatched")
            task_states[task.id] = "RUNNING"
            self._ui.display_progress(task.id, "RUNNING")
            if self._debug_controller is not None:
                self._debug_controller.await_step(task.id)

            with contextlib.suppress(Exception):
                self._active_recorder.note_keyframe(f"{task.id}_before")
                self._active_recorder.note_event(
                    "task_start", {"task_id": task.id, "action": task.action or task.task_type}
                )

            result = self._run_single(task, context)

            with contextlib.suppress(Exception):
                self._active_recorder.note_keyframe(f"{task.id}_after")

            if result.success:
                task_states[task.id] = "SUCCEEDED"
                self._task_states = dict(task_states)
                self._ui.display_progress(task.id, "SUCCEEDED")
                if task.output_var and result.data:
                    context[task.output_var] = result.data
                with contextlib.suppress(Exception):
                    self._active_recorder.note_event(
                        "task_done",
                        {"task_id": task.id, "success": True},
                    )
                self._context.add_entry(
                    {
                        "task_id": task.id,
                        "action": task.action or task.task_type,
                        "success": True,
                    }
                )
                self._fire("task_done")
                self._save_checkpoint()
                idx += 1
                continue

            # Failure path: SCHEDULING ← EXECUTING via task_failed.
            self._fire("task_failed")
            self._attempt_counts[task.id] = self._attempt_counts.get(task.id, 0) + 1
            error_exc = _result_to_exception(result)
            self._context.add_entry(
                {
                    "task_id": task.id,
                    "action": task.action or task.task_type,
                    "success": False,
                    "error": str(error_exc),
                }
            )
            recovery = self._recovery.attempt_recovery(
                task, error_exc, self._attempt_counts[task.id]
            )

            decision = self._apply_recovery(
                recovery=recovery,
                task=task,
                task_states=task_states,
                error_exc=error_exc,
                tasks=tasks,
                idx=idx,
            )

            if decision.outcome == "retry":
                self._task_states = dict(task_states)
                self._save_checkpoint()
                continue
            if decision.outcome == "advance":
                idx += 1
                continue
            if decision.outcome == "replan":
                if decision.new_idx is not None:
                    idx = decision.new_idx
                self._task_states = dict(task_states)
                self._save_checkpoint()
                continue
            if decision.outcome == "skip":
                task_states[task.id] = "SKIPPED"
                self._task_states = dict(task_states)
                self._ui.display_progress(task.id, "SKIPPED")
                self._save_checkpoint()
                idx += 1
                continue
            # decision.outcome == "abort"
            assert decision.error is not None  # invariant: abort carries an error
            raise _OrchestratorAbort(error=decision.error, task_states=task_states)

        # All tasks consumed: SCHEDULING → all_done → COMPLETED.
        self._fire("all_done")
        return task_states

    def _run_single(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        """Execute *task*. Control-flow nodes are delegated to LinearRunner."""
        if task.task_type in ("repeat", "if_then", "for_each"):
            # Reuse LinearRunner's interpreter for loop/branch nodes.
            return self._runner.execute_single(task, self._executor, dict(context))
        return self._executor.execute(task, context)

    def _apply_recovery(
        self,
        *,
        recovery: RecoveryAction,
        task: TaskNode,
        task_states: dict[str, TaskState],
        error_exc: Exception,
        tasks: list[TaskNode],
        idx: int,
    ) -> _RecoveryDecision:
        """Translate a :class:`RecoveryAction` into a loop control outcome.

        The state machine transitions are a by-product of the decision.
        """
        level = recovery.level
        if level is RecoveryLevel.RETRY or level is RecoveryLevel.ALTERNATIVE:
            self._fire("recovered")
            task_states[task.id] = "PENDING"
            return _RecoveryDecision(outcome="retry")

        if level is RecoveryLevel.REPLAN:
            if not recovery.replacement_tasks:
                # RecoveryPipeline promised REPLAN but produced no node —
                # collapse to SKIP defensively.
                self._fire("recovered")
                return _RecoveryDecision(outcome="skip")
            new_idx = self._splice_replan(
                tasks=tasks,
                idx=idx,
                task=task,
                task_states=task_states,
                replacement_tasks=recovery.replacement_tasks,
            )
            self._fire("recovered")
            return _RecoveryDecision(outcome="replan", new_idx=new_idx)

        if level is RecoveryLevel.SKIP:
            self._fire("recovered")
            return _RecoveryDecision(outcome="skip")

        # USER_INTERVENTION → WAITING_USER → handle_error.
        self._fire("escalated")
        choice = self._ui.handle_error(task.id, error_exc)
        if choice == "retry":
            self._fire("user_decision")
            task_states[task.id] = "PENDING"
            return _RecoveryDecision(outcome="retry")
        if choice == "skip":
            self._fire("user_decision")
            return _RecoveryDecision(outcome="skip")
        # choice == "abort"
        self._fire("user_abort")
        return _RecoveryDecision(
            outcome="abort",
            error=OrchestrationError(f"user aborted at task {task.id!r}: {error_exc}"),
        )

    def _splice_replan(
        self,
        *,
        tasks: list[TaskNode],
        idx: int,
        task: TaskNode,
        task_states: dict[str, TaskState],
        replacement_tasks: Sequence[TaskNode],
    ) -> int:
        """Apply a REPLAN adjustment to *tasks* in place and return the new idx.

        Uses the full ``replacement_tasks`` from :class:`RecoveryAction` to
        build an :class:`Adjustment`; falls back to direct node substitution
        if :func:`apply_adjustment` fails.

        Returns the index of the first PENDING task (or ``len(tasks)`` if all
        are completed).
        """
        assert self._last_graph is not None
        adjustment = Adjustment(
            original_task_id=task.id,
            replacement_tasks=tuple(replacement_tasks),
            strategy="replace",
        )
        try:
            new_graph = apply_adjustment(self._last_graph, adjustment)
        except PlannerError:
            return idx

        self._last_graph = new_graph
        self._graph_tracker.update(new_graph, f"replan_{task.id}")
        tasks[:] = list(new_graph.tasks)
        del task_states[task.id]
        for node in new_graph.tasks:
            task_states.setdefault(node.id, "PENDING")

        for i, t in enumerate(tasks):
            if task_states.get(t.id) == "PENDING":
                return i
        return len(tasks)

    def _adjuster(self, failed_task: TaskNode, error: Exception) -> list[TaskNode]:
        """Adjuster closure handed to :class:`RecoveryPipeline`.

        Contract: may return ``[]`` to signal "no adjustment possible" (pipeline
        will escalate to SKIP). Any exception from the underlying LLM call is
        caught here — the recovery pipeline must remain robust in the face of
        a misbehaving planner adapter.

        The ``failure_context`` passed to :func:`adjust_plan` is sourced from
        the sliding :class:`ContextManager` window and fact slot so the LLM
        sees the last few actions and recorded facts rather than only the
        immediate error string (E.4).

        # CONVENTION: 宽口径 except Exception — LLM 适配器可能抛出非 PlannerError
        # 的底层异常（网络/解析/Mock 配置错误）。Adjuster 失败即退化为 SKIP。
        """
        if self._last_graph is None:
            return []
        failure_context: Mapping[str, object] = {
            "error_type": type(error).__name__,
            "error_msg": str(error),
            "recent_actions": list(self._context.get_window()),
            "facts": [{"key": f.key, "value": f.value} for f in self._context.get_facts()],
        }
        try:
            adjustment = adjust_plan(
                self._last_graph,
                failed_task.id,
                failure_context,
                self._llm_router,
                action_catalog=format_action_catalog_for_llm(),
            )
        except Exception:
            return []
        return list(adjustment.replacement_tasks)

    # ── persistence / resume ──

    # CONVENTION: strata.harness.orchestrator — checkpoint 在"任务边界"写入，而非
    # "动作边界"。理由：任务粒度足以支持崩溃恢复（重新执行一整个任务比丢掉整条
    # 目标代价小得多），动作粒度写入会放大磁盘 I/O 至每秒数次。写入使用
    # PersistenceManager.atomic_write，天然防止半写损坏。
    def _save_checkpoint(self) -> None:
        """Persist current lifecycle snapshot. Never raises — persistence
        failures (disk full, permissions) are not fatal for the run."""
        if self._last_graph is None:
            return
        try:
            cp = Checkpoint(
                global_state=self._state_machine.state,
                task_states=dict(self._task_states),
                context={"goal": self._current_goal},
                task_graph=self._last_graph,
                timestamp=time.time(),
            )
            self._persistence.save_checkpoint(cp)
        except (OSError, SerializationError):
            # CONVENTION: checkpoint 保存失败不中断主循环，只影响 resume 能力。
            return

    def _try_resume(self) -> TaskGraph | None:
        """Ask the user whether to resume a pre-existing checkpoint.

        Returns the restored :class:`TaskGraph` when the user confirms resume;
        otherwise clears the checkpoint and returns ``None``. A corrupt or
        schema-mismatched checkpoint is silently cleared (never offered).
        """
        try:
            cp = self._persistence.load_checkpoint()
        except (PersistenceSchemaVersionError, SerializationError, OSError):
            # Stale / corrupt / schema-mismatched checkpoint → discard.
            with contextlib.suppress(OSError):
                self._persistence.clear_checkpoint()
            return None

        if cp is None:
            return None

        saved_goal = str(cp.context.get("goal", "<unknown>"))
        task_count = len(cp.task_graph.tasks)

        if not self._ui.confirm_resume(saved_goal, task_count):
            with contextlib.suppress(OSError):
                self._persistence.clear_checkpoint()
            return None

        # Restore the saved state and jump straight into SCHEDULING.
        self._fire("receive_goal")
        self._last_graph = cp.task_graph
        self._graph_tracker.update(cp.task_graph, "resumed_from_checkpoint")
        self._task_states = dict(cp.task_states)
        self._current_goal = saved_goal
        self._fire("plan_ready")
        self._fire("user_confirm")
        return cp.task_graph

    def _transition_to_failed_via_waiting(self) -> None:
        """If the state machine is in EXECUTING, take a conservative path
        ``EXECUTING → task_failed → RECOVERING → unrecoverable`` to reach
        ``FAILED`` respecting the declared legal transitions. Used when the
        user interrupts mid-loop.
        """
        state = self._state_machine.state
        if state == "SCHEDULING":
            # Inject a no-op EXECUTING round trip so ``task_failed`` is legal.
            self._fire("task_dispatched")
        self._fire("task_failed")
        self._fire("unrecoverable")

    # ── state machine helpers ──

    def _fire(self, event: GlobalEvent) -> None:
        new_state = self._state_machine.transition(event)
        if self._debug_controller is not None:
            self._debug_controller.notify(event, new_state, dict(self._task_states))

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


@dataclass(frozen=True)
class _RecoveryDecision:
    """Loop control outcome produced by :meth:`AgentOrchestrator._apply_recovery`.

    - ``retry``:   rerun the current task (attempt_count already bumped)
    - ``advance``: move to the next task (legacy — currently unused path)
    - ``replan``:  graph was mutated; restart at the same index
    - ``skip``:    mark current task SKIPPED and advance
    - ``abort``:   terminate the run with ``error``
    """

    outcome: Literal["retry", "advance", "replan", "skip", "abort"]
    error: StrataError | None = None
    new_idx: int | None = None


def _default_state_dir() -> str:
    """Default checkpoint directory (~/.strata/state) with env override.

    # CONVENTION: STRATA_STATE_DIR 环境变量可覆盖默认路径，便于测试隔离；
    # 生产环境下固定 ~/.strata/state。
    """
    env = os.environ.get("STRATA_STATE_DIR")
    if env:
        return env
    return str(Path.home() / ".strata" / "state")


def _result_to_exception(result: ActionResult) -> Exception:
    """Synthesise an Exception instance for downstream adjuster / handle_error
    based on an ``ActionResult.error`` message. Using a plain ``Exception``
    here (not a :class:`StrataError`) because the underlying adapter already
    converted any domain error to a message in the executor layer.
    """
    msg = result.error or "action failed"
    return Exception(msg)


__all__ = [
    "AgentOrchestrator",
    "AgentUI",
    "ExecutionResult",
]
