"""Thread-safe debug controller — event queue, snapshots, step mode, breakpoints,
prompt interception, LLM transcript history.
"""

from __future__ import annotations

import base64
import itertools
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import icontract

from strata.core.config import DebugConfig
from strata.core.errors import DebugRollbackError
from strata.core.types import TaskGraph, TaskNode, TaskState

if TYPE_CHECKING:
    from strata.llm.provider import ChatMessage

DebugState = Literal["INACTIVE", "OBSERVING", "PAUSED", "EDITING_PROMPT"]

LLMRecordStatus = Literal["pending", "done", "error"]


@dataclass(frozen=True)
class DebugEvent:
    """One lifecycle notification pushed to WebSocket consumers."""

    event: str
    global_state: str
    task_states: Mapping[str, str]
    timestamp: float
    task_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class LLMRecord:
    """One LLM request/response round-trip captured for the transcript viewer."""

    seq: int
    role: str
    started_at: float
    duration_ms: float
    status: LLMRecordStatus
    request_messages: Sequence[Mapping[str, object]] = field(default_factory=list)
    response_text: str = ""
    error_type: str = ""
    error_msg: str = ""


class DebugController:
    """Coordinates debug UI state, event fan-out, and single-step execution.

    # CONVENTION: Orchestrator 主线程调用 ``await_step``；HTTP 线程调用
    # ``continue_execution`` —使用 ``threading.Event`` 跨线程握手。
    """

    def __init__(
        self,
        config: DebugConfig,
        interrupt_check: Callable[[], bool] | None = None,
        history_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._interrupt_check = interrupt_check
        self._history_dir = history_dir
        self._lock = threading.Lock()
        self._debug_state: DebugState = "OBSERVING" if config.enabled else "INACTIVE"
        self._last_global_state: str = "INIT"
        self._last_task_states: dict[str, str] = {}
        self._event_queue: deque[DebugEvent] = deque()
        self._step_mode: bool = False
        self._breakpoints: set[str] = set()
        self._proceed = threading.Event()
        self._proceed.set()
        self._pending_prompt: dict[str, object] | None = None
        self._prompt_approved = threading.Event()
        self._prompt_approved.set()
        self._edited_messages: Sequence[ChatMessage] | None = None
        self._llm_history: dict[int, LLMRecord] = {}
        self._llm_seq: itertools.count[int] = itertools.count(1)
        self._llm_pending: dict[str, int] = {}  # role -> most-recent pending seq
        self._replan_goal: str | None = None

    @property
    def debug_state(self) -> DebugState:
        with self._lock:
            return self._debug_state

    def get_state_snapshot(self) -> dict[str, object]:
        """Return a JSON-friendly snapshot for ``GET /api/state``."""
        with self._lock:
            return {
                "debug_state": self._debug_state,
                "global_state": self._last_global_state,
                "task_states": dict(self._last_task_states),
                "step_mode": self._step_mode,
                "breakpoints": sorted(self._breakpoints),
                "debug_enabled": self._config.enabled,
                "intercept_prompts": self._config.intercept_prompts,
            }

    def notify(
        self,
        event: str,
        global_state: str,
        task_states: Mapping[str, str],
        task_id: str = "",
    ) -> None:
        """Record orchestrator transition and enqueue for WebSocket clients."""
        ts = time.time()
        entry = DebugEvent(
            event=event,
            global_state=global_state,
            task_states=dict(task_states),
            timestamp=ts,
            task_id=task_id,
        )
        with self._lock:
            self._last_global_state = global_state
            self._last_task_states = dict(task_states)
            self._event_queue.append(entry)

    def drain_events(self) -> Sequence[DebugEvent]:
        """Atomically remove and return all queued events (polling / WS tick)."""
        with self._lock:
            out = tuple(self._event_queue)
            self._event_queue.clear()
            return out

    def enable_step_mode(self) -> None:
        """Pause before each task until ``continue_execution``."""
        with self._lock:
            if self._debug_state == "INACTIVE":
                return
            self._step_mode = True

    def disable_step_mode(self) -> None:
        """Stop pausing every task; unblock any waiters."""
        with self._lock:
            self._step_mode = False
        self._proceed.set()

    def step_once(self) -> None:
        """Enable step mode and release one await_step barrier atomically."""
        with self._lock:
            if self._debug_state == "INACTIVE":
                return
            self._step_mode = True
            self._proceed.set()

    def await_step(self, task_id: str) -> None:
        """Block until continue if step mode or *task_id* hits a breakpoint."""
        need_pause = False
        with self._lock:
            if self._debug_state == "INACTIVE":
                return
            need_pause = self._step_mode or task_id in self._breakpoints
            if need_pause:
                self._debug_state = "PAUSED"
                self._proceed.clear()
        if not need_pause:
            return
        while True:
            if self._proceed.wait(timeout=0.05):
                break
            if self._interrupt_check is not None and self._interrupt_check():
                with self._lock:
                    self._debug_state = "OBSERVING"
                self._proceed.set()
                return

        with self._lock:
            if self._debug_state == "PAUSED":
                self._debug_state = "OBSERVING"

    def continue_execution(self) -> None:
        """Release one ``await_step`` barrier (idempotent set)."""
        self._proceed.set()

    @icontract.require(
        lambda self, task_id: len(task_id.strip()) > 0,
        "task_id must be non-empty",
    )
    def add_breakpoint(self, task_id: str) -> None:
        """Register *task_id*; idempotent if already present."""
        with self._lock:
            self._breakpoints.add(task_id)

    def remove_breakpoint(self, task_id: str) -> None:
        """Remove *task_id*; idempotent if absent."""
        with self._lock:
            self._breakpoints.discard(task_id)

    def list_breakpoints(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._breakpoints)

    # ── LLM transcript history ──

    def log_event(self, event: str, task_id: str = "", detail: str = "") -> None:
        """Append an event without mutating last_global_state/task_states."""
        with self._lock:
            if self._debug_state == "INACTIVE":
                return
            self._event_queue.append(
                DebugEvent(
                    event=event,
                    global_state=self._last_global_state,
                    task_states=dict(self._last_task_states),
                    timestamp=time.time(),
                    task_id=task_id,
                    detail=detail,
                )
            )

    def record_llm_done(
        self,
        role: str,
        duration_ms: float,
        messages: Sequence[ChatMessage],
        response: object,
    ) -> None:
        """Patch the pending LLMRecord with response data and emit llm_done."""
        resp_text = ""
        if hasattr(response, "content"):
            resp_text = str(getattr(response, "content", ""))
        elif isinstance(response, str):
            resp_text = response
        with self._lock:
            seq = self._llm_pending.pop(role, 0)
            if seq:
                rec = LLMRecord(
                    seq=seq,
                    role=role,
                    started_at=time.time() - duration_ms / 1000,
                    duration_ms=duration_ms,
                    status="done",
                    request_messages=self._serialize_messages(messages, include_images=True),
                    response_text=resp_text[:10000],
                )
                self._llm_history[seq] = rec
                self._persist_record(rec)
        if seq:
            self.log_event(
                "llm_done",
                task_id=role,
                detail=f"seq={seq} | {duration_ms:.0f}ms | {len(resp_text)} chars",
            )

    def record_llm_error(
        self,
        role: str,
        duration_ms: float,
        messages: Sequence[ChatMessage],
        error: BaseException,
    ) -> None:
        """Patch the pending LLMRecord with error info and emit llm_error."""
        with self._lock:
            seq = self._llm_pending.pop(role, 0)
            if seq:
                rec = LLMRecord(
                    seq=seq,
                    role=role,
                    started_at=time.time() - duration_ms / 1000,
                    duration_ms=duration_ms,
                    status="error",
                    request_messages=self._serialize_messages(messages),
                    error_type=type(error).__name__,
                    error_msg=str(error)[:2000],
                )
                self._llm_history[seq] = rec
                self._persist_record(rec)
        if seq:
            self.log_event(
                "llm_error",
                task_id=role,
                detail=f"seq={seq} | {duration_ms:.0f}ms | {type(error).__name__}: {error!s:.100}",
            )

    def get_llm_history(self) -> Sequence[LLMRecord]:
        """Return all captured LLM records (newest last)."""
        with self._lock:
            return list(self._llm_history.values())

    def get_llm_record(self, seq: int) -> LLMRecord | None:
        """Return a specific LLM record by sequence number.

        Falls back to disk if evicted from memory.
        """
        with self._lock:
            rec = self._llm_history.get(seq)
        if rec is not None:
            return rec
        return self._load_record_from_disk(seq)

    @staticmethod
    def _serialize_messages(
        messages: Sequence[ChatMessage],
        include_images: bool = False,
        max_image_bytes: int = 200_000,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for m in messages:
            entry: dict[str, object] = {
                "role": m.role,
                "content": m.content,
                "has_images": bool(m.images),
            }
            if include_images and m.images:
                images: list[str] = []
                for img_bytes in m.images:
                    encoded = base64.b64encode(img_bytes).decode("ascii")
                    if len(encoded) > max_image_bytes:
                        images.append("image_too_large")
                    else:
                        images.append(encoded)
                entry["images"] = images
            result.append(entry)
        return result

    def _persist_record(self, record: LLMRecord) -> None:
        """Write a completed LLM record to history_dir/{seq}.json."""
        if self._history_dir is None:
            return
        self._history_dir.mkdir(parents=True, exist_ok=True)
        path = self._history_dir / f"{record.seq}.json"
        data = {
            "seq": record.seq,
            "role": record.role,
            "started_at": record.started_at,
            "duration_ms": record.duration_ms,
            "status": record.status,
            "request_messages": [dict(m) for m in record.request_messages],
            "response_text": record.response_text,
            "error_type": record.error_type,
            "error_msg": record.error_msg,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.rename(path)

    def _load_record_from_disk(self, seq: int) -> LLMRecord | None:
        """Try to load an evicted LLM record from disk."""
        if self._history_dir is None:
            return None
        path = self._history_dir / f"{seq}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return LLMRecord(
                seq=data["seq"],
                role=data["role"],
                started_at=data["started_at"],
                duration_ms=data["duration_ms"],
                status=data["status"],
                request_messages=data.get("request_messages", []),
                response_text=data.get("response_text", ""),
                error_type=data.get("error_type", ""),
                error_msg=data.get("error_msg", ""),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    # ── prompt interception ──

    @icontract.require(
        lambda messages: len(messages) > 0,
        "messages must be non-empty",
    )
    def gate(
        self,
        role: str,
        messages: Sequence[ChatMessage],
    ) -> Sequence[ChatMessage]:
        """Block until prompt approved/edited; return (possibly modified) messages.

        Always emits an ``llm_call`` event for visibility regardless of
        ``intercept_prompts``. Only blocks when interception is active.
        """
        with self._lock:
            if self._debug_state == "INACTIVE":
                return messages
            last_content: str = messages[-1].content if messages else ""
            preview = last_content[:200].replace("\n", " ")
            if len(last_content) > 200:
                preview += "…"
            seq = next(self._llm_seq)
            self._llm_pending[role] = seq
            self._llm_history[seq] = LLMRecord(
                seq=seq,
                role=role,
                started_at=time.time(),
                duration_ms=0.0,
                status="pending",
                request_messages=self._serialize_messages(messages),
            )
            if len(self._llm_history) > 50:
                self._llm_history.pop(next(iter(self._llm_history)))
            self._event_queue.append(
                DebugEvent(
                    event="llm_call",
                    global_state=self._last_global_state,
                    task_states=dict(self._last_task_states),
                    timestamp=time.time(),
                    task_id=role,
                    detail=f"seq={seq} | {len(messages)} msgs | {preview}",
                )
            )
            if not self._config.intercept_prompts:
                return messages
            self._pending_prompt = {
                "role": role,
                "messages": [
                    {"role": m.role, "content": m.content, "has_images": bool(m.images)}
                    for m in messages
                ],
            }
            self._prompt_approved = threading.Event()
            self._edited_messages = None
            prev_state = self._debug_state
            self._debug_state = "EDITING_PROMPT"
            self._event_queue.append(
                DebugEvent(
                    event="prompt_pending",
                    global_state=self._last_global_state,
                    task_states=dict(self._last_task_states),
                    timestamp=time.time(),
                )
            )

        while True:
            if self._prompt_approved.wait(timeout=0.05):
                break
            if self._interrupt_check is not None and self._interrupt_check():
                with self._lock:
                    self._debug_state = prev_state
                    self._pending_prompt = None
                return messages

        with self._lock:
            result = self._edited_messages if self._edited_messages is not None else messages
            self._debug_state = prev_state
            self._pending_prompt = None
            return result

    def approve_prompt(
        self,
        edited_messages: Sequence[ChatMessage] | None = None,
    ) -> None:
        """Release the prompt gate, optionally with edited messages."""
        with self._lock:
            if edited_messages is not None:
                self._edited_messages = list(edited_messages)
            else:
                self._edited_messages = None
        if hasattr(self, "_prompt_approved"):
            self._prompt_approved.set()

    def skip_interception(self) -> None:
        """Disable prompt interception for the rest of this run."""
        with self._lock:
            self._config = replace(self._config, intercept_prompts=False)
        if hasattr(self, "_prompt_approved"):
            self._prompt_approved.set()

    def enable_interception(self) -> None:
        """Enable prompt interception so the next LLM call will be held for approval."""
        with self._lock:
            self._config = replace(self._config, intercept_prompts=True)

    def get_pending_prompt(self) -> dict[str, object] | None:
        """Return the pending prompt payload for ``GET /api/prompt/pending``."""
        with self._lock:
            if self._pending_prompt is not None:
                return dict(self._pending_prompt)
            return None

    # ── task editing ──

    @icontract.require(
        lambda self: self._debug_state in ("PAUSED", "OBSERVING"),
        "edit_task requires debug to be active and not in prompt editing",
    )
    @icontract.require(
        lambda task_id: len(task_id.strip()) > 0,
        "task_id must be non-empty",
    )
    def edit_task(
        self,
        task_id: str,
        task_states: Mapping[str, TaskState],
        graph: TaskGraph,
        params: Mapping[str, object] | None = None,
        action: str | None = None,
    ) -> TaskGraph:
        """Return a new TaskGraph with the specified task's params/action replaced.

        Raises DebugRollbackError if the task is not in PENDING state.
        """
        state = task_states.get(task_id)
        if state is None:
            raise DebugRollbackError(f"task {task_id!r} not found in task_states")
        if state != "PENDING":
            raise DebugRollbackError(f"task {task_id!r} is {state}, not PENDING — rollback first")
        new_tasks: list[TaskNode] = []
        found = False
        for t in graph.tasks:
            if t.id == task_id:
                found = True
                new_params = dict(params) if params is not None else dict(t.params)
                new_action = action if action is not None else t.action
                new_tasks.append(replace(t, params=new_params, action=new_action))
            else:
                new_tasks.append(t)
        if not found:
            raise DebugRollbackError(f"task {task_id!r} not found in graph")
        return replace(graph, tasks=tuple(new_tasks))

    # ── replan ──

    @icontract.require(
        lambda self: self._debug_state == "PAUSED",
        "replan requires PAUSED state",
    )
    @icontract.require(
        lambda new_goal: len(new_goal.strip()) > 0,
        "new_goal must be non-empty",
    )
    def request_replan(self, new_goal: str) -> None:
        """Signal the orchestrator to re-plan with a new goal."""
        with self._lock:
            self._replan_goal = new_goal
        self._proceed.set()

    def consume_replan(self) -> str | None:
        """Return and clear the pending replan goal, if any."""
        with self._lock:
            goal = self._replan_goal
            self._replan_goal = None
            return goal


@runtime_checkable
class PromptInterceptor(Protocol):
    """Protocol for intercepting LLM calls in the router."""

    def gate(
        self,
        role: str,
        messages: Sequence[ChatMessage],
    ) -> Sequence[ChatMessage]: ...

    def record_llm_done(
        self,
        role: str,
        duration_ms: float,
        messages: Sequence[ChatMessage],
        response: object,
    ) -> None: ...

    def record_llm_error(
        self,
        role: str,
        duration_ms: float,
        messages: Sequence[ChatMessage],
        error: BaseException,
    ) -> None: ...
