"""Action vocabulary — single source of truth for planner / executor alignment.

The vocabulary is shared by:
* ``strata.planner.htn.decompose_goal`` as ``available_actions`` input to the LLM.
* ``strata.harness.executor.PrimitiveTaskExecutor`` as the exhaustive dispatch set.

Both sides consume ``ACTION_VOCABULARY`` / ``ACTION_PARAM_SCHEMA`` so a drift
between planner output and executor dispatch is caught at import time by the
module-level invariant assert below.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

import icontract

ActionName = Literal[
    "click",
    "double_click",
    "move_mouse",
    "type_text",
    "press_key",
    "hotkey",
    "scroll",
    "screenshot",
    "locate_and_click",
    "execute_command",
    "read_file",
    "write_file",
    "list_directory",
    "move_to_trash",
    "launch_app",
    "close_app",
    "get_clipboard",
    "set_clipboard",
]

# CONVENTION: strata.harness.actions — 词典用 tuple[Literal, ...] 而非 Enum。
# 理由：action 名直接对应 planner 产出的 JSON 字符串，tuple 无额外 .value 解包；
# Literal 类型可从 tuple 推导，给 mypy 和 IDE 同等提示；Enum 反而多一层包装。
ACTION_VOCABULARY: Final[tuple[ActionName, ...]] = (
    "click",
    "double_click",
    "move_mouse",
    "type_text",
    "press_key",
    "hotkey",
    "scroll",
    "screenshot",
    "locate_and_click",
    "execute_command",
    "read_file",
    "write_file",
    "list_directory",
    "move_to_trash",
    "launch_app",
    "close_app",
    "get_clipboard",
    "set_clipboard",
)

ACTION_PARAM_SCHEMA: Final[Mapping[str, frozenset[str]]] = {
    "click": frozenset({"x", "y"}),
    "double_click": frozenset({"x", "y"}),
    "move_mouse": frozenset({"x", "y"}),
    "type_text": frozenset({"text"}),
    "press_key": frozenset({"key"}),
    "hotkey": frozenset({"keys"}),
    "scroll": frozenset({"delta_x", "delta_y"}),
    "screenshot": frozenset(),
    "locate_and_click": frozenset({"description"}),
    "execute_command": frozenset({"command"}),
    "read_file": frozenset({"path"}),
    "write_file": frozenset({"path", "content"}),
    "list_directory": frozenset({"path"}),
    "move_to_trash": frozenset({"path"}),
    "launch_app": frozenset({"app_name"}),
    "close_app": frozenset({"app_identifier"}),
    "get_clipboard": frozenset(),
    "set_clipboard": frozenset({"text"}),
}

ACTION_OPTIONAL_PARAMS: Final[Mapping[str, frozenset[str]]] = {
    "click": frozenset({"button"}),
    "double_click": frozenset(),
    "move_mouse": frozenset(),
    "type_text": frozenset({"interval"}),
    "press_key": frozenset(),
    "hotkey": frozenset(),
    "scroll": frozenset(),
    "screenshot": frozenset({"region"}),
    "locate_and_click": frozenset({"role"}),
    "execute_command": frozenset({"cwd", "timeout"}),
    "read_file": frozenset(),
    "write_file": frozenset({"encoding"}),
    "list_directory": frozenset({"pattern"}),
    "move_to_trash": frozenset(),
    "launch_app": frozenset({"args"}),
    "close_app": frozenset(),
    "get_clipboard": frozenset(),
    "set_clipboard": frozenset(),
}

DESTRUCTIVE_ACTIONS: Final[frozenset[str]] = frozenset(
    {"write_file", "move_to_trash", "execute_command"}
)

assert set(ACTION_VOCABULARY) == set(ACTION_PARAM_SCHEMA.keys()), (
    "invariant: ACTION_VOCABULARY must align with ACTION_PARAM_SCHEMA keys"
)
assert set(ACTION_VOCABULARY) == set(ACTION_OPTIONAL_PARAMS.keys()), (
    "invariant: ACTION_VOCABULARY must align with ACTION_OPTIONAL_PARAMS keys"
)
assert len(ACTION_VOCABULARY) == len(set(ACTION_VOCABULARY)), (
    "invariant: ACTION_VOCABULARY must have no duplicate names"
)
assert DESTRUCTIVE_ACTIONS.issubset(set(ACTION_VOCABULARY)), (
    "invariant: DESTRUCTIVE_ACTIONS must be a subset of ACTION_VOCABULARY"
)


@icontract.ensure(lambda result: len(result) > 0)
@icontract.ensure(lambda result: all(name in result for name in ACTION_VOCABULARY))
def format_action_catalog_for_llm() -> str:
    """Render the vocabulary for inclusion in the planner LLM prompt.

    The output is deterministic for a given module state so prompt hashes used
    by caching layers remain stable across runs.
    """
    lines: list[str] = [
        "Available actions (JSON dispatch names — use these as TaskNode.action):",
    ]
    for name in ACTION_VOCABULARY:
        required = sorted(ACTION_PARAM_SCHEMA[name])
        optional = sorted(ACTION_OPTIONAL_PARAMS[name])
        req_str = ", ".join(required) if required else "(none)"
        opt_str = ", ".join(optional) if optional else "(none)"
        destructive = " [DESTRUCTIVE]" if name in DESTRUCTIVE_ACTIONS else ""
        lines.append(f"- {name}{destructive}: required={req_str}; optional={opt_str}")
    return "\n".join(lines)
