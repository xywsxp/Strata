"""Tests for :class:`strata.harness.orchestrator.AgentOrchestrator`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings

from strata.core.config import StrataConfig, get_default_config
from strata.core.errors import (
    GoalDecompositionError,
    PlannerError,
)
from strata.core.types import ActionResult, TaskGraph, TaskNode, TaskState
from strata.env.protocols import EnvironmentBundle
from strata.harness.orchestrator import (
    AgentOrchestrator,
    AgentUI,
)
from strata.harness.scheduler import TaskExecutor

from ..strategies import _DeterministicExecutor, st_failing_sequence
from .test_executor import MockAppManager, MockFileSystem, MockGUI, MockSystem, MockTerminal


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every Orchestrator test into a fresh ``STRATA_STATE_DIR``.

    Without this, Phase E's checkpoint persistence would pollute the real
    ``~/.strata/state`` and cause cross-test interference.
    """
    monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))


# ── Test helpers ──


@dataclass
class RecordingUI:
    """Minimal AgentUI implementation that records calls."""

    confirm: bool = True
    resume_yes: bool = False
    interrupted_flag: bool = False
    error_decision: Literal["retry", "skip", "abort"] = "abort"
    destructive_allow: bool = True
    calls: list[tuple[str, Any]] = field(default_factory=list)

    @property
    def interrupted(self) -> bool:
        return self.interrupted_flag

    def display_plan(self, graph: TaskGraph) -> None:
        self.calls.append(("display_plan", graph))

    def confirm_plan(self) -> bool:
        self.calls.append(("confirm_plan", None))
        return self.confirm

    def confirm_resume(self, saved_goal: str, task_count: int) -> bool:
        self.calls.append(("confirm_resume", (saved_goal, task_count)))
        return self.resume_yes

    def display_progress(self, task_id: str, state: TaskState) -> None:
        self.calls.append(("display_progress", (task_id, state)))

    def handle_error(self, task_id: str, error: Exception) -> Literal["retry", "skip", "abort"]:
        self.calls.append(("handle_error", (task_id, error)))
        return self.error_decision

    def handle_destructive(self, description: str) -> bool:
        self.calls.append(("handle_destructive", description))
        return self.destructive_allow


def _make_bundle() -> EnvironmentBundle:
    return EnvironmentBundle(
        gui=MockGUI(),
        terminal=MockTerminal(),
        filesystem=MockFileSystem(),
        app_manager=MockAppManager(),
        system=MockSystem(),
    )


def _make_router_mock(
    graph: TaskGraph | None = None,
    raise_exc: Exception | None = None,
) -> MagicMock:
    """Return a MagicMock posing as LLMRouter; not used when executor is supplied."""
    router = MagicMock()
    router.plan = MagicMock()
    return router


def _make_orchestrator(
    *,
    config: StrataConfig | None = None,
    bundle: EnvironmentBundle | None = None,
    ui: AgentUI | None = None,
    executor: TaskExecutor | None = None,
    router: MagicMock | None = None,
) -> AgentOrchestrator:
    return AgentOrchestrator(
        config=config or get_default_config(),
        bundle=bundle or _make_bundle(),
        ui=ui or RecordingUI(),
        llm_router=router or _make_router_mock(),
        executor=executor,
    )


class _FixedGraphExecutor:
    """Executor that always succeeds (used with real decompose_goal monkeypatch)."""

    def __init__(self) -> None:
        self.calls: list[TaskNode] = []

    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        self.calls.append(task)
        return ActionResult(success=True, data={"task_id": task.id})


class _FailingExecutor:
    def __init__(self, n_failures: int = 1) -> None:
        self._n = n_failures
        self._count = 0

    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        self._count += 1
        if self._count <= self._n:
            return ActionResult(success=False, error="simulated fail")
        return ActionResult(success=True)


