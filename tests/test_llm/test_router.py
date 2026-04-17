"""Tests for strata.llm.router — role-based provider routing."""

from __future__ import annotations

from unittest.mock import patch

import icontract
import pytest

from strata.core.config import get_default_config
from strata.llm.router import LLMRouter


class TestRouterInit:
    @patch("strata.llm.router.OpenAICompatProvider")
    def test_creates_providers(self, mock_provider_cls: object) -> None:
        cfg = get_default_config()
        router = LLMRouter(cfg)
        assert router.get_provider("planner") is not None

    @patch("strata.llm.router.OpenAICompatProvider")
    def test_same_provider_instance_for_same_name(self, mock_cls: object) -> None:
        cfg = get_default_config()
        router = LLMRouter(cfg)
        p1 = router.get_provider("planner")
        p2 = router.get_provider("grounding")
        assert p1 is p2

    def test_missing_provider_raises(self) -> None:
        from strata.core.config import (
            GUIConfig,
            LLMProviderConfig,
            LLMRolesConfig,
            MemoryConfig,
            OSWorldConfig,
            SandboxConfig,
            StrataConfig,
            TerminalConfig,
        )
        from strata.paths import PathsConfig

        cfg = StrataConfig(
            log_level="INFO",
            audit_log="/tmp/audit.jsonl",
            trash_dir="/tmp/trash",
            providers={"test": LLMProviderConfig(api_key="sk-x", base_url="https://x", model="m")},
            roles=LLMRolesConfig(
                planner="test",
                grounding="test",
                vision="nonexistent",
                search="test",
            ),
            sandbox=SandboxConfig(
                enabled=True,
                root="/tmp/sb",
                read_only_paths=(),
                ask_for_permission=True,
            ),
            gui=GUIConfig(
                lock_timeout=10.0,
                wait_interval=0.5,
                screenshot_without_lock=False,
                enable_scroll_search=True,
                max_scroll_attempts=10,
                scroll_step_pixels=300,
            ),
            terminal=TerminalConfig(
                command_timeout=300.0, silence_timeout=30.0, default_shell="/bin/bash"
            ),
            memory=MemoryConfig(sliding_window_size=5, max_facts_in_slot=20),
            osworld=OSWorldConfig(
                enabled=False,
                provider="docker",
                os_type="Ubuntu",
                screen_size=(1920, 1080),
                headless=True,
                action_space="computer_13",
                docker_image=None,
            ),
            paths=PathsConfig(run_root="/tmp/strata-test", keep_last_runs=5),
            max_loop_iterations=50,
        )
        with pytest.raises(icontract.ViolationError):
            LLMRouter(cfg)


class TestRouterDispatches:
    @patch("strata.llm.router.OpenAICompatProvider")
    def test_get_provider_returns_correct_provider(self, mock_cls: object) -> None:
        cfg = get_default_config()
        router = LLMRouter(cfg)
        provider = router.get_provider("vision")
        assert provider is not None
        assert provider.model_name is not None
