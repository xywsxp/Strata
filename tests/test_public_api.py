"""Public API surface stability test.

Each package's ``__all__`` is the contract between strata and its consumers
(CLI, `__main__`, tests, downstream integrations). This test guarantees every
exported name can actually be resolved via ``importlib`` — a stricter check
than a plain ``from pkg import *`` star-import would give because it surfaces
the specific broken symbol.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

import pytest


def _module_exports(module_name: str) -> tuple[str, tuple[str, ...]]:
    module = importlib.import_module(module_name)
    exports = getattr(module, "__all__", None)
    assert exports is not None, f"{module_name} is missing __all__"
    assert isinstance(exports, list | tuple), (
        f"{module_name}.__all__ must be list or tuple, got {type(exports)!r}"
    )
    return module_name, tuple(exports)


def _symbol_params() -> Iterable[tuple[str, str]]:
    for mod in ("strata.core", "strata.harness", "strata.llm"):
        _, names = _module_exports(mod)
        for name in names:
            yield mod, name


@pytest.mark.parametrize(("module_name", "symbol"), list(_symbol_params()))
def test_public_export_resolves(module_name: str, symbol: str) -> None:
    """Every symbol in ``__all__`` must be a real attribute of the module."""
    module = importlib.import_module(module_name)
    assert hasattr(module, symbol), f"{module_name}.{symbol} is declared in __all__ but missing"


@pytest.mark.parametrize("module_name", ["strata.core", "strata.harness", "strata.llm"])
def test_public_exports_unique(module_name: str) -> None:
    _, names = _module_exports(module_name)
    assert len(names) == len(set(names)), (
        f"{module_name}.__all__ contains duplicates: {sorted(names)}"
    )


def test_core_exports_include_root_error() -> None:
    import strata.core as core

    assert "StrataError" in core.__all__
    assert issubclass(core.HarnessError, core.StrataError)


def test_harness_exports_include_orchestrator_and_vocabulary() -> None:
    import strata.harness as harness

    required = {
        "AgentOrchestrator",
        "AgentUI",
        "ExecutionResult",
        "PrimitiveTaskExecutor",
        "ACTION_VOCABULARY",
        "PersistenceManager",
        "AuditLogger",
        "ContextManager",
        "GUILock",
    }
    missing = required - set(harness.__all__)
    assert not missing, f"harness.__all__ missing: {missing}"


def test_llm_exports_include_router() -> None:
    import strata.llm as llm

    assert "LLMRouter" in llm.__all__
