"""E2E test fixtures.

``config.toml`` at the repo root contains real API keys; we re-use it so the
E2E surface matches production. Tests that call the real network are marked
``live_llm`` / ``integration`` and are opt-in via ``-m`` or environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strata.core.config import StrataConfig, load_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config.toml"


@pytest.fixture(scope="session")
def repo_config() -> StrataConfig:
    """Load the real repository config.toml once per test session.

    If ``config.toml`` is absent, the session skips — the fixture is only
    meaningful in a developer workspace that supplies it.
    """
    if not _CONFIG_PATH.exists():
        pytest.skip(f"config.toml not found at {_CONFIG_PATH}")
    return load_config(str(_CONFIG_PATH))
