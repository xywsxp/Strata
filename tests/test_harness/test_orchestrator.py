"""Tests for :class:`strata.harness.orchestrator.AgentOrchestrator`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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

# ── Test helpers ──


@dataclass
class RecordingUI:
    """Minimal AgentUI implementation that records calls."""

    confirm: bool = True
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
    def test_task_failure_reaches_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        graph = _single_task_graph()
        _stub_decompose_goal(monkeypatch, graph)

        orch = _make_orchestrator(executor=_FailingExecutor(n_failures=99))
        result = orch.run_goal("list files")

        assert result.final_state == "FAILED"
        assert result.task_states["t1"] == "FAILED"


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
