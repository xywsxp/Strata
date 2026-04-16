"""Terminal command handler — wraps ITerminalAdapter with prompt tokens and sudo sanitization."""

from __future__ import annotations

import re
import uuid

import icontract

from strata.core.config import TerminalConfig
from strata.core.types import CommandResult
from strata.env.protocols import ITerminalAdapter


class TerminalHandler:
    """Wraps terminal adapter with prompt-token exit code capture and sudo handling."""

    def __init__(self, terminal: ITerminalAdapter, config: TerminalConfig) -> None:
        self._terminal = terminal
        self._config = config

    @icontract.require(lambda command: len(command.strip()) > 0, "command must be non-empty")
    @icontract.ensure(lambda result: isinstance(result.returncode, int), "returncode must be int")
    def execute_command(self, command: str, cwd: str | None = None) -> CommandResult:
        """Execute a command via the terminal adapter."""
        sanitized = self._sanitize_sudo(command)
        return self._terminal.run_command(
            sanitized,
            cwd=cwd,
            timeout=self._config.command_timeout,
            silence_timeout=self._config.silence_timeout,
        )

    def _wrap_command(self, command: str) -> str:
        """Add a unique prompt token for exit code extraction."""
        token = f"AGENT_DONE_{uuid.uuid4().hex[:12]}"
        return f"{command}; echo '{token}' $?"

    @icontract.ensure(
        lambda command, result: (
            "sudo" not in command or "sudo -n" in result or "sudo" not in result
        ),
        "sudo commands must have -n flag",
    )
    def _sanitize_sudo(self, command: str) -> str:
        """Ensure all sudo invocations use -n (non-interactive)."""
        return re.sub(r"\bsudo\b(?!\s+-n)", "sudo -n", command)
