"""Thread-safe debug controller — event queue, snapshots, step mode, breakpoints,
prompt interception.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import icontract

from strata.core.config import DebugConfig

if TYPE_CHECKING:
    from strata.llm.provider import ChatMessage

DebugState = Literal["INACTIVE", "OBSERVING", "PAUSED", "EDITING_PROMPT", "ROLLING_BACK"]


@dataclass(frozen=True)
class DebugEvent:
    """One lifecycle notification pushed to WebSocket consumers."""

    event: str
    global_state: str
    task_states: Mapping[str, str]
    timestamp: float


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
            }

    def notify(self, event: str, global_state: str, task_states: Mapping[str, str]) -> None:
        """Record orchestrator transition and enqueue for WebSocket clients."""
        ts = time.time()
        entry = DebugEvent(
            event=event,
            global_state=global_state,
            task_states=dict(task_states),
            timestamp=ts,
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

    # ── prompt interception ──

    def gate(
        self,
        role: str,
        messages: Sequence[ChatMessage],
    ) -> Sequence[ChatMessage]:
        """Block until prompt approved/edited; return (possibly modified) messages.

        When ``intercept_prompts`` is off or state is INACTIVE, passes through.
        """
        with self._lock:
            if self._debug_state == "INACTIVE" or not self._config.intercept_prompts:
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
            self._config = DebugConfig(
                enabled=self._config.enabled,
                port=self._config.port,
                token=self._config.token,
                intercept_prompts=False,
                max_checkpoint_history=self._config.max_checkpoint_history,
            )
        if hasattr(self, "_prompt_approved"):
            self._prompt_approved.set()

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
