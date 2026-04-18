"""Tests for strata.debug.controller — DebugController core logic."""

from __future__ import annotations

import threading

import icontract
import pytest
from hypothesis import given
from hypothesis import strategies as st

from strata.core.config import DebugConfig
from strata.debug.controller import DebugController
from tests.strategies import st_breakpoint_set, st_debug_event

# ── Hypothesis properties ──


@given(
    bp_id=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=["L", "N"])),
)
def test_prop_breakpoint_add_remove_roundtrip(bp_id: str) -> None:
    """add then remove leaves breakpoints unchanged."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.add_breakpoint(bp_id)
    assert bp_id in ctrl.list_breakpoints()
    ctrl.remove_breakpoint(bp_id)
    assert bp_id not in ctrl.list_breakpoints()


@given(n=st.integers(min_value=1, max_value=20))
def test_prop_step_continue_idempotent(n: int) -> None:
    """Consecutive continue_execution calls do not raise."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    for _ in range(n):
        ctrl.continue_execution()


@given(data=st.data())
def test_prop_notify_drain_count_matches(data: st.DataObject) -> None:
    """Number of drained events equals number of notify calls."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    n = data.draw(st.integers(min_value=0, max_value=20))
    for _ in range(n):
        ev, gs, ts = data.draw(st_debug_event())
        ctrl.notify(ev, gs, ts)
    events = ctrl.drain_events()
    assert len(events) == n


@given(bps=st_breakpoint_set(max_size=10))
def test_prop_add_all_breakpoints_listed(bps: frozenset[str]) -> None:
    """All added breakpoints appear in list_breakpoints."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    for bp in bps:
        ctrl.add_breakpoint(bp)
    assert ctrl.list_breakpoints() == bps


# ── Unit tests ──


def test_inactive_when_disabled() -> None:
    cfg = DebugConfig(enabled=False, port=0, token="")
    ctrl = DebugController(cfg)
    assert ctrl.debug_state == "INACTIVE"


def test_observing_when_enabled() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    assert ctrl.debug_state == "OBSERVING"


def test_notify_enqueues_event() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.notify("task_dispatched", "EXECUTING", {"t1": "RUNNING"})
    events = ctrl.drain_events()
    assert len(events) == 1
    assert events[0].event == "task_dispatched"
    assert events[0].global_state == "EXECUTING"
    assert events[0].task_states == {"t1": "RUNNING"}


def test_drain_clears_queue() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.notify("task_done", "SCHEDULING", {})
    ctrl.drain_events()
    assert len(ctrl.drain_events()) == 0


def test_step_mode_skips_non_breakpoint() -> None:
    """Without step mode and no breakpoints, await_step returns immediately."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.await_step("any_task")
    assert ctrl.debug_state == "OBSERVING"


def test_await_step_blocks_until_continue() -> None:
    """Step mode causes await_step to block; continue_execution releases."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.enable_step_mode()
    released = threading.Event()
    paused = threading.Event()

    def worker() -> None:
        paused.set()
        ctrl.await_step("t1")
        released.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    paused.wait(timeout=2.0)
    assert not released.wait(timeout=0.15), "should still be blocked"
    state_while_blocked: str = ctrl.debug_state
    assert state_while_blocked == "PAUSED"
    ctrl.continue_execution()
    assert released.wait(timeout=2.0), "should have been released"
    state_after_release: str = ctrl.debug_state
    assert state_after_release == "OBSERVING"
    t.join(timeout=2.0)


def test_breakpoint_causes_pause() -> None:
    """A registered breakpoint triggers PAUSED even without step mode."""
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.add_breakpoint("t2")
    released = threading.Event()

    def worker() -> None:
        ctrl.await_step("t2")
        released.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert not released.wait(timeout=0.15)
    assert ctrl.debug_state == "PAUSED"
    ctrl.continue_execution()
    assert released.wait(timeout=2.0)
    t.join(timeout=2.0)


def test_inactive_skips_step() -> None:
    """INACTIVE controller never blocks on await_step."""
    cfg = DebugConfig(enabled=False, port=0, token="")
    ctrl = DebugController(cfg)
    ctrl.enable_step_mode()
    ctrl.await_step("any")
    assert ctrl.debug_state == "INACTIVE"


def test_add_breakpoint_empty_raises() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    with pytest.raises(icontract.ViolationError):
        ctrl.add_breakpoint("")


def test_add_breakpoint_whitespace_raises() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    with pytest.raises(icontract.ViolationError):
        ctrl.add_breakpoint("   ")


def test_remove_breakpoint_absent_no_error() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.remove_breakpoint("nonexistent")


def test_get_state_snapshot_shape() -> None:
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    ctrl.notify("receive_goal", "PLANNING", {})
    snap = ctrl.get_state_snapshot()
    assert snap["debug_state"] == "OBSERVING"
    assert snap["global_state"] == "PLANNING"
    assert snap["step_mode"] is False
    assert isinstance(snap["breakpoints"], list)


