"""Tests for strata.harness.state_machine — state transition logic."""

from __future__ import annotations

import pytest

from strata.core.errors import StateTransitionError
from strata.harness.state_machine import (
    create_global_state_machine,
    create_task_state_machine,
)


def _state(sm: object) -> str:
    """Read state as plain str to avoid mypy Literal-narrowing overlap checks."""
    attr = "state"
    return str(getattr(sm, attr))


class TestGlobalHappyPath:
    def test_full_success_path(self) -> None:
        sm = create_global_state_machine()
        assert _state(sm) == "INIT"
        sm.transition("receive_goal")
        assert _state(sm) == "PLANNING"
        sm.transition("plan_ready")
        assert _state(sm) == "CONFIRMING"
        sm.transition("user_confirm")
        assert _state(sm) == "SCHEDULING"
        sm.transition("task_dispatched")
        assert _state(sm) == "EXECUTING"
        sm.transition("task_done")
        assert _state(sm) == "SCHEDULING"
        sm.transition("all_done")
        assert _state(sm) == "COMPLETED"


class TestInvalidTransition:
    def test_init_task_dispatched_raises(self) -> None:
        sm = create_global_state_machine()
        with pytest.raises(StateTransitionError):
            sm.transition("task_dispatched")


class TestRecoveryPath:
    def test_executing_fail_recover(self) -> None:
        sm = create_global_state_machine()
        sm.transition("receive_goal")
        sm.transition("plan_ready")
        sm.transition("user_confirm")
        sm.transition("task_dispatched")
        sm.transition("task_failed")
        assert _state(sm) == "RECOVERING"
        sm.transition("recovered")
        assert _state(sm) == "SCHEDULING"


class TestResetReturnsToInitial:
    def test_reset(self) -> None:
        sm = create_global_state_machine()
        sm.transition("receive_goal")
        sm.reset()
        assert _state(sm) == "INIT"


class TestTaskStateMachine:
    def test_happy_path(self) -> None:
        sm = create_task_state_machine()
        assert _state(sm) == "PENDING"
        sm.transition("start")
        assert _state(sm) == "RUNNING"
        sm.transition("succeed")
        assert _state(sm) == "SUCCEEDED"

    def test_skip(self) -> None:
        sm = create_task_state_machine()
        sm.transition("skip")
        assert _state(sm) == "SKIPPED"

    def test_fail(self) -> None:
        sm = create_task_state_machine()
        sm.transition("start")
        sm.transition("fail")
        assert _state(sm) == "FAILED"
