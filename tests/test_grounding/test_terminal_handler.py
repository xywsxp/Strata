"""Tests for strata.grounding.terminal_handler."""

from __future__ import annotations

from unittest.mock import MagicMock

from strata.core.config import TerminalConfig
from strata.core.types import CommandResult
from strata.grounding.terminal_handler import TerminalHandler


def _default_config() -> TerminalConfig:
    return TerminalConfig(
        command_timeout=30.0,
        silence_timeout=10.0,
        default_shell="/bin/bash",
    )


def _make_terminal(returncode: int = 0) -> MagicMock:
    terminal = MagicMock()
    terminal.run_command.return_value = CommandResult(
        stdout="ok",
        stderr="",
        returncode=returncode,
        timed_out=False,
        interrupted_by_silence=False,
    )
    return terminal


class TestWrapCommand:
    def test_adds_token(self) -> None:
        handler = TerminalHandler(_make_terminal(), _default_config())
        wrapped = handler._wrap_command("ls -la")
        assert "AGENT_DONE_" in wrapped
        assert "ls -la" in wrapped


class TestSanitizeSudo:
    def test_adds_n_flag(self) -> None:
        handler = TerminalHandler(_make_terminal(), _default_config())
        result = handler._sanitize_sudo("sudo apt update")
        assert "sudo -n" in result

    def test_preserves_existing_n(self) -> None:
        handler = TerminalHandler(_make_terminal(), _default_config())
        result = handler._sanitize_sudo("sudo -n apt update")
        assert result == "sudo -n apt update"

    def test_no_sudo_unchanged(self) -> None:
        handler = TerminalHandler(_make_terminal(), _default_config())
        result = handler._sanitize_sudo("ls -la")
        assert result == "ls -la"


class TestExecuteCommand:
    def test_captures_exit_code(self) -> None:
        terminal = _make_terminal(0)
        handler = TerminalHandler(terminal, _default_config())
        result = handler.execute_command("echo hello")
        assert result.returncode == 0
        terminal.run_command.assert_called_once()

    def test_passes_cwd(self) -> None:
        terminal = _make_terminal()
        handler = TerminalHandler(terminal, _default_config())
        handler.execute_command("ls", cwd="/tmp")
        call_kwargs = terminal.run_command.call_args
        assert call_kwargs.kwargs.get("cwd") == "/tmp" or call_kwargs[1].get("cwd") == "/tmp"
