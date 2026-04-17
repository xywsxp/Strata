"""Terminal command handler — wraps ITerminalAdapter with sudo sanitization.

The underlying PTY adapter embeds its own exit-code token, so this handler is
deliberately thin: it only enforces the ``sudo -n`` rule via a shell-lex aware
check on the first token. Regex-based rewriting is avoided because it ignores
shell word boundaries (e.g. ``"sudo"`` inside a quoted string literal).
"""

from __future__ import annotations

import shlex

import icontract

from strata.core.config import TerminalConfig
from strata.core.types import CommandResult
from strata.env.protocols import ITerminalAdapter


class TerminalHandler:
    """Wraps terminal adapter with sudo-safety sanitization."""

    def __init__(self, terminal: ITerminalAdapter, config: TerminalConfig) -> None:
        self._terminal = terminal
        self._config = config

    @icontract.require(lambda command: len(command.strip()) > 0, "command must be non-empty")
    @icontract.ensure(lambda result: isinstance(result.returncode, int), "returncode must be int")
    def execute_command(self, command: str, cwd: str | None = None) -> CommandResult:
        """Execute *command* via the terminal adapter after sudo sanitization."""
        sanitized = self._sanitize_sudo(command)
        return self._terminal.run_command(
            sanitized,
            cwd=cwd,
            timeout=self._config.command_timeout,
            silence_timeout=self._config.silence_timeout,
        )

    def _sanitize_sudo(self, command: str) -> str:
        """Ensure a leading ``sudo`` invocation always uses ``-n``.

        Uses ``shlex`` tokenization so that ``sudo`` appearing inside a quoted
        argument (e.g. ``echo 'sudo rm -rf /'``) is left untouched. Only the
        first shell word of the command is inspected.
        """
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return command
        if not tokens or tokens[0] != "sudo":
            return command
        if len(tokens) >= 2 and tokens[1] == "-n":
            return command
        return "sudo -n " + command[len("sudo") :].lstrip()
