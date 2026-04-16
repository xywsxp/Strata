"""Tests for ``strata.harness.actions`` — action vocabulary single source of truth."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from strata.harness.actions import (
    ACTION_OPTIONAL_PARAMS,
    ACTION_PARAM_SCHEMA,
    ACTION_VOCABULARY,
    DESTRUCTIVE_ACTIONS,
    format_action_catalog_for_llm,
)

# ── L2 property: vocabulary/schema alignment ──


def test_vocabulary_schema_alignment() -> None:
    """ACTION_VOCABULARY and ACTION_PARAM_SCHEMA must have identical key sets."""
    assert set(ACTION_VOCABULARY) == set(ACTION_PARAM_SCHEMA.keys())
    assert set(ACTION_VOCABULARY) == set(ACTION_OPTIONAL_PARAMS.keys())


def test_no_duplicate_action_names() -> None:
    assert len(ACTION_VOCABULARY) == len(set(ACTION_VOCABULARY))


def test_destructive_subset() -> None:
    assert DESTRUCTIVE_ACTIONS.issubset(set(ACTION_VOCABULARY))
    assert "write_file" in DESTRUCTIVE_ACTIONS
    assert "move_to_trash" in DESTRUCTIVE_ACTIONS
    assert "execute_command" in DESTRUCTIVE_ACTIONS
    assert "read_file" not in DESTRUCTIVE_ACTIONS
    assert "click" not in DESTRUCTIVE_ACTIONS


# ── L3 examples: schema keys for critical actions ──


def test_click_schema_has_x_y() -> None:
    assert ACTION_PARAM_SCHEMA["click"] == frozenset({"x", "y"})


def test_execute_command_schema_has_command() -> None:
    assert "command" in ACTION_PARAM_SCHEMA["execute_command"]


def test_write_file_schema_has_path_and_content() -> None:
    assert ACTION_PARAM_SCHEMA["write_file"] == frozenset({"path", "content"})


def test_locate_and_click_schema_has_description() -> None:
    assert "description" in ACTION_PARAM_SCHEMA["locate_and_click"]


# ── L2 / L3: catalog formatting ──


def test_catalog_non_empty() -> None:
    out = format_action_catalog_for_llm()
    assert len(out) > 0
    assert "Available actions" in out


def test_catalog_contains_every_action() -> None:
    out = format_action_catalog_for_llm()
    for name in ACTION_VOCABULARY:
        assert name in out


def test_catalog_marks_destructive_actions() -> None:
    out = format_action_catalog_for_llm()
    for name in DESTRUCTIVE_ACTIONS:
        line = next(line for line in out.splitlines() if line.startswith(f"- {name}"))
        assert "DESTRUCTIVE" in line


@given(st.sampled_from(ACTION_VOCABULARY))
def test_catalog_line_shape(name: str) -> None:
    out = format_action_catalog_for_llm()
    lines = [line for line in out.splitlines() if line.startswith(f"- {name}")]
    assert len(lines) == 1, f"expected exactly one line for {name}"


# ── Exhaustiveness check for schema shape ──


def test_every_action_schema_is_frozenset() -> None:
    for name in ACTION_VOCABULARY:
        assert isinstance(ACTION_PARAM_SCHEMA[name], frozenset)
        assert isinstance(ACTION_OPTIONAL_PARAMS[name], frozenset)


def test_action_vocabulary_is_tuple() -> None:
    assert isinstance(ACTION_VOCABULARY, tuple)


# ── Negative guard: no unknown key sneaks into schema ──


@pytest.mark.parametrize("action", list(ACTION_VOCABULARY))
def test_optional_disjoint_from_required(action: str) -> None:
    required = ACTION_PARAM_SCHEMA[action]
    optional = ACTION_OPTIONAL_PARAMS[action]
    assert required.isdisjoint(optional), f"{action}: required/optional overlap"
