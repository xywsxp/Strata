"""Real PTY terminal adapter (Linux + macOS shared).

Uses ``pty.openpty`` + ``os.dup2`` to give the child a genuine controlling
terminal: ``isatty`` returns True, ncurses / password prompts / progress bars
behave correctly. All output flows through the master fd as a single stream.

# CONVENTION: 使用 pty.openpty() 替代 subprocess.PIPE — TUI/isatty 行为正确
# CONVENTION: stderr 字段在 PTY 模式下恒为空串 — 真 PTY 单流语义，不复制 stdout 以避免误导调用方

Timeout / silence signals are exceptions, not fields:
- `CommandTimeoutError` — wall-clock exceeded `timeout`
- `SilenceTimeoutError` — no output for longer than `silence_timeout`

Callers perceive failure via `try / except` (exception = single source of truth).
`CommandResult` only represents successful execution.
"""

from __future__ import annotations

import contextlib
import errno
import os
import pty
import select
import signal
import subprocess
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import icontract

from strata.core.config import TerminalConfig
from strata.core.errors import CommandTimeoutError, SilenceTimeoutError
from strata.core.types import CommandResult


@dataclass
class _PTYSession:
    """Typed container for a PTY-backed shell session (replaces monkey-patching)."""

    proc: subprocess.Popen[bytes]
    master_fd: int


class PTYTerminalAdapter:
    """ITerminalAdapter backed by a genuine pseudo-terminal.

    ``CommandResult.stderr`` is always the empty string — a PTY merges stdout
    and stderr at the kernel level; callers must rely on ``returncode`` and
    exception types, not on parsing ``stderr``.
    """

    def __init__(self, config: TerminalConfig) -> None:
        self._config = config
        self._sessions: dict[str, _PTYSession] = {}

    @icontract.require(lambda command: len(command.strip()) > 0, "command must be non-empty")
    @icontract.require(lambda timeout: timeout > 0, "timeout must be positive")
    @icontract.require(
        lambda command: "sudo" not in command or "-n" in command,
        "sudo commands must include -n flag",
    )
    @icontract.ensure(
        lambda result: isinstance(result.returncode, int),
        "returncode must be int",
    )
    @icontract.ensure(
        lambda result: result.stderr == "",
        "PTY single-stream invariant: stderr is always empty",
    )
    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 300.0,
        silence_timeout: float | None = 30.0,
    ) -> CommandResult:
        """Execute *command* under a real PTY and return the result.

        Raises:
            CommandTimeoutError: wall-clock ``timeout`` exceeded.
            SilenceTimeoutError: no output for longer than ``silence_timeout``.
        """
        if silence_timeout is None:
            silence_timeout = self._config.silence_timeout

        token = f"__STRATA_DONE_{uuid.uuid4().hex}__"
        wrapped = f'{command}\nprintf "%s %d\\n" "{token}" "$?"'

        run_env: dict[str, str] | None = None
        if env is not None:
            run_env = {**os.environ, **dict(env)}

        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                [self._config.default_shell, "-c", wrapped],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=run_env,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        os.close(slave_fd)

        try:
            stdout_full = self._collect_output(proc, master_fd, token, timeout, silence_timeout)
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)

        returncode = self._extract_exit_code(stdout_full, token, proc.returncode or 0)
        stdout_clean = stdout_full.split(token)[0].rstrip("\n")
        return CommandResult(stdout=stdout_clean, stderr="", returncode=returncode)

    def _collect_output(
        self,
        proc: subprocess.Popen[bytes],
        master_fd: int,
        token: str,
        timeout: float,
        silence_timeout: float | None,
    ) -> str:
        chunks: list[str] = []
        start = time.monotonic()
        last_output = start
        os.set_blocking(master_fd, False)

        while True:
            if proc.poll() is not None and not self._has_pending_read(master_fd):
                break

            now = time.monotonic()
            if now - start > timeout:
                self._kill(proc)
                self._drain_until_eof(master_fd, chunks)
                raise CommandTimeoutError(
                    f"command exceeded timeout={timeout}s after {now - start:.2f}s"
                )
            if silence_timeout and (now - last_output) > silence_timeout:
                self._kill(proc)
                self._drain_until_eof(master_fd, chunks)
                raise SilenceTimeoutError(
                    f"command produced no output for {now - last_output:.2f}s "
                    f"(silence_timeout={silence_timeout}s)"
                )

            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if not ready:
                continue
            try:
                data = os.read(master_fd, 8192)
            except OSError as e:
                if e.errno in (errno.EIO, errno.EBADF):
                    break
                raise
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
            last_output = time.monotonic()
            if token in "".join(chunks):
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._kill(proc)
                break

        self._drain_until_eof(master_fd, chunks)
        return "".join(chunks)

    @staticmethod
    def _has_pending_read(fd: int) -> bool:
        ready, _, _ = select.select([fd], [], [], 0)
        return bool(ready)

    @staticmethod
    def _drain_until_eof(fd: int, chunks: list[str]) -> None:
        while True:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            chunks.append(data.decode("utf-8", errors="replace"))

    @staticmethod
    def _extract_exit_code(stdout: str, token: str, fallback: int) -> int:
        if token not in stdout:
            return fallback
        tail = stdout.split(token)[-1].strip()
        if not tail:
            return fallback
        first = tail.split()[0]
        try:
            return int(first)
        except ValueError:
            return fallback

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        except OSError:
            pass

    def open_terminal(self, cwd: str | None = None) -> str:
        """Open a persistent PTY-backed shell session."""
        session_id = uuid.uuid4().hex
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [self._config.default_shell],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        os.set_blocking(master_fd, False)
        self._sessions[session_id] = _PTYSession(proc=proc, master_fd=master_fd)
        return session_id

    def send_to_terminal(self, session_id: str, text: str) -> None:
        session = self._sessions[session_id]
        os.write(session.master_fd, (text + "\n").encode("utf-8"))

    def read_terminal_output(self, session_id: str, timeout: float = 1.0) -> str:
        session = self._sessions[session_id]
        ready, _, _ = select.select([session.master_fd], [], [], timeout)
        if not ready:
            return ""
        try:
            return os.read(session.master_fd, 8192).decode("utf-8", errors="replace")
        except OSError:
            return ""

    def close_terminal(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        self._kill(session.proc)
        with contextlib.suppress(OSError):
            os.close(session.master_fd)
