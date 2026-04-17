"""Tests for strata.health — startup health checks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
from strata.health import (
    HealthStatus,
    check_all,
    check_llm_providers,
    check_osworld,
    require_healthy,
)
from strata.paths import PathsConfig


def _minimal_config(
    *,
    osworld_enabled: bool = False,
    server_url: str = "http://localhost:5000",
) -> StrataConfig:
    return StrataConfig(
        log_level="INFO",
        audit_log="/tmp/audit.jsonl",
        trash_dir="/tmp/trash",
        providers={
            "test_provider": LLMProviderConfig(
                api_key="sk-test",
                base_url="https://api.test.com/v1",
                model="test-model",
            ),
        },
        roles=LLMRolesConfig(
            planner="test_provider",
            grounding="test_provider",
            vision="test_provider",
            search="test_provider",
        ),
        sandbox=SandboxConfig(
            enabled=False,
            root="/tmp/sandbox",
            read_only_paths=(),
            ask_for_permission=False,
        ),
        gui=GUIConfig(
            lock_timeout=10.0,
            wait_interval=0.5,
            screenshot_without_lock=False,
            enable_scroll_search=False,
            max_scroll_attempts=5,
            scroll_step_pixels=300,
        ),
        terminal=TerminalConfig(
            command_timeout=30.0,
            silence_timeout=5.0,
            default_shell="/bin/bash",
        ),
        memory=MemoryConfig(sliding_window_size=5, max_facts_in_slot=20),
        osworld=OSWorldConfig(
            enabled=osworld_enabled,
            provider="docker",
            os_type="Ubuntu",
            screen_size=(1920, 1080),
            headless=True,
            action_space="pyautogui",
            docker_image=None,
            server_url=server_url,
            request_timeout=30.0,
        ),
        paths=PathsConfig(run_root="/tmp/strata-run", keep_last_runs=5),
        max_loop_iterations=50,
        dangerous_patterns=(),
        auto_confirm_level="low",
    )


class TestCheckLLMProviders:
    @patch("strata.health.OpenAICompatProvider")
    def test_success_with_mock_provider(self, mock_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_instance.chat.return_value = MagicMock(
            content="pong", model="m", usage={}, finish_reason="stop"
        )
        mock_cls.return_value = mock_instance

        cfg = _minimal_config()
        results = check_llm_providers(cfg)

        assert len(results) == 1
        assert results[0].ok is True
        assert "test_provider" in results[0].component
        assert results[0].latency_ms >= 0

    @patch("strata.health.OpenAICompatProvider")
    def test_failure_with_unreachable_provider(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = ConnectionError("unreachable")

        cfg = _minimal_config()
        results = check_llm_providers(cfg)

        assert len(results) == 1
        assert results[0].ok is False
        assert "ConnectionError" in results[0].detail


class TestCheckOSWorld:
    @patch("strata.health.urllib.request.urlopen")
    def test_success_with_mock_server(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"width": 1920, "height": 1080}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cfg = _minimal_config(osworld_enabled=True)
        result = check_osworld(cfg)

        assert result.ok is True
        assert "1920" in result.detail
        assert "1080" in result.detail

    @patch("strata.health.urllib.request.urlopen")
    def test_failure_with_unreachable_server(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = ConnectionError("refused")

        cfg = _minimal_config(osworld_enabled=True)
        result = check_osworld(cfg)

        assert result.ok is False
        assert "refused" in result.detail


class TestCheckAll:
    @patch("strata.health.OpenAICompatProvider")
    def test_includes_llm_when_providers_exist(self, mock_cls: MagicMock) -> None:
        mock_instance = MagicMock()
        mock_instance.chat.return_value = MagicMock(content="ok")
        mock_cls.return_value = mock_instance

        cfg = _minimal_config()
        results = check_all(cfg)

        assert len(results) >= 1
        assert any("llm/" in s.component for s in results)

    @patch("strata.health.urllib.request.urlopen")
    @patch("strata.health.OpenAICompatProvider")
    def test_includes_osworld_when_enabled(
        self, mock_cls: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        mock_instance = MagicMock()
        mock_instance.chat.return_value = MagicMock(content="ok")
        mock_cls.return_value = mock_instance

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"width": 1920, "height": 1080}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        cfg = _minimal_config(osworld_enabled=True)
        results = check_all(cfg)

        assert any("osworld" in s.component for s in results)


class TestRequireHealthy:
    def test_passes_on_all_ok(self) -> None:
        statuses = [
            HealthStatus(component="a", ok=True, detail="ok", latency_ms=1.0),
            HealthStatus(component="b", ok=True, detail="ok", latency_ms=2.0),
        ]
        require_healthy(statuses)

    def test_exits_on_failure(self) -> None:
        statuses = [
            HealthStatus(component="a", ok=True, detail="ok", latency_ms=1.0),
            HealthStatus(component="b", ok=False, detail="broken", latency_ms=5.0),
        ]
        with pytest.raises(SystemExit) as exc_info:
            require_healthy(statuses)
        assert exc_info.value.code == 1
