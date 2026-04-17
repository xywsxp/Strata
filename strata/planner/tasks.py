"""Declarative task file loading for OSWorld evaluation.

Each ``.toml`` file under ``tasks/`` describes a single evaluation goal with
optional setup commands and verification criteria.  ``TaskFile.load`` parses
and validates the TOML; ``TaskFile.load_many`` enforces uniqueness.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import icontract

from strata import StrataError

_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class TaskFileError(StrataError):
    """Invalid or unparseable task definition file."""


@dataclass(frozen=True)
class SetupSpec:
    target: Literal["host", "osworld"]
    commands: Sequence[str]


@dataclass(frozen=True)
class VerifySpec:
    target: Literal["host", "osworld"]
    command: str
    expected_stdout_regex: str | None
    expected_exit_code: int | None


@dataclass(frozen=True)
class TaskFile:
    id: str
    goal: str
    tags: Sequence[str]
    timeout_s: float
    max_iterations: int | None
    setup: SetupSpec | None
    verify: VerifySpec | None
    source_path: Path

    @classmethod
    @icontract.require(
        lambda path: path.exists() and path.suffix == ".toml",
        "path must exist and be a .toml file",
    )
    def load(cls, path: Path) -> TaskFile:
        """Parse and validate a single task TOML file."""
        try:
            text = path.read_text(encoding="utf-8")
            data = tomllib.loads(text)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise TaskFileError(f"cannot parse {path}: {exc}") from exc

        task_section = data.get("task")
        if not isinstance(task_section, dict):
            raise TaskFileError(f"{path}: missing [task] section")

        task_id = str(task_section.get("id", ""))
        if not _TASK_ID_RE.match(task_id):
            raise TaskFileError(f"{path}: task.id {task_id!r} must match {_TASK_ID_RE.pattern}")

        goal = str(task_section.get("goal", ""))
        if not goal.strip():
            raise TaskFileError(f"{path}: task.goal must be non-empty")

        tags = tuple(str(t) for t in task_section.get("tags", ()))
        timeout_s = float(task_section.get("timeout_s", 120))
        if timeout_s <= 0:
            raise TaskFileError(f"{path}: task.timeout_s must be > 0")

        max_iter_raw = task_section.get("max_iterations")
        max_iterations = int(max_iter_raw) if max_iter_raw is not None else None

        setup = _parse_setup(data.get("setup"), path)
        verify = _parse_verify(data.get("verify"), path)

        return cls(
            id=task_id,
            goal=goal,
            tags=tags,
            timeout_s=timeout_s,
            max_iterations=max_iterations,
            setup=setup,
            verify=verify,
            source_path=path,
        )

    @classmethod
    @icontract.require(lambda paths: len(paths) > 0, "must provide at least one path")
    @icontract.ensure(
        lambda result: len({t.id for t in result}) == len(result),
        "task IDs must be unique across loaded files",
    )
    def load_many(cls, paths: Sequence[Path]) -> Sequence[TaskFile]:
        """Load and validate multiple task files, enforcing ID uniqueness."""
        tasks: list[TaskFile] = []
        seen_ids: set[str] = set()
        for p in paths:
            task = cls.load(p)
            if task.id in seen_ids:
                raise TaskFileError(
                    f"duplicate task id {task.id!r}: {p} conflicts with an earlier file"
                )
            seen_ids.add(task.id)
            tasks.append(task)
        return tuple(tasks)


def _parse_setup(raw: object, path: Path) -> SetupSpec | None:
    if not isinstance(raw, dict):
        return None
    target = str(raw.get("target", "host"))
    if target not in ("host", "osworld"):
        raise TaskFileError(f"{path}: setup.target must be 'host' or 'osworld'")
    commands = tuple(str(c) for c in raw.get("commands", ()))
    if not commands:
        return None
    return SetupSpec(target=cast(Literal["host", "osworld"], target), commands=commands)


def _parse_verify(raw: object, path: Path) -> VerifySpec | None:
    if not isinstance(raw, dict):
        return None
    target = str(raw.get("target", "host"))
    if target not in ("host", "osworld"):
        raise TaskFileError(f"{path}: verify.target must be 'host' or 'osworld'")
    command = str(raw.get("command", ""))
    if not command.strip():
        raise TaskFileError(f"{path}: verify.command must be non-empty")
    regex = raw.get("expected_stdout_regex")
    exit_code = raw.get("expected_exit_code")
    if regex is None and exit_code is None:
        raise TaskFileError(f"{path}: verify must have expected_stdout_regex or expected_exit_code")
    return VerifySpec(
        target=cast(Literal["host", "osworld"], target),
        command=command,
        expected_stdout_regex=str(regex) if regex is not None else None,
        expected_exit_code=int(exit_code) if exit_code is not None else None,
    )
