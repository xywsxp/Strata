"""Thread-safe debug controller — event queue, snapshots, step mode, breakpoints,
prompt interception, LLM transcript history.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import icontract

from strata.core.config import DebugConfig

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
    ) -> None:
        self._config = config
        self._interrupt_check = interrupt_check
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
                self._llm_history[seq] = LLMRecord(
                    seq=seq,
                    role=role,
                    started_at=time.time() - duration_ms / 1000,
                    duration_ms=duration_ms,
                    status="done",
                    request_messages=self._serialize_messages(messages),
                    response_text=resp_text[:10000],
                )
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
                self._llm_history[seq] = LLMRecord(
                    seq=seq,
                    role=role,
                    started_at=time.time() - duration_ms / 1000,
                    duration_ms=duration_ms,
                    status="error",
                    request_messages=self._serialize_messages(messages),
                    error_type=type(error).__name__,
                    error_msg=str(error)[:2000],
                )
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
        """Return a specific LLM record by sequence number."""
        with self._lock:
            return self._llm_history.get(seq)

    @staticmethod
    def _serialize_messages(
        messages: Sequence[ChatMessage],
    ) -> list[dict[str, object]]:
        return [
            {
                "role": m.role,
                "content": m.content,
                "has_images": bool(m.images),
            }
            for m in messages
        ]

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
