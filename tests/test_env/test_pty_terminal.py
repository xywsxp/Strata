"""Tests for strata.env.pty_terminal — real PTY terminal adapter."""

from __future__ import annotations

import icontract
import pytest

from strata.core.config import TerminalConfig
from strata.core.errors import CommandTimeoutError, SilenceTimeoutError
from strata.env.pty_terminal import PTYTerminalAdapter


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
        assert result.stderr == ""

    def test_false_returns_1(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("false", timeout=10.0)
        assert result.returncode == 1

    def test_timeout_raises(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(CommandTimeoutError):
            adapter.run_command("sleep 100", timeout=1.0)

    def test_silence_raises(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(SilenceTimeoutError):
            adapter.run_command("sleep 5", timeout=30.0, silence_timeout=0.5)

    def test_sudo_without_n_rejected(self) -> None:
        adapter = _make_adapter()
        with pytest.raises(icontract.ViolationError, match="sudo"):
            adapter.run_command("sudo rm -rf /", timeout=10.0)

    def test_sudo_with_n_allowed(self) -> None:
        adapter = _make_adapter()
        result = adapter.run_command("echo sudo -n test", timeout=10.0)
        assert result.returncode == 0


class TestPTYBehavior:
    def test_isatty_true_under_pty(self) -> None:
        """Child process sees its stdout as a TTY (the signature real-PTY behavior)."""
        adapter = _make_adapter()
        result = adapter.run_command(
            'python3 -c "import sys; print(sys.stdout.isatty())"',
            timeout=10.0,
        )
        assert result.returncode == 0
        assert "True" in result.stdout

    def test_stderr_merged_into_stdout(self) -> None:
        """Real PTY merges stderr into the single stream; stderr field stays empty."""
        adapter = _make_adapter()
        result = adapter.run_command("echo out && echo err 1>&2", timeout=10.0)
        assert result.returncode == 0
        assert "out" in result.stdout
        assert "err" in result.stdout
        assert result.stderr == ""


class TestPersistentSession:
    def test_open_send_read_close(self) -> None:
        adapter = _make_adapter()
        sid = adapter.open_terminal()
        try:
            adapter.send_to_terminal(sid, "echo session_works")
            import time

            time.sleep(0.3)
            output = adapter.read_terminal_output(sid, timeout=1.0)
            assert "session_works" in output
        finally:
            adapter.close_terminal(sid)
