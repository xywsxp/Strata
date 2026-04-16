"""Tests for strata.interaction.cli — CLI interface."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from strata.core.config import get_default_config
from strata.core.types import TaskGraph, TaskNode
from strata.interaction.cli import CLI


def _make_cli() -> CLI:
    return CLI(get_default_config())


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
