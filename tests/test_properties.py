"""Hypothesis property tests — Phase 6.

6.3: Checkpoint roundtrip
6.4: redact idempotency
6.5: State machine transition legality
"""

from __future__ import annotations

from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given, settings

from strata.core.types import TaskGraph, TaskNode
from strata.grounding.filter import contains_sensitive, redact
from strata.harness.persistence import (
    Checkpoint,
    PersistenceManager,
    _checkpoint_from_dict,
    _checkpoint_to_dict,
)
from strata.harness.state_machine import (
    VALID_GLOBAL_TRANSITIONS,
    create_global_state_machine,
)

# ── 6.3: Checkpoint roundtrip ──

_GLOBAL_STATES = st.sampled_from(["INIT", "PLANNING", "EXECUTING", "COMPLETED", "FAILED"])
_TASK_STATES = st.sampled_from(["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"])


@st.composite
def st_checkpoint(draw: st.DrawFn) -> Checkpoint:
    gs = draw(_GLOBAL_STATES)
    n_tasks = draw(st.integers(min_value=0, max_value=5))
    task_states = {f"t{i}": draw(_TASK_STATES) for i in range(n_tasks)}
    ctx_keys = draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=8, alphabet=st.characters(categories=["L"])),
            values=st.text(max_size=20),
            max_size=3,
        )
    )
    context: dict[str, object] = {k: v for k, v in ctx_keys.items()}
    nodes = tuple(
        TaskNode(id=f"t{i}", task_type="primitive", action="click") for i in range(n_tasks)
    )
    graph = TaskGraph(goal="test", tasks=nodes)
    return Checkpoint(
        global_state=gs,  # type: ignore[arg-type]
        task_states=task_states,  # type: ignore[arg-type]
        context=context,
        task_graph=graph,
        timestamp=draw(st.floats(min_value=0.0, max_value=1e12, allow_nan=False)),
    )


@given(cp=st_checkpoint())
@settings(max_examples=50)
def test_prop_checkpoint_roundtrip_dict(cp: Checkpoint) -> None:
    """checkpoint_to_dict → checkpoint_from_dict is identity."""
    d = _checkpoint_to_dict(cp)
    restored = _checkpoint_from_dict(d)
    assert restored.global_state == cp.global_state
    assert restored.task_states == cp.task_states
    assert restored.task_graph.goal == cp.task_graph.goal
    assert len(restored.task_graph.tasks) == len(cp.task_graph.tasks)


@given(cp=st_checkpoint())
@settings(max_examples=30)
def test_prop_checkpoint_roundtrip_file(cp: Checkpoint, tmp_path_factory: object) -> None:
    """PersistenceManager.save → load is identity."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        mgr = PersistenceManager(str(Path(td) / "state"))
        mgr.save_checkpoint(cp)
        loaded = mgr.load_checkpoint()
        assert loaded is not None
        assert loaded.global_state == cp.global_state
        assert loaded.task_states == cp.task_states


# ── 6.4: Sensitive filter — redact idempotency ──


@given(text=st.text(max_size=200))
@settings(max_examples=100)
def test_prop_redact_idempotent(text: str) -> None:
    """redact(redact(x)) == redact(x) — fixpoint after one pass."""
    once = redact(text)
    twice = redact(once)
    assert once == twice


@given(text=st.text(max_size=200))
@settings(max_examples=100)
def test_prop_redacted_text_not_sensitive(text: str) -> None:
    """After redacting, contains_sensitive must return False."""
    result = redact(text)
    assert not contains_sensitive(result)


# ── 6.5: State machine transition legality ──


_ALL_EVENTS = list({e for transitions in VALID_GLOBAL_TRANSITIONS.values() for e in transitions})


@given(
    events=st.lists(
        st.sampled_from(_ALL_EVENTS),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=100)
def test_prop_state_machine_only_valid_transitions(events: list[str]) -> None:
    """Applying random events to the state machine must either succeed
    (and land in a valid state) or raise StateTransitionError — never
    corrupt the machine.
    """
    import contextlib

    from strata.core.errors import StateTransitionError

    sm = create_global_state_machine()
    for event in events:
        with contextlib.suppress(StateTransitionError):
            sm.transition(event)  # type: ignore[arg-type]
    # After any sequence of events, state must be a valid GlobalState
    assert sm.state in VALID_GLOBAL_TRANSITIONS