def _stub_decompose_goal(monkeypatch: pytest.MonkeyPatch, graph: TaskGraph) -> None:
    """Replace strata.harness.orchestrator.decompose_goal with a stub."""

    def _stub(
        goal: str,
        router: object,
        available_actions: Sequence[str],
        context: Mapping[str, object] | None = None,
        action_catalog: str | None = None,
    ) -> TaskGraph:
        return graph if graph.goal == goal else TaskGraph(goal=goal, tasks=graph.tasks)

    monkeypatch.setattr(
        "strata.harness.orchestrator.decompose_goal",
        _stub,
    )


def _stub_decompose_raise(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def _stub(
        goal: str,
        router: object,
        available_actions: Sequence[str],
        context: Mapping[str, object] | None = None,
        action_catalog: str | None = None,
    ) -> TaskGraph:
        raise exc

    monkeypatch.setattr(
        "strata.harness.orchestrator.decompose_goal",
        _stub,
    )


def _single_task_graph(goal: str = "list files") -> TaskGraph:
    return TaskGraph(
        goal=goal,
        tasks=(
            TaskNode(
                id="t1",
                task_type="primitive",
                action="list_directory",
                params={"path": "/tmp"},
            ),
        ),
    )


# ── Contract tests ──


class TestInitContracts:
    def test_init_requires_config(self) -> None:
        import icontract

        with pytest.raises(icontract.ViolationError):
            AgentOrchestrator(
                config=None,  # type: ignore[arg-type]
                bundle=_make_bundle(),
                ui=RecordingUI(),
            )


class TestRunGoalContracts:
    def test_empty_goal_rejected(self) -> None:
        import icontract

        orch = _make_orchestrator()
        with pytest.raises(icontract.ViolationError):
            orch.run_goal("")

    def test_whitespace_only_goal_rejected(self) -> None:
        import icontract

        orch = _make_orchestrator()
        with pytest.raises(icontract.ViolationError):
            orch.run_goal("   ")


# ── Happy-path tests ──


class TestHappyPath:
    def test_single_task_reaches_completed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        exec_ = _FixedGraphExecutor()
        ui = RecordingUI(confirm=True)
        orch = _make_orchestrator(ui=ui, executor=exec_)

        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert result.task_states["t1"] == "SUCCEEDED"
        assert result.error is None
        assert exec_.calls[0].id == "t1"

    def test_user_abort_reaches_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=False)
        orch = _make_orchestrator(ui=ui, executor=_FixedGraphExecutor())

        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
        assert result.error is not None
        assert "reject" in str(result.error).lower() or "aborted" in str(result.error).lower()

    def test_decompose_failure_reaches_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_decompose_raise(monkeypatch, PlannerError("LLM exploded"))

        orch = _make_orchestrator()
        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
        assert isinstance(result.error, GoalDecompositionError)


class TestUnknownActionRejected:
    def test_planner_unknown_action_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = TaskGraph(
            goal="do bad thing",
            tasks=(TaskNode(id="x", task_type="primitive", action="__mystery__", params={}),),
        )
        _stub_decompose_goal(monkeypatch, bad)

        orch = _make_orchestrator()
        result = orch.run_goal("do bad thing")

        assert result.final_state == "FAILED"
        assert isinstance(result.error, GoalDecompositionError)
        assert "__mystery__" in str(result.error)


# ── Single-task failure → FAILED (Phase B; Phase C adds recovery) ──


