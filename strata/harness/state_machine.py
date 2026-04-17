"""Global and task-level state machines with typed transitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Generic, Literal, TypeVar

import icontract

from strata.core.errors import StateTransitionError
from strata.core.types import GlobalState, TaskState

S = TypeVar("S")
E = TypeVar("E")

GlobalEvent = Literal[
    "receive_goal",
    "plan_ready",
    "user_confirm",
    "user_revise",
    "task_dispatched",
    "task_done",
    "task_failed",
    "recovered",
    "escalated",
    "user_decision",
    "user_abort",
    "all_done",
    "unrecoverable",
]

TaskEvent = Literal["start", "succeed", "fail", "skip"]

VALID_GLOBAL_TRANSITIONS: Final[Mapping[GlobalState, Mapping[GlobalEvent, GlobalState]]] = {
    "INIT": {"receive_goal": "PLANNING"},
    "PLANNING": {"plan_ready": "CONFIRMING", "unrecoverable": "FAILED"},
    "CONFIRMING": {
        "user_confirm": "SCHEDULING",
        "user_revise": "PLANNING",
        "user_abort": "FAILED",
    },
    "SCHEDULING": {"task_dispatched": "EXECUTING", "all_done": "COMPLETED"},
    "EXECUTING": {
        "task_done": "SCHEDULING",
        "task_failed": "RECOVERING",
        "user_abort": "FAILED",
    },
    "RECOVERING": {
        "recovered": "SCHEDULING",
        "escalated": "WAITING_USER",
        "unrecoverable": "FAILED",
    },
    "WAITING_USER": {
        "user_decision": "SCHEDULING",
        "user_abort": "FAILED",
    },
    "COMPLETED": {},
    "FAILED": {},
}

VALID_TASK_TRANSITIONS: Final[Mapping[TaskState, Mapping[TaskEvent, TaskState]]] = {
    "PENDING": {"start": "RUNNING", "skip": "SKIPPED"},
    "RUNNING": {"succeed": "SUCCEEDED", "fail": "FAILED"},
    "SUCCEEDED": {},
    "FAILED": {},
    "SKIPPED": {},
}


class StateMachine(Generic[S, E]):
    """Generic state machine with typed states and events."""

    def __init__(self, initial: S, transitions: Mapping[S, Mapping[E, S]]) -> None:
        self._initial = initial
        self._state = initial
        self._transitions = transitions

    @property
    def state(self) -> S:
        return self._state

    def can_transition(self, event: E) -> bool:
        current_map = self._transitions.get(self._state)
        if current_map is None:
            return False
        return event in current_map

    @icontract.require(
        lambda self, event: self.can_transition(event),
        "transition must be valid for current state",
        error=lambda self, event: StateTransitionError(
            f"cannot apply {event!r} in state {self._state!r}"
        ),
    )
    def transition(self, event: E) -> S:
        """Apply *event* and return the new state."""
        new_state = self._transitions[self._state][event]
        self._state = new_state
        return new_state

    def reset(self) -> None:
        self._state = self._initial


def create_global_state_machine() -> StateMachine[GlobalState, GlobalEvent]:
    return StateMachine("INIT", VALID_GLOBAL_TRANSITIONS)


def create_task_state_machine() -> StateMachine[TaskState, TaskEvent]:
    return StateMachine("PENDING", VALID_TASK_TRANSITIONS)
