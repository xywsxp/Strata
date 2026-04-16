"""Tests for :class:`strata.harness.executor.PrimitiveTaskExecutor`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import icontract
import pytest
from hypothesis import given, settings

from strata import StrataError
from strata.core.errors import (
    ActionParamsError,
    OrchestrationError,
    UnknownActionError,
)
from strata.core.types import (
    ActionResult,
    AppInfo,
    CommandResult,
    FileInfo,
    ScreenRegion,
    TaskNode,
)
from strata.env.protocols import (
    EnvironmentBundle,
)
from strata.harness.executor import PrimitiveTaskExecutor

from ..strategies import st_primitive_task_node

# ── Mock adapters ──


@dataclass
class MockGUI:
    screen_size: tuple[int, int] = (1920, 1080)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def click(self, x: float, y: float, button: str = "left") -> None:
        self.calls.append(("click", (x, y, button)))

    def double_click(self, x: float, y: float) -> None:
        self.calls.append(("double_click", (x, y)))

    def move_mouse(self, x: float, y: float) -> None:
        self.calls.append(("move_mouse", (x, y)))

    def type_text(self, text: str, interval: float = 0.05) -> None:
        self.calls.append(("type_text", (text, interval)))

    def press_key(self, key: str) -> None:
        self.calls.append(("press_key", (key,)))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", keys))

    def scroll(self, delta_x: int, delta_y: int) -> None:
        self.calls.append(("scroll", (delta_x, delta_y)))

    def get_screen_size(self) -> tuple[int, int]:
        return self.screen_size

    def capture_screen(self, region: ScreenRegion | None = None) -> bytes:
        self.calls.append(("capture_screen", (region,)))
        return b"fake-image"

    def get_dpi_scale_for_point(self, x: float, y: float) -> float:
        return 1.0


@dataclass
class MockTerminal:
    result: CommandResult = field(
        default_factory=lambda: CommandResult(stdout="ok", stderr="", returncode=0)
    )
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 300.0,
        silence_timeout: float | None = 30.0,
    ) -> CommandResult:
        self.calls.append(("run_command", (command, cwd)))
        return self.result

    def open_terminal(self, cwd: str | None = None) -> str:
        return "term-0"

    def send_to_terminal(self, session_id: str, text: str) -> None:
        self.calls.append(("send_to_terminal", (session_id, text)))

    def read_terminal_output(self, session_id: str, timeout: float = 1.0) -> str:
        return ""

    def close_terminal(self, session_id: str) -> None:
        self.calls.append(("close_terminal", (session_id,)))


@dataclass
class MockFileSystem:
    file_contents: dict[str, str] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def read_file(self, path: str) -> str:
        self.calls.append(("read_file", (path,)))
        return self.file_contents.get(path, "")

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.calls.append(("write_file", (path, content, encoding)))
        self.file_contents[path] = content

    def list_directory(self, path: str, pattern: str | None = None) -> Sequence[FileInfo]:
        self.calls.append(("list_directory", (path, pattern)))
        return (
            FileInfo(path=f"{path}/a.txt", name="a.txt", is_dir=False, size=4, modified_at=0.0),
        )

    def move_to_trash(self, path: str) -> str:
        self.calls.append(("move_to_trash", (path,)))
        return f"/tmp/trash/{path}"

    def restore_from_trash(self, trash_path: str) -> None:
        self.calls.append(("restore_from_trash", (trash_path,)))

    def get_file_info(self, path: str) -> FileInfo:
        return FileInfo(path=path, name=path, is_dir=False, size=0, modified_at=0.0)


@dataclass
class MockAppManager:
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def launch_app(self, app_name: str, args: Sequence[str] | None = None) -> str:
        self.calls.append(("launch_app", (app_name, args)))
        return f"handle-{app_name}"

    def close_app(self, app_identifier: str) -> None:
        self.calls.append(("close_app", (app_identifier,)))

    def get_running_apps(self) -> Sequence[AppInfo]:
        return ()

    def switch_to_app(self, app_identifier: str) -> None:
        self.calls.append(("switch_to_app", (app_identifier,)))


@dataclass
class MockSystem:
    clipboard: str = ""
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def get_clipboard_text(self) -> str:
        self.calls.append(("get_clipboard_text", ()))
        return self.clipboard

    def set_clipboard_text(self, text: str) -> None:
        self.calls.append(("set_clipboard_text", (text,)))
        self.clipboard = text

    def get_environment_variable(self, name: str) -> str | None:
        return None

    def set_environment_variable(self, name: str, value: str) -> None:
        self.calls.append(("set_environment_variable", (name, value)))

    def get_cwd(self) -> str:
        return "/"

    def set_cwd(self, path: str) -> None:
        self.calls.append(("set_cwd", (path,)))


_BundleTuple = tuple[
    EnvironmentBundle, MockGUI, MockTerminal, MockFileSystem, MockAppManager, MockSystem
]
_ExecutorTuple = tuple[PrimitiveTaskExecutor, MockGUI, MockFileSystem, MockAppManager, MockSystem]


def make_bundle() -> _BundleTuple:
    gui = MockGUI()
    terminal = MockTerminal()
    fs = MockFileSystem()
    apps = MockAppManager()
    sys_ = MockSystem()
    bundle = EnvironmentBundle(
        gui=gui,
        terminal=terminal,
        filesystem=fs,
        app_manager=apps,
        system=sys_,
    )
    return bundle, gui, terminal, fs, apps, sys_


def make_executor(**overrides: object) -> _ExecutorTuple:
    bundle, gui, _term, fs, apps, sys_ = make_bundle()
    kwargs: dict[str, Any] = {"bundle": bundle}
    kwargs.update(overrides)
    executor = PrimitiveTaskExecutor(**kwargs)
    return executor, gui, fs, apps, sys_


# ── Contract violation tests ──


def test_non_primitive_task_rejected() -> None:
    executor, *_ = make_executor()
    node = TaskNode(id="c1", task_type="compound", action=None, method="m1")
    with pytest.raises((OrchestrationError, icontract.ViolationError)):
        executor.execute(node, {})


def test_unknown_action_rejected() -> None:
    executor, *_ = make_executor()
    node = TaskNode(id="p1", task_type="primitive", action="__no_such_action__", params={})
    with pytest.raises((UnknownActionError, icontract.ViolationError)):
        executor.execute(node, {})


def test_missing_required_params_raises() -> None:
    executor, *_ = make_executor()
    node = TaskNode(id="p1", task_type="primitive", action="click", params={"x": 10.0})
    with pytest.raises(ActionParamsError):
        executor.execute(node, {})


# ── Filesystem actions ──


def test_read_file_delegates() -> None:
    executor, _gui, fs, _apps, _sys = make_executor()
    fs.file_contents = {"/tmp/a.txt": "hello"}
    node = TaskNode(
        id="p", task_type="primitive", action="read_file", params={"path": "/tmp/a.txt"}
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert result.data is not None and result.data["content"] == "hello"


def test_write_file_delegates() -> None:
    executor, _gui, fs, _apps, _sys = make_executor()
    node = TaskNode(
        id="p",
        task_type="primitive",
        action="write_file",
        params={"path": "/tmp/b.txt", "content": "world"},
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert fs.file_contents["/tmp/b.txt"] == "world"


def test_list_directory_delegates() -> None:
    executor, _gui, fs, _apps, _sys = make_executor()
    node = TaskNode(id="p", task_type="primitive", action="list_directory", params={"path": "/tmp"})
    result = executor.execute(node, {})
    assert result.success is True
    assert ("list_directory", ("/tmp", None)) in fs.calls


def test_move_to_trash_delegates() -> None:
    executor, _gui, fs, _apps, _sys = make_executor()
    node = TaskNode(
        id="p", task_type="primitive", action="move_to_trash", params={"path": "/tmp/old.txt"}
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert result.data is not None and result.data["trash_path"] == "/tmp/trash//tmp/old.txt"


# ── App manager actions ──


def test_launch_app_delegates() -> None:
    executor, _gui, _fs, apps, _sys = make_executor()
    node = TaskNode(
        id="p", task_type="primitive", action="launch_app", params={"app_name": "Calculator"}
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert apps.calls[0] == ("launch_app", ("Calculator", None))


def test_close_app_delegates() -> None:
    executor, _gui, _fs, apps, _sys = make_executor()
    node = TaskNode(
        id="p", task_type="primitive", action="close_app", params={"app_identifier": "handle-x"}
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert apps.calls[0] == ("close_app", ("handle-x",))


# ── System actions ──


def test_get_clipboard_delegates() -> None:
    executor, _gui, _fs, _apps, sys_ = make_executor()
    sys_.clipboard = "hello-clip"
    node = TaskNode(id="p", task_type="primitive", action="get_clipboard", params={})
    result = executor.execute(node, {})
    assert result.success is True
    assert result.data is not None and result.data["text"] == "hello-clip"


def test_set_clipboard_delegates() -> None:
    executor, _gui, _fs, _apps, sys_ = make_executor()
    node = TaskNode(
        id="p", task_type="primitive", action="set_clipboard", params={"text": "payload"}
    )
    result = executor.execute(node, {})
    assert result.success is True
    assert sys_.clipboard == "payload"


# ── GUI actions ──


def test_click_without_lock() -> None:
    executor, gui, _fs, _apps, _sys = make_executor()
    node = TaskNode(id="p", task_type="primitive", action="click", params={"x": 100.0, "y": 200.0})
    result = executor.execute(node, {})
    assert result.success is True
    assert gui.calls[0] == ("click", (100.0, 200.0, "left"))


def test_type_text_delegates() -> None:
    executor, gui, _fs, _apps, _sys = make_executor()
    node = TaskNode(id="p", task_type="primitive", action="type_text", params={"text": "hi"})
    result = executor.execute(node, {})
    assert result.success is True
    assert gui.calls[0][0] == "type_text"


def test_hotkey_empty_keys_rejected() -> None:
    executor, *_ = make_executor()
    node = TaskNode(id="p", task_type="primitive", action="hotkey", params={"keys": []})
    with pytest.raises(ActionParamsError):
        executor.execute(node, {})


def test_scroll_all_zero_rejected() -> None:
    executor, *_ = make_executor()
    node = TaskNode(
        id="p",
        task_type="primitive",
        action="scroll",
        params={"delta_x": 0, "delta_y": 0},
    )
    with pytest.raises(ActionParamsError):
        executor.execute(node, {})


def test_locate_and_click_without_vision_returns_failed() -> None:
    executor, *_ = make_executor()
    node = TaskNode(
        id="p",
        task_type="primitive",
        action="locate_and_click",
        params={"description": "OK button"},
    )
    result = executor.execute(node, {})
    assert result.success is False
    assert result.error is not None
    assert "VisionLocator" in result.error


def test_execute_command_without_handler_returns_failed() -> None:
    executor, *_ = make_executor()
    node = TaskNode(
        id="p",
        task_type="primitive",
        action="execute_command",
        params={"command": "echo hi"},
    )
    result = executor.execute(node, {})
    assert result.success is False
    assert result.error is not None
    assert "TerminalHandler" in result.error


# ── Property: env actions never raise stdlib exceptions ──

_ENV_ACTIONS = frozenset(
    {
        "read_file",
        "write_file",
        "list_directory",
        "move_to_trash",
        "launch_app",
        "close_app",
        "get_clipboard",
        "set_clipboard",
    }
)


@given(st_primitive_task_node().filter(lambda t: t.action in _ENV_ACTIONS))
@settings(max_examples=30)
def test_env_actions_only_raise_strata_errors(task: TaskNode) -> None:
    executor, *_ = make_executor()
    try:
        result = executor.execute(task, {})
        assert isinstance(result, ActionResult)
    except StrataError:
        pass
    except icontract.ViolationError:
        pass


# ── GUI lock integration ──


class TestGUILockIntegration:
    def test_click_with_lock_serializes(self) -> None:
        from strata.core.config import get_default_config
        from strata.harness.gui_lock import GUILock

        lock = GUILock(get_default_config().gui)
        executor, gui, _fs, _apps, _sys = make_executor(gui_lock=lock)
        node = TaskNode(
            id="p", task_type="primitive", action="click", params={"x": 10.0, "y": 20.0}
        )
        result = executor.execute(node, {})
        assert result.success is True
        assert not lock.locked()
        assert gui.calls[0][0] == "click"

    def test_click_releases_lock_on_failure(self) -> None:
        from strata.core.config import get_default_config
        from strata.harness.gui_lock import GUILock

        class _FailingGUI(MockGUI):
            def click(self, x: float, y: float, button: str = "left") -> None:
                raise StrataError("simulated failure")

        lock = GUILock(get_default_config().gui)
        bundle = EnvironmentBundle(
            gui=_FailingGUI(),
            terminal=MockTerminal(),
            filesystem=MockFileSystem(),
            app_manager=MockAppManager(),
            system=MockSystem(),
        )
        executor = PrimitiveTaskExecutor(bundle=bundle, gui_lock=lock)
        node = TaskNode(
            id="p", task_type="primitive", action="click", params={"x": 10.0, "y": 20.0}
        )
        result = executor.execute(node, {})
        assert result.success is False
        assert not lock.locked()


# ── Validator integration ──


class TestValidator:
    def test_invalid_coordinate_returns_failed(self) -> None:
        from strata.grounding.validator import ActionValidator

        bundle, gui, _, _, _, _ = make_bundle()
        validator = ActionValidator(gui)
        executor = PrimitiveTaskExecutor(bundle=bundle, action_validator=validator)
        node = TaskNode(
            id="p",
            task_type="primitive",
            action="click",
            params={"x": 99999.0, "y": 99999.0},
        )
        result = executor.execute(node, {})
        assert result.success is False
        assert "InvalidCoordinateError" in (result.error or "")

    def test_valid_coordinate_accepted(self) -> None:
        from strata.grounding.validator import ActionValidator

        bundle, gui, _, _, _, _ = make_bundle()
        validator = ActionValidator(gui)
        executor = PrimitiveTaskExecutor(bundle=bundle, action_validator=validator)
        node = TaskNode(
            id="p",
            task_type="primitive",
            action="click",
            params={"x": 10.0, "y": 20.0},
        )
        result = executor.execute(node, {})
        assert result.success is True
