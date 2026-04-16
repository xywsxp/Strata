"""Tests for strata.harness.gui_lock — GUILock + AtomicGUITransaction."""

from __future__ import annotations

import pytest

from strata.core.config import GUIConfig
from strata.core.errors import GUILockTimeoutError
from strata.core.types import ActionResult
from strata.harness.gui_lock import AtomicGUITransaction, GUILock

_GUI_CFG = GUIConfig(
    lock_timeout=2.0,
    wait_interval=0.05,
    screenshot_without_lock=False,
    enable_scroll_search=True,
    max_scroll_attempts=10,
    scroll_step_pixels=300,
)


class TestLockAcquireRelease:
    def test_basic_acquire_release(self) -> None:
        lock = GUILock(_GUI_CFG)
        assert lock.acquire(timeout=1.0)
        lock.release()

    def test_context_manager(self) -> None:
        lock = GUILock(_GUI_CFG)
        with lock:
            pass


class TestAtomicTransaction:
    def test_wait_then_act(self) -> None:
        lock = GUILock(_GUI_CFG)
        call_count = 0

        def check() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count >= 3

        def act() -> ActionResult:
            return ActionResult(success=True)

        txn = AtomicGUITransaction(lock, _GUI_CFG)
        result = txn.wait_and_act(check, act, max_wait=5.0)
        assert result.success
        assert call_count >= 3

    def test_timeout(self) -> None:
        lock = GUILock(_GUI_CFG)
        txn = AtomicGUITransaction(lock, _GUI_CFG)
        with pytest.raises(GUILockTimeoutError):
            txn.wait_and_act(lambda: False, lambda: ActionResult(success=True), max_wait=0.2)
