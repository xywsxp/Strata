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


@pytest.fixture(scope="session")
def osworld_url(repo_config: StrataConfig) -> str:
    """Return OSWorld server URL; skip if not enabled or unreachable."""
    if not repo_config.osworld.enabled:
        pytest.skip("osworld.enabled=false in config.toml")

    from strata.health import check_osworld

    status = check_osworld(repo_config)
    if not status.ok:
        pytest.skip(f"OSWorld not reachable: {status.detail}")
    return repo_config.osworld.server_url
