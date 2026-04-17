"""Tests for strata.core.config — TOML loading, validation, defaults."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from strata.core.config import (
    LLMProviderConfig,
    StrataConfig,
    get_default_config,
    load_config,
)
from strata.core.errors import ConfigError

MINIMAL_TOML = textwrap.dedent("""\
    [providers.test]
    api_key = "sk-test"
    base_url = "https://api.example.com/v1"
    model = "test-model"

    [roles]
    planner = "test"
    grounding = "test"
    vision = "test"
    search = "test"

    [sandbox]
    root = "/tmp/strata-test-sandbox"

    [terminal]
    default_shell = "/bin/bash"
""")


class TestLoadConfig:
    def test_load_example_config(self) -> None:
        cfg = load_config("config.example.toml")
        assert isinstance(cfg, StrataConfig)
        assert len(cfg.providers) >= 1

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(MINIMAL_TOML)
        cfg = load_config(str(p))
        assert cfg.providers["test"].model == "test-model"
        assert cfg.roles.planner == "test"

    def test_optional_field_uses_default(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(MINIMAL_TOML)
        cfg = load_config(str(p))
        assert cfg.log_level == "INFO"
        assert cfg.gui.lock_timeout == 10.0
        assert cfg.gui.enable_scroll_search is True
        assert cfg.gui.max_scroll_attempts == 10
        assert cfg.gui.scroll_step_pixels == 300
        assert cfg.memory.sliding_window_size == 5

    def test_required_field_missing_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(
            textwrap.dedent("""\
            [providers.test]
            api_key = "sk-test"
            base_url = "https://api.example.com/v1"
            model = "test-model"

            [roles]
            planner = "test"
            grounding = "test"
            vision = "test"
            search = "test"

            [terminal]
            default_shell = "/bin/bash"
        """)
        )
        with pytest.raises(ConfigError, match="sandbox"):
            load_config(str(p))

    def test_invalid_toml_raises_config_error(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_config(str(p))

    def test_expand_tilde(self, tmp_path: Path) -> None:
        toml = 'audit_log = "~/.strata/test_audit.jsonl"\n\n' + MINIMAL_TOML
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(str(p))
        assert "~" not in cfg.audit_log
        assert cfg.audit_log.endswith("test_audit.jsonl")

    def test_roles_reference_missing_provider(self, tmp_path: Path) -> None:
        toml = textwrap.dedent("""\
            [providers.test]
            api_key = "sk-test"
            base_url = "https://api.example.com/v1"
            model = "test-model"

            [roles]
            planner = "test"
            grounding = "test"
            vision = "nonexistent"
            search = "test"

            [sandbox]
            root = "/tmp/strata-test-sandbox"

            [terminal]
            default_shell = "/bin/bash"
        """)
        p = tmp_path / "config.toml"
        p.write_text(toml)
        with pytest.raises(ConfigError, match="nonexistent"):
            load_config(str(p))


class TestGetDefaultConfig:
    def test_default_config_valid(self) -> None:
        cfg = get_default_config()
        assert isinstance(cfg, StrataConfig)
        assert cfg.log_level == "INFO"
        assert cfg.osworld.enabled is False
        assert cfg.osworld.provider == "docker"
        assert cfg.osworld.screen_size == (1920, 1080)
        assert cfg.osworld.headless is True
        assert cfg.osworld.action_space == "computer_13"
        assert cfg.osworld.docker_image is None

    def test_default_providers_have_keys(self) -> None:
        cfg = get_default_config()
        for p in cfg.providers.values():
            assert p.api_key.strip()


class TestLLMProviderConfigRepr:
    def test_api_key_hidden_in_repr(self) -> None:
        p = LLMProviderConfig(api_key="sk-real-secret-key", base_url="https://x", model="m")
        r = repr(p)
        assert "sk-real-secret-key" not in r
        assert "sk-***" in r

    def test_strata_config_repr_hides_keys(self) -> None:
        cfg = get_default_config()
        r = repr(cfg)
        assert "sk-placeholder" not in r


class TestPathsConfig:
    def test_load_config_with_paths_section(self, tmp_path: Path) -> None:
        toml = MINIMAL_TOML + textwrap.dedent("""
            [paths]
            run_root = "/tmp/strata-test-runs"
            keep_last_runs = 10
        """)
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(str(p))
        assert cfg.paths.run_root == "/tmp/strata-test-runs"
        assert cfg.paths.keep_last_runs == 10

    def test_load_config_missing_paths_uses_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "config.toml"
        p.write_text(MINIMAL_TOML)
        cfg = load_config(str(p))
        assert "runs-fallback" in cfg.paths.run_root
        assert cfg.paths.keep_last_runs == 5

    def test_paths_run_root_expands_tilde(self, tmp_path: Path) -> None:
        toml = MINIMAL_TOML + textwrap.dedent("""
            [paths]
            run_root = "~/.strata/my-runs"
        """)
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(str(p))
        assert "~" not in cfg.paths.run_root
        assert cfg.paths.run_root.endswith("my-runs")


class TestOSWorldConfig:
    def test_osworld_defaults(self) -> None:
        cfg = get_default_config()
        assert cfg.osworld.enabled is False
        assert cfg.osworld.os_type == "Ubuntu"

    def test_osworld_from_toml(self, tmp_path: Path) -> None:
        toml = MINIMAL_TOML + textwrap.dedent("""
            [osworld]
            enabled = true
            provider = "docker"
            os_type = "Ubuntu"
            screen_size = [1920, 1080]
            headless = false
            action_space = "computer_13"
        """)
        p = tmp_path / "config.toml"
        p.write_text(toml)
        cfg = load_config(str(p))
        assert cfg.osworld.enabled is True
        assert cfg.osworld.headless is False