class TestTaskFailure:
    def test_perennial_failure_skips_to_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With recovery enabled, a task that always fails is eventually SKIPPED
        and the goal reaches COMPLETED. Phase C behaviour."""
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        orch = _make_orchestrator(executor=_FailingExecutor(n_failures=99))
        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert result.task_states["t1"] == "SKIPPED"

    def test_retry_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A task that fails once then succeeds is retried and reaches COMPLETED."""
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        orch = _make_orchestrator(executor=_FailingExecutor(n_failures=1))
        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert result.task_states["t1"] == "SUCCEEDED"

    def test_user_abort_on_escalation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When recovery escalates to USER_INTERVENTION and the UI says
        ``abort``, the goal reaches FAILED."""
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True, error_decision="abort")
        # attempt_count needs to exceed 4 for USER_INTERVENTION
        orch = _make_orchestrator(ui=ui, executor=_FailingExecutor(n_failures=99))
        # Inject many retry failures: easiest is to spy on recovery counter
        # indirectly: bump attempt_counts before run_goal.  We do this by
        # patching RecoveryPipeline to always return USER_INTERVENTION.
        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        monkeypatch.setattr(
            orch._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.USER_INTERVENTION,
                description="test",
            ),
        )
        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
        assert any(c[0] == "handle_error" for c in ui.calls)

    def test_user_skip_on_escalation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True, error_decision="skip")
        orch = _make_orchestrator(ui=ui, executor=_FailingExecutor(n_failures=99))
        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        monkeypatch.setattr(
            orch._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.USER_INTERVENTION,
                description="test",
            ),
        )
        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert result.task_states["t1"] == "SKIPPED"

    def test_attempt_count_escalates_monotonically(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each repeated failure of the same task must bump attempt_count by
        exactly 1 before the recovery pipeline is consulted."""
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        seen_counts: list[int] = []
        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        orch = _make_orchestrator(executor=_FailingExecutor(n_failures=99))

        def _spy(task: TaskNode, error: Exception, count: int) -> RecoveryAction:
            seen_counts.append(count)
            # Force SKIP on the 4th attempt so the test terminates.
            if count >= 4:
                return RecoveryAction(level=RecoveryLevel.SKIP, description="done")
            return RecoveryAction(level=RecoveryLevel.RETRY, description="retry")

        monkeypatch.setattr(orch._recovery, "attempt_recovery", _spy)
        orch.run_goal("list files")

        # Counts must be strictly increasing starting from 1.
        assert seen_counts == [1, 2, 3, 4]

    def test_user_retry_then_succeed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True, error_decision="retry")
        orch = _make_orchestrator(ui=ui, executor=_FailingExecutor(n_failures=1))
        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        monkeypatch.setattr(
            orch._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.USER_INTERVENTION,
                description="test",
            ),
        )
        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert result.task_states["t1"] == "SUCCEEDED"


# ── Property: with mock executor, run_goal always terminates in COMPLETED/FAILED ──


@given(st_failing_sequence(max_length=5))
@settings(max_examples=20, deadline=None)
def test_run_goal_always_terminates(
    pattern: Sequence[bool],
) -> None:
    """For any bool pattern, run_goal must end in COMPLETED or FAILED."""
    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    try:
        graph = _single_task_graph()

        def _stub(
            goal: str,
            router: object,
            available_actions: Sequence[str],
            context: Mapping[str, object] | None = None,
            action_catalog: str | None = None,
        ) -> TaskGraph:
            return graph

        monkeypatch.setattr("strata.harness.orchestrator.decompose_goal", _stub)

        executor = _DeterministicExecutor(pattern)
        orch = _make_orchestrator(executor=executor)
        result = orch.run_goal("list files")

        assert result.final_state in ("COMPLETED", "FAILED")
    finally:
        monkeypatch.undo()


# ── UI wiring ──


# ── Phase D: grounding components constructed and injected ──


class TestGroundingWiring:
    def test_default_executor_has_grounding_components(self) -> None:
        """Constructing without an executor injects VisionLocator,
        TerminalHandler, GUILock, ActionValidator into PrimitiveTaskExecutor."""
        from strata.grounding.terminal_handler import TerminalHandler
        from strata.grounding.validator import ActionValidator
        from strata.grounding.vision_locator import VisionLocator
        from strata.harness.executor import PrimitiveTaskExecutor
        from strata.harness.gui_lock import GUILock

        orch = _make_orchestrator()

        assert isinstance(orch._executor, PrimitiveTaskExecutor)
        assert isinstance(orch._vision_locator, VisionLocator)
        assert isinstance(orch._terminal_handler, TerminalHandler)
        assert isinstance(orch._gui_lock, GUILock)
        assert isinstance(orch._action_validator, ActionValidator)


