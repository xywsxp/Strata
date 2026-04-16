"""Tests for strata.interaction.cli — CLI interface."""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Any
from unittest.mock import patch

from strata.core.config import get_default_config
from strata.core.types import TaskGraph, TaskNode
from strata.harness.orchestrator import ExecutionResult
from strata.interaction.cli import CLI


def _make_cli() -> CLI:
    return CLI(get_default_config())


@dataclass
class _StubOrchestrator:
    """Minimal structural stand-in for :class:`AgentOrchestrator`."""

    final_state: str = "COMPLETED"
    calls: list[str] | None = None

    def run_goal(self, goal: str) -> ExecutionResult:
        if self.calls is None:
            self.calls = []
        self.calls.append(goal)
        return ExecutionResult(final_state=self.final_state, task_states={}, error=None)  # type: ignore[arg-type]


class TestDisplayPlan:
    def test_outputs_plan(self) -> None:
        cli = _make_cli()
        graph = TaskGraph(
            goal="open firefox",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="launch_app"),
                TaskNode(id="t2", task_type="primitive", action="click"),
            ),
        )
        buf = StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")):
            cli.display_plan(graph)
        output = buf.getvalue()
        assert "open firefox" in output
        assert "t1" in output
        assert "t2" in output


class TestConfirmPlan:
    def test_yes(self) -> None:
        cli = _make_cli()
        with patch("builtins.input", return_value="y"):
            assert cli.confirm_plan() is True

    def test_no(self) -> None:
        cli = _make_cli()
        with patch("builtins.input", return_value="n"):
            assert cli.confirm_plan() is False

    def test_eof(self) -> None:
        cli = _make_cli()
        with patch("builtins.input", side_effect=EOFError):
            assert cli.confirm_plan() is False


class TestHandleError:
    def test_retry(self) -> None:
        cli = _make_cli()
        with (
            patch("builtins.input", return_value="r"),
            patch("builtins.print"),
        ):
            assert cli.handle_error("t1", RuntimeError("fail")) == "retry"

    def test_skip(self) -> None:
        cli = _make_cli()
        with (
            patch("builtins.input", return_value="s"),
            patch("builtins.print"),
        ):
            assert cli.handle_error("t1", RuntimeError("fail")) == "skip"

    def test_abort(self) -> None:
        cli = _make_cli()
        with (
            patch("builtins.input", return_value="a"),
            patch("builtins.print"),
        ):
            assert cli.handle_error("t1", RuntimeError("fail")) == "abort"


class TestDisplayProgress:
    def test_displays_state(self) -> None:
        cli = _make_cli()
        buf = StringIO()
        with patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")):
            cli.display_progress("t1", "RUNNING")
        assert "RUNNING" in buf.getvalue()
        assert "t1" in buf.getvalue()


# ── Phase 11.2: auto_confirm_level dispatching ──


def _cli_with_level(level: str) -> CLI:
    import dataclasses

    cfg = dataclasses.replace(get_default_config(), auto_confirm_level=level)  # type: ignore[arg-type]
    return CLI(cfg)


class TestAutoConfirmLow:
    def test_asks_first_plan_auto_confirms_rest(self) -> None:
        cli = _cli_with_level("low")
        with patch("builtins.input", return_value="y"):
            assert cli.confirm_plan() is True
        # Subsequent call must not invoke input
        with patch("builtins.input", side_effect=AssertionError("should not ask")):
            assert cli.confirm_plan() is True


class TestAutoConfirmMedium:
    def test_plan_auto_confirmed(self) -> None:
        cli = _cli_with_level("medium")
        with patch("builtins.input", side_effect=AssertionError("should not ask")):
            assert cli.confirm_plan() is True

    def test_error_retries_then_asks(self) -> None:
        cli = _cli_with_level("medium")
        with patch("builtins.print"):
            assert cli.handle_error("t1", RuntimeError("x")) == "retry"
        with (
            patch("builtins.input", return_value="s"),
            patch("builtins.print"),
        ):
            assert cli.handle_error("t1", RuntimeError("x")) == "skip"


