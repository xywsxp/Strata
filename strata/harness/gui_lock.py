"""GUI global mutex lock + atomic transaction."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from strata.core.config import GUIConfig
from strata.core.errors import GUILockTimeoutError
from strata.core.types import ActionResult


class GUILock:
    """Reentrant GUI mutex with configurable timeout."""

    def __init__(self, config: GUIConfig) -> None:
        self._lock = threading.RLock()
        self._timeout = config.lock_timeout

    def acquire(self, timeout: float | None = None) -> bool:
        t = timeout if timeout is not None else self._timeout
        return self._lock.acquire(timeout=t)

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        acquired = self._lock.acquire(blocking=False)
        if acquired:
            self._lock.release()
            return False
        return True

    def __enter__(self) -> GUILock:
        if not self.acquire():
            raise GUILockTimeoutError("failed to acquire GUI lock")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()


class AtomicGUITransaction:
    """Atomic wait→check→act transaction under GUI lock.

    Atomicity boundary: check_fn and act_fn both execute under lock protection.
    When check fails, auxiliary_fn (if any) runs under lock before releasing.
    When check succeeds, act_fn runs without releasing — lock released in finally.
    """

    def __init__(self, lock: GUILock, config: GUIConfig) -> None:
        self._lock = lock
        self._interval = config.wait_interval

    def wait_and_act(
        self,
        check_fn: Callable[[], bool],
        act_fn: Callable[[], ActionResult],
        max_wait: float = 30.0,
        auxiliary_fn: Callable[[], None] | None = None,
    ) -> ActionResult:
        """Poll check_fn under lock; once True, run act_fn atomically."""
        start = time.monotonic()
        while True:
            if not self._lock.acquire(timeout=max_wait):
                raise GUILockTimeoutError("timeout acquiring lock for transaction")
            try:
                if check_fn():
                    return act_fn()
                if auxiliary_fn is not None:
                    auxiliary_fn()
            finally:
                self._lock.release()

            elapsed = time.monotonic() - start
            if elapsed >= max_wait:
                raise GUILockTimeoutError(f"wait_and_act timed out after {elapsed:.1f}s")
            time.sleep(self._interval)