class TestVisionLocatorCacheRemoved:
    def test_next_page_cache_attribute_does_not_exist(self) -> None:
        """Phase D.4: dead _next_page_cache field removed."""
        from strata.grounding.vision_locator import VisionLocator

        orch = _make_orchestrator()
        assert not hasattr(orch._vision_locator, "_next_page_cache")
        # Sanity: constructor arguments unchanged.
        assert VisionLocator.__init__.__code__.co_varnames[:4] == (
            "self",
            "gui",
            "router",
            "config",
        )


# ── Phase E: persistence + audit + context ──


class TestCheckpointPersistence:
    def test_checkpoint_cleared_on_completed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)
        orch = _make_orchestrator(executor=_FixedGraphExecutor())

        result = orch.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert not (tmp_path / "checkpoint.json").exists()

    def test_checkpoint_preserved_on_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """On user-abort (FAILED) the checkpoint is retained for resume.
        Q5 scheme a."""
        monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)
        ui = RecordingUI(confirm=True, error_decision="abort")

        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        orch = _make_orchestrator(ui=ui, executor=_FailingExecutor(n_failures=99))
        monkeypatch.setattr(
            orch._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.USER_INTERVENTION,
                description="test",
            ),
        )
        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
        # Checkpoint left behind for the next run.
        assert (tmp_path / "checkpoint.json").exists()

    def test_checkpoint_written_incrementally(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Each successful task bump triggers a save_checkpoint call."""
        monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))
        two_task = TaskGraph(
            goal="two tasks",
            tasks=(
                TaskNode(
                    id="a",
                    task_type="primitive",
                    action="list_directory",
                    params={"path": "/"},
                ),
                TaskNode(
                    id="b",
                    task_type="primitive",
                    action="list_directory",
                    params={"path": "/"},
                ),
            ),
        )
        _stub_decompose_goal(monkeypatch, two_task)
        orch = _make_orchestrator(executor=_FixedGraphExecutor())

        save_calls: list[int] = []
        original = orch._persistence.save_checkpoint
        from strata.harness.persistence import Checkpoint

        def _spy(cp: Checkpoint) -> None:
            save_calls.append(len(cp.task_states))
            original(cp)

        monkeypatch.setattr(orch._persistence, "save_checkpoint", _spy)

        orch.run_goal("two tasks")

        assert len(save_calls) >= 2  # initial + per-task


class TestResumeFromCheckpoint:
    def test_resume_yes_skips_planning(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If a checkpoint exists and the user says yes, run_goal skips
        decompose_goal / confirm_plan and jumps to execution."""
        monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))
        graph = _single_task_graph()

        # First run: leave a checkpoint behind by making the task fail + abort.
        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        _stub_decompose_goal(monkeypatch, graph)
        ui1 = RecordingUI(confirm=True, error_decision="abort")
        orch1 = _make_orchestrator(ui=ui1, executor=_FailingExecutor(n_failures=99))
        monkeypatch.setattr(
            orch1._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.USER_INTERVENTION,
                description="t",
            ),
        )
        orch1.run_goal("list files")
        assert (tmp_path / "checkpoint.json").exists()

        # Second run: new orchestrator, checkpoint on disk, user says yes.
        plan_calls: list[str] = []

        def _unreachable_decompose(
            goal: str,
            router: object,
            actions: Sequence[str],
            context: Mapping[str, object] | None = None,
        ) -> TaskGraph:
            plan_calls.append(goal)
            return graph

        monkeypatch.setattr("strata.harness.orchestrator.decompose_goal", _unreachable_decompose)

        ui2 = RecordingUI(confirm=True, resume_yes=True)
        orch2 = _make_orchestrator(ui=ui2, executor=_FixedGraphExecutor())
        result = orch2.run_goal("list files")

        assert result.final_state == "COMPLETED"
        assert any(c[0] == "confirm_resume" for c in ui2.calls)
        # confirm_plan not called (resume path skips PLANNING/CONFIRMING).
        assert not any(c[0] == "confirm_plan" for c in ui2.calls)
        assert plan_calls == []  # decompose_goal not called

    def test_resume_no_runs_fresh_plan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """If the user declines to resume, the orchestrator clears the
        checkpoint and runs a normal PLANNING cycle."""
        monkeypatch.setenv("STRATA_STATE_DIR", str(tmp_path))

        # Seed a checkpoint manually via the PersistenceManager.
        from strata.core.types import TaskGraph as TG
        from strata.harness.persistence import Checkpoint, PersistenceManager

        pm = PersistenceManager(str(tmp_path))
        pm.save_checkpoint(
            Checkpoint(
                global_state="SCHEDULING",
                task_states={"old": "PENDING"},
                context={"goal": "old goal"},
                task_graph=TG(
                    goal="old goal",
                    tasks=(
                        TaskNode(
                            id="old",
                            task_type="primitive",
                            action="list_directory",
                            params={"path": "/"},
                        ),
                    ),
                ),
                timestamp=0.0,
            )
        )

        graph = _single_task_graph("new goal")
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True, resume_yes=False)
        orch = _make_orchestrator(ui=ui, executor=_FixedGraphExecutor())
        result = orch.run_goal("new goal")

        assert result.final_state == "COMPLETED"
        assert any(c[0] == "confirm_resume" for c in ui.calls)
        assert any(c[0] == "confirm_plan" for c in ui.calls)