class TestAutoConfirmHigh:
    def test_plan_auto_confirmed(self) -> None:
        cli = _cli_with_level("high")
        with patch("builtins.input", side_effect=AssertionError("should not ask")):
            assert cli.confirm_plan() is True

    def test_error_auto_skipped(self) -> None:
        cli = _cli_with_level("high")
        with (
            patch("builtins.input", side_effect=AssertionError("should not ask")),
            patch("builtins.print"),
        ):
            assert cli.handle_error("t42", RuntimeError("boom")) == "skip"

    def test_destructive_auto_allowed(self) -> None:
        cli = _cli_with_level("high")
        with (
            patch("builtins.input", side_effect=AssertionError("should not ask")),
            patch("builtins.print"),
        ):
            assert cli.handle_destructive("rm -rf /tmp/foo") is True


class TestAutoConfirmNone:
    def test_plan_always_asks(self) -> None:
        cli = _cli_with_level("none")
        with patch("builtins.input", return_value="y"):
            assert cli.confirm_plan() is True
        # Second call must ask again
        with patch("builtins.input", return_value="n"):
            assert cli.confirm_plan() is False


# ── Phase 11.3: SIGINT scope ──


class TestSigintScope:
    def test_handler_restored_after_run(self) -> None:
        import signal as _signal

        def _marker_handler(signum: int, frame: object) -> None:
            pass

        previous = _signal.signal(_signal.SIGINT, _marker_handler)
        try:
            cli = _make_cli()
            orch: Any = _StubOrchestrator()
            with patch("builtins.input", side_effect=EOFError):
                cli.run(orch)
            current = _signal.getsignal(_signal.SIGINT)
            assert current is _marker_handler, (
                f"expected SIGINT handler to be restored to _marker_handler, got {current!r}"
            )
        finally:
            _signal.signal(_signal.SIGINT, previous)

    def test_construction_does_not_install_global_handler(self) -> None:
        """Regression: CLI(config) must no longer install a SIGINT handler
        as a side effect of construction."""
        import signal as _signal

        def _marker(signum: int, frame: object) -> None:
            pass

        previous = _signal.signal(_signal.SIGINT, _marker)
        try:
            CLI(get_default_config())
            assert _signal.getsignal(_signal.SIGINT) is _marker
        finally:
            _signal.signal(_signal.SIGINT, previous)


# ── Phase B: run(orchestrator) delegates properly ──


class TestRunDelegatesToOrchestrator:
    def test_single_goal_runs_once_then_exits_on_quit(self) -> None:
        cli = _make_cli()
        orch = _StubOrchestrator(final_state="COMPLETED")
        with (
            patch("builtins.input", side_effect=["do thing", "quit"]),
            patch("builtins.print"),
        ):
            cli.run(orch)  # type: ignore[arg-type]
        assert orch.calls == ["do thing"]

    def test_loops_until_eof(self) -> None:
        cli = _make_cli()
        orch = _StubOrchestrator(final_state="COMPLETED")
        with (
            patch("builtins.input", side_effect=["a", "b", EOFError]),
            patch("builtins.print"),
        ):
            cli.run(orch)  # type: ignore[arg-type]
        assert orch.calls == ["a", "b"]

    def test_prints_completed(self) -> None:
        cli = _make_cli()
        orch = _StubOrchestrator(final_state="COMPLETED")
        buf = StringIO()
        with (
            patch("builtins.input", side_effect=["do thing", "quit"]),
            patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")),
        ):
            cli.run(orch)  # type: ignore[arg-type]
        assert "Goal completed" in buf.getvalue()

    def test_prints_failed(self) -> None:
        cli = _make_cli()
        orch = _StubOrchestrator(final_state="FAILED")
        buf = StringIO()
        with (
            patch("builtins.input", side_effect=["do thing", "quit"]),
            patch("builtins.print", side_effect=lambda *a, **kw: buf.write(str(a[0]) + "\n")),
        ):
            cli.run(orch)  # type: ignore[arg-type]
        assert "Goal failed" in buf.getvalue()

    def test_empty_goal_does_not_call_orchestrator(self) -> None:
        cli = _make_cli()
        orch = _StubOrchestrator(final_state="COMPLETED")
        with (
            patch("builtins.input", side_effect=["", "quit"]),
            patch("builtins.print"),
        ):
            cli.run(orch)  # type: ignore[arg-type]
        assert orch.calls is None or orch.calls == []
