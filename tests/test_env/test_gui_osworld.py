"""Tests for strata.env.gui_osworld — OSWorld GUI adapter (mocked DesktopEnv)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strata.core.config import OSWorldConfig
from strata.core.errors import OSWorldConnectionError


def _osworld_config() -> OSWorldConfig:
    return OSWorldConfig(
        enabled=True,
        provider="docker",
        os_type="Ubuntu",
        screen_size=(1920, 1080),
        headless=True,
        action_space="computer_13",
        docker_image=None,
    )


class TestOSWorldGUIAdapter:
    @patch("strata.env.gui_osworld.DesktopEnv", create=True)
    def test_click_delegates(self, mock_env_cls: MagicMock) -> None:
        """Verify click maps to execute_action('click', ...)."""
        from strata.env.gui_osworld import OSWorldGUIAdapter

        mock_env = MagicMock()
        mock_env_cls.return_value = mock_env

        with patch.object(OSWorldGUIAdapter, "__init__", lambda self, config: None):
            adapter = OSWorldGUIAdapter.__new__(OSWorldGUIAdapter)
            adapter._env = mock_env
            adapter._screen_w = 1920
            adapter._screen_h = 1080
            adapter._config = _osworld_config()

        adapter.click(100.0, 200.0)
        mock_env.execute_action.assert_called_once_with(
            "click", {"coordinate": [100.0, 200.0], "button": "left"}
        )

    def test_dpi_scale_is_1(self) -> None:
        """OSWorld VMs have fixed DPI 1.0."""
        from strata.env.gui_osworld import OSWorldGUIAdapter

        with patch.object(OSWorldGUIAdapter, "__init__", lambda self, config: None):
            adapter = OSWorldGUIAdapter.__new__(OSWorldGUIAdapter)
        assert adapter.get_dpi_scale_for_point(100.0, 200.0) == 1.0

    @patch("strata.env.gui_osworld.DesktopEnv", create=True)
    def test_screenshot_returns_bytes(self, mock_env_cls: MagicMock) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        mock_env = MagicMock()
        mock_env.execute_action.return_value = b"PNG_DATA"
        mock_env_cls.return_value = mock_env

        with patch.object(OSWorldGUIAdapter, "__init__", lambda self, config: None):
            adapter = OSWorldGUIAdapter.__new__(OSWorldGUIAdapter)
            adapter._env = mock_env
            adapter._screen_w = 1920
            adapter._screen_h = 1080
            adapter._config = _osworld_config()

        result = adapter.capture_screen()
        assert isinstance(result, bytes)
        assert len(result) > 0


class TestOSWorldImportError:
    def test_missing_desktop_env_raises(self) -> None:
        with pytest.raises(OSWorldConnectionError, match="desktop_env package not installed"):
            from strata.env.gui_osworld import OSWorldGUIAdapter

            OSWorldGUIAdapter(_osworld_config())