class TestAuditLoggerInjection:
    def test_audit_logger_attached_to_default_executor(self) -> None:
        from strata.harness.executor import PrimitiveTaskExecutor

        orch = _make_orchestrator()
        assert isinstance(orch._executor, PrimitiveTaskExecutor)
        assert orch._executor._audit_logger is orch._audit_logger


class TestContextManagerWiring:
    def test_context_window_contains_completed_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)
        orch = _make_orchestrator(executor=_FixedGraphExecutor())
        orch.run_goal("list files")
        window = list(orch._context.get_window())
        assert any(entry.get("task_id") == "t1" for entry in window)
        assert any(entry.get("success") is True for entry in window)

    def test_splice_replan_uses_replacement_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify _splice_replan uses replacement_tasks from RecoveryAction
        to build an Adjustment, eliminating the double LLM call."""
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        from strata.planner.adjuster import apply_adjustment as _orig_apply

        captured_adjustments: list[object] = []

        def _fake_apply(graph: TaskGraph, adjustment: object) -> TaskGraph:
            captured_adjustments.append(adjustment)
            return _orig_apply(graph, adjustment)  # type: ignore[arg-type]

        monkeypatch.setattr("strata.harness.orchestrator.apply_adjustment", _fake_apply)

        from strata.harness.recovery import RecoveryAction, RecoveryLevel

        orch = _make_orchestrator(executor=_FailingExecutor(n_failures=99))
        monkeypatch.setattr(
            orch._recovery,
            "attempt_recovery",
            lambda task, error, count: RecoveryAction(
                level=RecoveryLevel.REPLAN,
                description="replan",
                replacement_tasks=(
                    TaskNode(
                        id="fallback",
                        task_type="primitive",
                        action="list_directory",
                        params={"path": "/"},
                    ),
                ),
            ),
        )
        orch.run_goal("list files")

        assert captured_adjustments, "apply_adjustment never invoked"


class TestUIInteractions:
    def test_display_plan_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True)
        orch = _make_orchestrator(ui=ui, executor=_FixedGraphExecutor())
        orch.run_goal("list files")

        assert any(c[0] == "display_plan" for c in ui.calls)
        assert any(c[0] == "confirm_plan" for c in ui.calls)

    def test_interrupted_ui_treated_as_abort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        ui = RecordingUI(confirm=True, interrupted_flag=True)
        orch = _make_orchestrator(ui=ui, executor=_FixedGraphExecutor())
        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
