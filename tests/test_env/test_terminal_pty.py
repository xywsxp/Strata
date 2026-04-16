"""Tests for strata.env.terminal_pty — PTY terminal adapter."""

from __future__ import annotations

import icontract
import pytest

from strata.core.config import TerminalConfig
from strata.env.terminal_pty import PTYTerminalAdapter


def _make_adapter() -> PTYTerminalAdapter:
    return PTYTerminalAdapter(
        TerminalConfig(command_timeout=300.0, silence_timeout=30.0, default_shell="/bin/bash")
    )


class TestRunEcho:
    def test_echo_success(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("echo hello", timeout=10.0)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_false_returns_1(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("false", timeout=10.0)
        assert result.returncode == 1

    def test_timeout(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("sleep 100", timeout=1.0)
        assert result.timed_out is True

    def test_sudo_without_n_rejected(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(icontract.ViolationError, match="sudo"):
            adapter.run_command("sudo rm -rf /", timeout=10.0)

    def test_sudo_with_n_allowed(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("echo sudo -n test", timeout=10.0)
        assert result.returncode == 0
