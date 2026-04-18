"""Hypothesis property tests — Phase 6 + Phase 7.

6.3: Checkpoint roundtrip
6.4: redact idempotency
6.5: State machine transition legality
6.6: New property tests (task roundtrip, scaler, sudo, cycle detection, etc.)
7.1: DebugConfig property tests
"""

from __future__ import annotations

from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given, settings

from strata.core._validators import VALID_GLOBAL_STATES
from strata.core.config import DebugConfig
from strata.core.types import GlobalState, TaskGraph, TaskNode
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
from tests.strategies import (
    st_checkpoint,
    st_debug_config,
    st_sudo_command,
    st_task_graph_strategy,
    st_task_node_strategy,
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


# ── Validators: GlobalState ↔ frozenset sync ──


def test_prop_valid_global_states_synced_with_literal() -> None:
    """VALID_GLOBAL_STATES must exactly equal the Literal args of GlobalState."""
    from typing import get_args

    assert frozenset(get_args(GlobalState)) == VALID_GLOBAL_STATES


def test_global_states_contains_all_literal_values() -> None:
    """Exhaustive check: every GlobalState value is in VALID_GLOBAL_STATES."""
    expected = {
        "INIT",
        "PLANNING",
        "CONFIRMING",
        "SCHEDULING",
        "EXECUTING",
        "RECOVERING",
        "WAITING_USER",
        "COMPLETED",
        "FAILED",
    }
    assert expected == VALID_GLOBAL_STATES


# ── 6.6.1: TaskNode roundtrip ──


@given(node=st_task_node_strategy())
@settings(max_examples=100)
def test_prop_task_node_roundtrip(node: TaskNode) -> None:
    """task_node_from_dict(task_node_to_dict(n)) preserves identity."""
    from strata.core.types import task_node_from_dict, task_node_to_dict

    d = task_node_to_dict(node)
    restored = task_node_from_dict(d)
    assert restored.id == node.id
    assert restored.task_type == node.task_type
    assert restored.action == node.action
    assert restored.method == node.method
    assert restored.depends_on == node.depends_on
    assert restored.output_var == node.output_var
    assert restored.max_iterations == node.max_iterations


# ── 6.6.2: TaskGraph roundtrip ──


@given(graph=st_task_graph_strategy())
@settings(max_examples=50)
def test_prop_task_graph_roundtrip(graph: TaskGraph) -> None:
    """task_graph_from_dict(task_graph_to_dict(g)) preserves goal and task count."""
    from strata.core.types import task_graph_from_dict, task_graph_to_dict

    d = task_graph_to_dict(graph)
    restored = task_graph_from_dict(d)
    assert restored.goal == graph.goal
    assert len(restored.tasks) == len(graph.tasks)
    for orig, rest in zip(graph.tasks, restored.tasks, strict=True):
        assert orig.id == rest.id
        assert orig.task_type == rest.task_type


# ── 6.6.3: validate_graph valid → empty errors ──


@st.composite
def _st_primitive_only_graph(draw: st.DrawFn) -> TaskGraph:
    """Generate a graph with only primitive tasks (no compound method refs)."""
    from tests.strategies import st_task_node

    goal = draw(st.text(min_size=1, max_size=50))
    n = draw(st.integers(min_value=1, max_value=10))
    nodes: list[TaskNode] = []
    for i in range(n):
        node = draw(st_task_node(task_type="primitive"))
        nodes.append(
            TaskNode(
                id=f"{node.id}_{i}",
                task_type="primitive",
                action=node.action,
                params=node.params,
                depends_on=(),
                output_var=node.output_var,
                max_iterations=None,
            )
        )
    return TaskGraph(goal=goal, tasks=tuple(nodes))


@given(graph=_st_primitive_only_graph())
@settings(max_examples=50)
def test_prop_validate_graph_valid_returns_empty(graph: TaskGraph) -> None:
    """A well-formed primitive-only graph validates clean."""
    from strata.planner.htn import validate_graph

    errors = validate_graph(graph)
    assert errors == [], f"unexpected validation errors: {errors}"


# ── 6.6.4: validate_literal identity ──


@given(value=st.sampled_from(sorted(VALID_GLOBAL_STATES)))
@settings(max_examples=50)
def test_prop_validate_literal_identity(value: str) -> None:
    """validate_literal(v, valid, name) == v for any v in valid."""
    from strata.core._validators import validate_literal

    result = validate_literal(value, VALID_GLOBAL_STATES, "test_field")
    assert result == value


# ── 6.6.5: CoordinateScaler roundtrip ──


@given(
    x=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    y=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    scale=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_prop_coordinate_scaler_roundtrip(x: float, y: float, scale: float) -> None:
    """physical_to_logical(logical_to_physical(c)) ≈ c within float tolerance."""
    from unittest.mock import MagicMock

    from strata.core.types import Coordinate
    from strata.grounding.scaler import CoordinateScaler

    gui = MagicMock()
    gui.get_dpi_scale_for_point.return_value = scale
    scaler = CoordinateScaler(gui)
    coord = Coordinate(x=x, y=y)
    physical = scaler.logical_to_physical(coord)
    back = scaler.physical_to_logical(physical)
    assert abs(back.x - coord.x) < 1e-6, f"x: {back.x} != {coord.x}"
    assert abs(back.y - coord.y) < 1e-6, f"y: {back.y} != {coord.y}"


# ── 6.6.6: _detect_cycles acyclic ──


@given(graph=st_task_graph_strategy())
@settings(max_examples=50)
def test_prop_detect_cycles_acyclic(graph: TaskGraph) -> None:
    """An acyclic graph (no depends_on) has no cycles."""
    from strata.planner.htn import _detect_cycles

    errors = _detect_cycles(graph.tasks)
    assert errors == []


# ── 6.6.7: _sanitize_sudo idempotent ──


@given(cmd=st_sudo_command())
@settings(max_examples=100)
def test_prop_sanitize_sudo_idempotent(cmd: str) -> None:
    """f(f(cmd)) == f(cmd) — sanitization is a fixpoint."""
    from unittest.mock import MagicMock

    from strata.core.config import TerminalConfig
    from strata.grounding.terminal_handler import TerminalHandler

    handler = TerminalHandler(
        MagicMock(),
        TerminalConfig(command_timeout=30.0, silence_timeout=10.0, default_shell="/bin/sh"),
    )
    once = handler._sanitize_sudo(cmd)
    twice = handler._sanitize_sudo(once)
    assert once == twice, f"not idempotent: {cmd!r} -> {once!r} -> {twice!r}"


# ── Phase 7.1: DebugConfig ──


@given(
    port=st.integers(min_value=-1000, max_value=70000),
    token=st.text(max_size=20),
)
def test_prop_debug_config_disabled_accepts_any_port(port: int, token: str) -> None:
    """When enabled=False, any port/token combination is accepted."""
    cfg = DebugConfig(enabled=False, port=port, token=token)
    assert not cfg.enabled
    assert cfg.port == port


@given(data=st.data())
def test_prop_debug_config_enabled_valid_roundtrip(data: st.DataObject) -> None:
    """A valid enabled DebugConfig can be constructed and read back."""
    cfg = data.draw(st_debug_config(enabled=True))
    assert cfg.enabled
    assert 1024 <= cfg.port <= 65535
    assert len(cfg.token.strip()) > 0


def test_load_config_debug_disabled_default() -> None:
    """Without a [debug] section, DebugConfig defaults to disabled."""
    from strata.core.config import get_default_config

    config = get_default_config()
    assert not config.debug.enabled
    assert config.debug.port == 0
    assert config.debug.token == ""


def test_parse_debug_enabled_no_token_raises() -> None:
    """enabled=true with empty token raises ConfigError."""
    import pytest

    from strata.core.config import _parse_debug
    from strata.core.errors import ConfigError

    with pytest.raises(ConfigError, match="token"):
        _parse_debug({"enabled": True, "port": 8390, "token": ""})


def test_parse_debug_enabled_bad_port_raises() -> None:
    """enabled=true with out-of-range port raises ConfigError."""
    import pytest

    from strata.core.config import _parse_debug
    from strata.core.errors import ConfigError

    with pytest.raises(ConfigError, match="port"):
        _parse_debug({"enabled": True, "port": 80, "token": "secret"})


def test_parse_debug_enabled_valid() -> None:
    """enabled=true with valid port and token parses correctly."""
    from strata.core.config import _parse_debug

    cfg = _parse_debug(
        {
            "enabled": True,
            "port": 8390,
            "token": "my-secret",
            "intercept_prompts": True,
            "max_checkpoint_history": 30,
        }
    )
    assert cfg.enabled
    assert cfg.port == 8390
    assert cfg.token == "my-secret"
    assert cfg.intercept_prompts
    assert cfg.max_checkpoint_history == 30
