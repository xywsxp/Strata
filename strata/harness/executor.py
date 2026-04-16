"""PrimitiveTaskExecutor — production implementation of the TaskExecutor Protocol.

Dispatches a :class:`TaskNode.action` into one of three lanes:

* **env** — filesystem / app_manager / system / GUI adapter calls via the
  ``EnvironmentBundle``.
* **grounding** — ``locate_and_click`` (VisionLocator) and ``execute_command``
  (TerminalHandler). When the respective dependency is not injected, the
  dispatcher returns ``ActionResult(success=False, error=...)`` rather than
  raising — so the Orchestrator can drive recovery on the ``success`` channel.
* **GUI** — every GUI action is wrapped in ``GUILock`` (when provided) so
  concurrent execution stays serialized. Coordinate-bearing actions are first
  validated by ``ActionValidator`` (when provided).

All exceptions raised by the underlying adapters (``StrataError`` subclasses)
are captured and packed into a failed :class:`ActionResult` rather than
escaping the executor. This is deliberate — the Orchestrator drives recovery
off the ``success`` field; letting exceptions escape would require a parallel
``try/except`` ladder in the Orchestrator.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from typing import Final, cast

import icontract

from strata import StrataError
from strata.core.errors import (
    ActionParamsError,
    OrchestrationError,
    UnknownActionError,
)
from strata.core.types import ActionResult, Coordinate, TaskNode
from strata.env.protocols import EnvironmentBundle
from strata.grounding.terminal_handler import TerminalHandler
from strata.grounding.validator import ActionValidator
from strata.grounding.vision_locator import VisionLocator
from strata.harness.actions import ACTION_PARAM_SCHEMA, ACTION_VOCABULARY
from strata.harness.context import AuditLogger
from strata.harness.gui_lock import GUILock

_GUI_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "click",
        "double_click",
        "move_mouse",
        "type_text",
        "press_key",
        "hotkey",
        "scroll",
        "screenshot",
        "locate_and_click",
    }
)

_COORDINATE_ACTIONS: Final[frozenset[str]] = frozenset({"click", "double_click", "move_mouse"})


class PrimitiveTaskExecutor:
    """Structural implementation of :class:`strata.harness.scheduler.TaskExecutor`.

    Injected grounding/lock/validator dependencies are **optional** so callers
    can construct an env-only executor for tests. Actions whose required
    dependency is missing degrade to ``ActionResult(success=False)`` with an
    explanatory ``error`` string — they do **not** raise.
    """

    def __init__(
        self,
        bundle: EnvironmentBundle,
        vision_locator: VisionLocator | None = None,
        terminal_handler: TerminalHandler | None = None,
        gui_lock: GUILock | None = None,
        action_validator: ActionValidator | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._bundle = bundle
        self._vision_locator = vision_locator
        self._terminal_handler = terminal_handler
        self._gui_lock = gui_lock
        self._action_validator = action_validator
        self._audit_logger = audit_logger

    @icontract.require(
        lambda task: task.task_type == "primitive",
        "executor only handles primitive tasks",
        error=lambda task: OrchestrationError(
            f"PrimitiveTaskExecutor received non-primitive task_type={task.task_type!r}"
        ),
    )
    @icontract.require(
        lambda task: task.action is not None and task.action in ACTION_VOCABULARY,
        "action must be in ACTION_VOCABULARY",
        error=lambda task: UnknownActionError(
            f"task {task.id!r} action={task.action!r} not in ACTION_VOCABULARY"
        ),
    )
    @icontract.ensure(
        lambda result: isinstance(result, ActionResult),
        "execute must return ActionResult",
    )
    def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
        action = cast(str, task.action)
        self._require_params(task)

        if action in _GUI_ACTIONS:
            result = self._run_gui(action, task)
        else:
            result = self._dispatch_env(action, task)

        self._record_audit(task, result)
        return result

    # ── params validation ──

    def _require_params(self, task: TaskNode) -> None:
        action = cast(str, task.action)
        required = ACTION_PARAM_SCHEMA[action]
        missing = required - set(task.params.keys())
        if missing:
            raise ActionParamsError(
                f"task {task.id!r} action={action!r} missing required params: {sorted(missing)}"
            )

    # ── GUI lane ──

    def _run_gui(self, action: str, task: TaskNode) -> ActionResult:
        fn = self._gui_dispatcher(action, task)
        if self._gui_lock is None:
            return self._call_safely(fn)
        with self._gui_lock:
            return self._call_safely(fn)

    def _gui_dispatcher(self, action: str, task: TaskNode) -> Callable[[], ActionResult]:
        if action == "click":
            return lambda: self._dispatch_click(task)
        if action == "double_click":
            return lambda: self._dispatch_double_click(task)
        if action == "move_mouse":
            return lambda: self._dispatch_move_mouse(task)
        if action == "type_text":
            return lambda: self._dispatch_type_text(task)
        if action == "press_key":
            return lambda: self._dispatch_press_key(task)
        if action == "hotkey":
            return lambda: self._dispatch_hotkey(task)
        if action == "scroll":
            return lambda: self._dispatch_scroll(task)
        if action == "screenshot":
            return lambda: self._dispatch_screenshot(task)
        if action == "locate_and_click":
            return lambda: self._dispatch_locate_and_click(task)
        raise OrchestrationError(f"internal: unhandled GUI action {action!r}")

    def _validate_coord(self, x: float, y: float) -> None:
        if self._action_validator is None:
            return
        self._action_validator.validate_coordinates_in_screen(Coordinate(x=x, y=y))

    def _dispatch_click(self, task: TaskNode) -> ActionResult:
        x = _as_float(task.params["x"], "x")
        y = _as_float(task.params["y"], "y")
        button = str(task.params.get("button", "left"))
        self._validate_coord(x, y)
        self._bundle.gui.click(x, y, button=button)
        return ActionResult(success=True, data={"x": x, "y": y, "button": button})

    def _dispatch_double_click(self, task: TaskNode) -> ActionResult:
        x = _as_float(task.params["x"], "x")
        y = _as_float(task.params["y"], "y")
        self._validate_coord(x, y)
        self._bundle.gui.double_click(x, y)
        return ActionResult(success=True, data={"x": x, "y": y})

    def _dispatch_move_mouse(self, task: TaskNode) -> ActionResult:
        x = _as_float(task.params["x"], "x")
        y = _as_float(task.params["y"], "y")
        self._validate_coord(x, y)
        self._bundle.gui.move_mouse(x, y)
        return ActionResult(success=True, data={"x": x, "y": y})

    def _dispatch_type_text(self, task: TaskNode) -> ActionResult:
        text = _as_str(task.params["text"], "text")
        interval = _as_float(task.params.get("interval", 0.05), "interval")
        self._bundle.gui.type_text(text, interval=interval)
        return ActionResult(success=True, data={"text_len": len(text)})

    def _dispatch_press_key(self, task: TaskNode) -> ActionResult:
        key = _as_str(task.params["key"], "key")
        self._bundle.gui.press_key(key)
        return ActionResult(success=True, data={"key": key})

    def _dispatch_hotkey(self, task: TaskNode) -> ActionResult:
        keys = _as_str_sequence(task.params["keys"], "keys")
        if not keys:
            raise ActionParamsError(
                f"task {task.id!r} action=hotkey requires non-empty 'keys' sequence"
            )
        self._bundle.gui.hotkey(*keys)
        return ActionResult(success=True, data={"keys": list(keys)})

    def _dispatch_scroll(self, task: TaskNode) -> ActionResult:
        dx = _as_int(task.params["delta_x"], "delta_x")
        dy = _as_int(task.params["delta_y"], "delta_y")
        if dx == 0 and dy == 0:
            raise ActionParamsError(
                f"task {task.id!r} action=scroll requires at least one non-zero delta"
            )
        self._bundle.gui.scroll(dx, dy)
        return ActionResult(success=True, data={"delta_x": dx, "delta_y": dy})

    def _dispatch_screenshot(self, task: TaskNode) -> ActionResult:
        image = self._bundle.gui.capture_screen()
        return ActionResult(success=True, data={"size": len(image)})

    def _dispatch_locate_and_click(self, task: TaskNode) -> ActionResult:
        description = _as_str(task.params["description"], "description")
        if not description.strip():
            raise ActionParamsError(
                f"task {task.id!r} action=locate_and_click requires non-empty 'description'"
            )
        if self._vision_locator is None:
            return ActionResult(
                success=False,
                error="locate_and_click unavailable: VisionLocator not injected",
            )
        role_raw = task.params.get("role")
        role = str(role_raw) if role_raw is not None else None
        coord = self._vision_locator.locate_with_scroll(description, role=role)
        self._bundle.gui.click(coord.x, coord.y)
        return ActionResult(success=True, data={"x": coord.x, "y": coord.y})

    # ── env lane ──

    def _dispatch_env(self, action: str, task: TaskNode) -> ActionResult:
        if action == "execute_command":
            return self._call_safely(lambda: self._dispatch_execute_command(task))
        if action == "read_file":
            return self._call_safely(lambda: self._dispatch_read_file(task))
        if action == "write_file":
            return self._call_safely(lambda: self._dispatch_write_file(task))
        if action == "list_directory":
            return self._call_safely(lambda: self._dispatch_list_directory(task))
        if action == "move_to_trash":
            return self._call_safely(lambda: self._dispatch_move_to_trash(task))
        if action == "launch_app":
            return self._call_safely(lambda: self._dispatch_launch_app(task))
        if action == "close_app":
            return self._call_safely(lambda: self._dispatch_close_app(task))
        if action == "get_clipboard":
            return self._call_safely(lambda: self._dispatch_get_clipboard(task))
        if action == "set_clipboard":
            return self._call_safely(lambda: self._dispatch_set_clipboard(task))
        raise OrchestrationError(f"internal: unhandled env action {action!r}")

    def _dispatch_execute_command(self, task: TaskNode) -> ActionResult:
        command = _as_str(task.params["command"], "command")
        if not command.strip():
            raise ActionParamsError(
                f"task {task.id!r} action=execute_command requires non-empty 'command'"
            )
        cwd_raw = task.params.get("cwd")
        cwd = str(cwd_raw) if cwd_raw is not None else None
        if self._terminal_handler is None:
            return ActionResult(
                success=False,
                error="execute_command unavailable: TerminalHandler not injected",
            )
        result = self._terminal_handler.execute_command(command, cwd=cwd)
        ok = result.returncode == 0
        data: Mapping[str, object] = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
        return ActionResult(
            success=ok,
            data=data,
            error=None if ok else f"command returned non-zero exit {result.returncode}",
        )

    def _dispatch_read_file(self, task: TaskNode) -> ActionResult:
        path = _as_str(task.params["path"], "path")
        content = self._bundle.filesystem.read_file(path)
        return ActionResult(success=True, data={"content": content})

    def _dispatch_write_file(self, task: TaskNode) -> ActionResult:
        path = _as_str(task.params["path"], "path")
        content = _as_str(task.params["content"], "content")
        encoding = str(task.params.get("encoding", "utf-8"))
        self._bundle.filesystem.write_file(path, content, encoding=encoding)
        return ActionResult(success=True, data={"path": path, "bytes": len(content)})

    def _dispatch_list_directory(self, task: TaskNode) -> ActionResult:
        path = _as_str(task.params["path"], "path")
        pattern_raw = task.params.get("pattern")
        pattern = str(pattern_raw) if pattern_raw is not None else None
        files = self._bundle.filesystem.list_directory(path, pattern=pattern)
        return ActionResult(success=True, data={"count": len(files)})

    def _dispatch_move_to_trash(self, task: TaskNode) -> ActionResult:
        path = _as_str(task.params["path"], "path")
        trash_path = self._bundle.filesystem.move_to_trash(path)
        return ActionResult(success=True, data={"trash_path": trash_path})

    def _dispatch_launch_app(self, task: TaskNode) -> ActionResult:
        app_name = _as_str(task.params["app_name"], "app_name")
        args_raw = task.params.get("args")
        args: Sequence[str] | None
        if args_raw is None:
            args = None
        elif isinstance(args_raw, (list, tuple)):
            args = tuple(str(a) for a in args_raw)
        else:
            raise ActionParamsError(f"task {task.id!r} action=launch_app 'args' must be a sequence")
        handle = self._bundle.app_manager.launch_app(app_name, args=args)
        return ActionResult(success=True, data={"handle": handle})

    def _dispatch_close_app(self, task: TaskNode) -> ActionResult:
        app_identifier = _as_str(task.params["app_identifier"], "app_identifier")
        self._bundle.app_manager.close_app(app_identifier)
        return ActionResult(success=True, data=None)

    def _dispatch_get_clipboard(self, task: TaskNode) -> ActionResult:
        text = self._bundle.system.get_clipboard_text()
        return ActionResult(success=True, data={"text": text})

    def _dispatch_set_clipboard(self, task: TaskNode) -> ActionResult:
        text = _as_str(task.params["text"], "text")
        self._bundle.system.set_clipboard_text(text)
        return ActionResult(success=True, data=None)

    # ── helpers ──

    def _call_safely(self, fn: Callable[[], ActionResult]) -> ActionResult:
        """Run ``fn`` and convert any :class:`StrataError` into a failed result.

        ``ActionParamsError`` is deliberately re-raised: it represents a
        programming error in the planner output (missing required param), and
        should surface as an exception for the Orchestrator to treat as an
        unrecoverable planning defect. Other ``StrataError`` subclasses (env,
        grounding, LLM) are converted to ``ActionResult(success=False)``.
        """
        try:
            return fn()
        except ActionParamsError:
            raise
        except UnknownActionError:
            raise
        except StrataError as exc:
            return ActionResult(success=False, error=f"{type(exc).__name__}: {exc}")

    def _record_audit(self, task: TaskNode, result: ActionResult) -> None:
        if self._audit_logger is None:
            return
        action = cast(str, task.action)
        payload = "success" if result.success else f"failed: {result.error}"
        # CONVENTION: 审计写盘失败不中断主循环 —— agent 的主目的是完成任务，
        # 审计层的 I/O 故障（磁盘满/权限）不应升格为 goal 失败。
        with contextlib.suppress(OSError):
            self._audit_logger.log(task.id, action, task.params, payload)


# ── coercion helpers ──


def _as_float(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ActionParamsError(f"param {name!r}: bool is not accepted as float")
    if isinstance(value, (int, float)):
        return float(value)
    raise ActionParamsError(f"param {name!r}: expected number, got {type(value).__name__}")


def _as_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ActionParamsError(f"param {name!r}: bool is not accepted as int")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ActionParamsError(f"param {name!r}: expected integer, got {type(value).__name__}")


def _as_str(value: object, name: str) -> str:
    if isinstance(value, str):
        return value
    raise ActionParamsError(f"param {name!r}: expected str, got {type(value).__name__}")


def _as_str_sequence(value: object, name: str) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise ActionParamsError(f"param {name!r}: expected sequence, got {type(value).__name__}")
