"""Terminal command handler — wraps ITerminalAdapter with sudo sanitization.

The underlying PTY adapter embeds its own exit-code token, so this handler is
deliberately thin: it enforces the ``sudo -n`` rule across all sub-commands in
a pipeline or chain (``|``, ``&&``, ``||``, ``;``).

Each sub-command is tokenized with ``shlex`` so that ``sudo`` appearing inside
a quoted argument (e.g. ``echo 'sudo rm -rf /'``) is left untouched.
"""

from __future__ import annotations

import re
import shlex

import icontract

from strata.core.config import TerminalConfig
from strata.core.types import CommandResult
from strata.env.protocols import ITerminalAdapter

_SHELL_OP = re.compile(r"(&&|\|\||[;|])")


def _sanitize_sudo_segment(segment: str) -> str:
    """Inject ``-n`` into a single shell command segment if it starts with ``sudo``."""
    stripped = segment.strip()
    if not stripped:
        return segment
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError:
        return segment
    if not tokens or tokens[0] != "sudo":
        return segment
    if len(tokens) >= 2 and tokens[1] == "-n":
        return segment
    leading_ws = segment[: len(segment) - len(segment.lstrip())]
    rest = segment.lstrip()
    return leading_ws + "sudo -n" + rest[4:]


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
        """Ensure every ``sudo`` invocation in a pipeline/chain uses ``-n``.

        Splits on shell operators (``|``, ``&&``, ``||``, ``;``) then checks
        each segment independently via ``shlex`` tokenization.
        """
        parts = _SHELL_OP.split(command)
        result: list[str] = []
        for i, part in enumerate(parts):
            if i % 2 == 1:
                result.append(part)
            else:
                result.append(_sanitize_sudo_segment(part))
        return "".join(result)
