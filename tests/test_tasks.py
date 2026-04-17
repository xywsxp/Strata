"""Tests for strata.tasks — TaskFile TOML loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import icontract
import pytest

from strata.planner.tasks import TaskFile, TaskFileError

MINIMAL_TASK = textwrap.dedent("""\
    [task]
    id = "hello-world"
    goal = "Create a file at /tmp/hello.txt"
""")

FULL_TASK = textwrap.dedent("""\
    [task]
    id = "full-example"
    goal = "Do something complex"
    tags = ["smoke", "filesystem"]
    timeout_s = 60
    max_iterations = 10

    [setup]
    target = "host"
    commands = ["rm -f /tmp/hello.txt"]

    [verify]
    target = "host"
    command = "cat /tmp/hello.txt"
    expected_stdout_regex = "^hello$"
""")


class TestTaskFileLoad:
    def test_load_minimal(self, tmp_path: Path) -> None:
        p = tmp_path / "t.toml"
        p.write_text(MINIMAL_TASK)
        task = TaskFile.load(p)
        assert task.id == "hello-world"
        assert task.goal == "Create a file at /tmp/hello.txt"
        assert task.tags == ()
        assert task.timeout_s == 120
        assert task.max_iterations is None
        assert task.setup is None
        assert task.verify is None
        assert task.source_path == p

    def test_load_full(self, tmp_path: Path) -> None:
        p = tmp_path / "t.toml"
        p.write_text(FULL_TASK)
        task = TaskFile.load(p)
        assert task.id == "full-example"
        assert task.tags == ("smoke", "filesystem")
        assert task.timeout_s == 60
        assert task.max_iterations == 10
        assert task.setup is not None
        assert task.setup.target == "host"
        assert task.setup.commands == ("rm -f /tmp/hello.txt",)
        assert task.verify is not None
        assert task.verify.expected_stdout_regex == "^hello$"

    def test_rejects_bad_id_character(self, tmp_path: Path) -> None:
        p = tmp_path / "t.toml"
        p.write_text('[task]\nid = "BAD_ID"\ngoal = "test"\n')
        with pytest.raises(TaskFileError, match="must match"):
            TaskFile.load(p)

    def test_rejects_empty_goal(self, tmp_path: Path) -> None:
        p = tmp_path / "t.toml"
        p.write_text('[task]\nid = "ok-id"\ngoal = ""\n')
        with pytest.raises(TaskFileError, match="non-empty"):
            TaskFile.load(p)

    def test_rejects_nonexistent_path(self, tmp_path: Path) -> None:
        p = tmp_path / "nope.toml"
        with pytest.raises(icontract.ViolationError):
            TaskFile.load(p)

    def test_rejects_non_toml_suffix(self, tmp_path: Path) -> None:
        p = tmp_path / "task.json"
        p.write_text("{}")
        with pytest.raises(icontract.ViolationError):
            TaskFile.load(p)

    def test_verify_requires_at_least_one_expectation(self, tmp_path: Path) -> None:
        toml = textwrap.dedent("""\
            [task]
            id = "verify-bad"
            goal = "test"

            [verify]
            command = "echo hi"
        """)
        p = tmp_path / "t.toml"
        p.write_text(toml)
        with pytest.raises(TaskFileError, match="expected_stdout_regex"):
            TaskFile.load(p)

    def test_verify_with_exit_code_only(self, tmp_path: Path) -> None:
        toml = textwrap.dedent("""\
            [task]
            id = "exit-code-only"
            goal = "test"

            [verify]
            command = "true"
            expected_exit_code = 0
        """)
        p = tmp_path / "t.toml"
        p.write_text(toml)
        task = TaskFile.load(p)
        assert task.verify is not None
        assert task.verify.expected_exit_code == 0
        assert task.verify.expected_stdout_regex is None


class TestTaskFileLoadMany:
    def test_load_many_unique(self, tmp_path: Path) -> None:
        (tmp_path / "a.toml").write_text('[task]\nid = "task-a"\ngoal = "A"\n')
        (tmp_path / "b.toml").write_text('[task]\nid = "task-b"\ngoal = "B"\n')
        tasks = TaskFile.load_many([tmp_path / "a.toml", tmp_path / "b.toml"])
        assert len(tasks) == 2

    def test_load_many_detects_duplicate_id(self, tmp_path: Path) -> None:
        (tmp_path / "a.toml").write_text('[task]\nid = "dup"\ngoal = "A"\n')
        (tmp_path / "b.toml").write_text('[task]\nid = "dup"\ngoal = "B"\n')
        with pytest.raises(TaskFileError, match="duplicate"):
            TaskFile.load_many([tmp_path / "a.toml", tmp_path / "b.toml"])

    def test_load_many_rejects_empty(self) -> None:
        with pytest.raises(icontract.ViolationError):
            TaskFile.load_many([])