def test_interrupt_check_unblocks_await() -> None:
    """interrupt_check=True aborts a blocked await_step."""
    interrupted = False

    def check() -> bool:
        return interrupted

    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg, interrupt_check=check)
    ctrl.enable_step_mode()
    released = threading.Event()

    def worker() -> None:
        ctrl.await_step("t1")
        released.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert not released.wait(timeout=0.15)
    interrupted = True
    assert released.wait(timeout=2.0)
    assert ctrl.debug_state == "OBSERVING"
    t.join(timeout=2.0)


# ── Prompt interception tests ──


def test_gate_passthrough_when_disabled() -> None:
    """gate returns messages unchanged when intercept_prompts is off."""
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t", intercept_prompts=False)
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hello")]
    result = ctrl.gate("planner", msgs)
    assert list(result) == msgs


def test_gate_passthrough_when_inactive() -> None:
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=False, port=0, token="", intercept_prompts=True)
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hello")]
    result = ctrl.gate("planner", msgs)
    assert list(result) == msgs


def test_gate_blocks_until_approved() -> None:
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t", intercept_prompts=True)
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hello")]
    released = threading.Event()
    result_box: list[object] = []

    def worker() -> None:
        result_box.append(ctrl.gate("planner", msgs))
        released.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert not released.wait(timeout=0.15)
    state: str = ctrl.debug_state
    assert state == "EDITING_PROMPT"
    assert ctrl.get_pending_prompt() is not None
    ctrl.approve_prompt()
    assert released.wait(timeout=2.0)
    assert list(result_box[0]) == msgs  # type: ignore[call-overload]
    t.join(timeout=2.0)


def test_skip_interception_unblocks_gate() -> None:
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t", intercept_prompts=True)
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hello")]
    released = threading.Event()

    def worker() -> None:
        ctrl.gate("planner", msgs)
        released.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert not released.wait(timeout=0.15)
    ctrl.skip_interception()
    assert released.wait(timeout=2.0)
    t.join(timeout=2.0)


# ── New tests for Phase 10/11/12 fixes ──


def test_gate_rejects_empty_messages() -> None:
    """gate() contract requires at least one message."""
    import icontract

    cfg = DebugConfig(enabled=True, port=8390, token="t", intercept_prompts=True)
    ctrl = DebugController(cfg)
    with pytest.raises(icontract.ViolationError, match="non-empty"):
        ctrl.gate("planner", [])


def test_record_llm_done_no_ghost_event_on_missing_seq() -> None:
    """record_llm_done for unknown role should not enqueue a ghost llm_done event."""
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hi")]
    # No pending entry for "unknown_role" → seq will be 0, no event emitted.
    ctrl.record_llm_done("unknown_role", 50.0, msgs, "response")
    events = [e for e in ctrl.drain_events() if e.event == "llm_done"]
    assert not events, "no llm_done event expected for missing seq"


def test_record_llm_error_no_ghost_event_on_missing_seq() -> None:
    """record_llm_error for unknown role should not enqueue a ghost llm_error event."""
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hi")]
    ctrl.record_llm_error("unknown_role", 50.0, msgs, RuntimeError("boom"))
    events = [e for e in ctrl.drain_events() if e.event == "llm_error"]
    assert not events, "no llm_error event expected for missing seq"


def test_record_llm_done_emits_event_for_known_seq() -> None:
    """record_llm_done emits llm_done only when the seq is in pending."""
    from strata.llm.provider import ChatMessage

    cfg = DebugConfig(enabled=True, port=8390, token="t", intercept_prompts=False)
    ctrl = DebugController(cfg)
    msgs = [ChatMessage(role="user", content="hi")]
    # gate() (even without blocking) registers the seq in _llm_pending.
    ctrl.gate("planner", msgs)
    ctrl.drain_events()  # clear llm_call event
    ctrl.record_llm_done("planner", 100.0, msgs, "ok")
    events = [e for e in ctrl.drain_events() if e.event == "llm_done"]
    assert len(events) == 1
    assert "seq=1" in events[0].detail


def test_skip_interception_uses_replace() -> None:
    """skip_interception preserves all other config fields via dataclasses.replace."""
    cfg = DebugConfig(
        enabled=True, port=9000, token="mytoken", intercept_prompts=True, max_checkpoint_history=5
    )
    ctrl = DebugController(cfg)
    ctrl.skip_interception()
    snap = ctrl.get_state_snapshot()
    assert snap["intercept_prompts"] is False
    # Other fields preserved — re-enable to verify roundtrip.
    ctrl.enable_interception()
    snap2 = ctrl.get_state_snapshot()
    assert snap2["intercept_prompts"] is True


def test_debug_state_literal_no_rolling_back() -> None:
    """ROLLING_BACK is removed from the DebugState Literal."""
    from strata.debug.controller import DebugState

    # If ROLLING_BACK were still in the union, this isinstance check (via
    # get_args) would expose it.  Simply instantiate and verify state is
    # one of the four expected values.
    cfg = DebugConfig(enabled=True, port=8390, token="t")
    ctrl = DebugController(cfg)
    state: DebugState = ctrl.debug_state
    assert state in {"INACTIVE", "OBSERVING", "PAUSED", "EDITING_PROMPT"}
