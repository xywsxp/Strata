"""Tests for strata.env.gui_osworld — OSWorld GUI adapter (HTTP-based).

Phase 9.4++ rewrite: the adapter now talks to the OSWorld Docker server over
HTTP (pyautogui action-space via ``POST /run_python``, ``GET /screenshot``,
``POST /screen_size``). Tests mock :class:`OSWorldHTTPClient` directly — no
``desktop_env`` Python package is required.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from strata.core.config import OSWorldConfig
from strata.core.errors import ConfigError, OSWorldConnectionError
from strata.core.types import ScreenRegion


def _osworld_config(screen: tuple[int, int] = (1920, 1080)) -> OSWorldConfig:
    return OSWorldConfig(
        enabled=True,
        provider="docker",
        os_type="Ubuntu",
        screen_size=screen,
        headless=True,
        action_space="pyautogui",
        docker_image=None,
        server_url="http://osworld-test:5000",
        request_timeout=2.0,
    )


def _make_adapter(
    mock_client: MagicMock,
    *,
    screen: tuple[int, int] = (1920, 1080),
) -> object:
    """Construct an OSWorldGUIAdapter with a stubbed HTTP client.

    Bypasses the real ``__init__`` so no network IO occurs; the client stub
    answers `/screen_size` calls made from ``__init__`` when the test chooses
    to go through the real constructor path (see ``TestConstruction``).
    """
    from strata.env.gui_osworld import OSWorldGUIAdapter

    with patch.object(OSWorldGUIAdapter, "__init__", lambda self, config: None):
        adapter = OSWorldGUIAdapter.__new__(OSWorldGUIAdapter)
        adapter._config = _osworld_config(screen)
        adapter._client = mock_client
        adapter._screen_w, adapter._screen_h = screen
    return adapter


class TestConstructionGoesThroughHTTP:
    def test_init_verifies_screen_size_over_http(self) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        mock_client = MagicMock()
        mock_client.post_json.return_value = {"width": 1920, "height": 1080}
        with patch(
            "strata.env.gui_osworld.OSWorldHTTPClient",
            return_value=mock_client,
        ):
            adapter = OSWorldGUIAdapter(_osworld_config())
        assert adapter.get_screen_size() == (1920, 1080)
        mock_client.post_json.assert_called_once_with("/screen_size", {})

    def test_mismatch_screen_size_raises_config_error(self) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        mock_client = MagicMock()
        mock_client.post_json.return_value = {"width": 1024, "height": 768}
        with (
            patch(
                "strata.env.gui_osworld.OSWorldHTTPClient",
                return_value=mock_client,
            ),
            pytest.raises(ConfigError, match="screen size mismatch"),
        ):
            OSWorldGUIAdapter(_osworld_config())

    def test_server_unreachable_raises_connection_error(self) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        mock_client = MagicMock()
        mock_client.post_json.side_effect = OSWorldConnectionError("POST /screen_size failed")
        with (
            patch(
                "strata.env.gui_osworld.OSWorldHTTPClient",
                return_value=mock_client,
            ),
            pytest.raises(OSWorldConnectionError),
        ):
            OSWorldGUIAdapter(_osworld_config())


class TestMouseActions:
    def test_click_sends_pyautogui_snippet(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.click(100.0, 200.0)  # type: ignore[attr-defined]
        args, _ = client.post_json.call_args
        assert args[0] == "/run_python"
        code = args[1]["code"]
        assert "pyautogui.click" in code
        assert "x=100.0" in code and "y=200.0" in code
        assert "'left'" in code

    def test_click_invalid_button_rejected(self) -> None:
        client = MagicMock()
        adapter = _make_adapter(client)
        with pytest.raises(ValueError, match="invalid mouse button"):
            adapter.click(1.0, 2.0, button="middle-scroll")  # type: ignore[attr-defined]
        client.post_json.assert_not_called()

    def test_double_click(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.double_click(50.0, 60.0)  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.doubleClick" in code

    def test_move_mouse(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.move_mouse(10.0, 20.0)  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "moveTo" in code


class TestKeyboardActions:
    def test_type_text(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.type_text("hello world")  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "_Kbd" in code or "Controller" in code
        assert "hello world" in code

    def test_type_text_uses_raw_string_literal(self) -> None:
        """Backslashes must survive to pyautogui verbatim — the emitted
        run_python snippet must wrap the text in a raw triple-quoted string
        (``r'''...'''``). Regression guard for the ``\\074`` → ``<`` octal
        interpretation bug.
        """
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.type_text("printf '\\074br\\076'")  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "r'''" in code
        assert "\\074br\\076" in code

    def test_type_text_rejects_triple_quote(self) -> None:
        client = MagicMock()
        adapter = _make_adapter(client)
        with pytest.raises(ValueError):
            adapter.type_text("evil '''injection''' attempt")  # type: ignore[attr-defined]
        client.post_json.assert_not_called()

    def test_press_key(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.press_key("enter")  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.press('enter')" in code

    def test_hotkey(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.hotkey("ctrl", "c")  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.hotkey('ctrl', 'c')" in code


class TestScroll:
    def test_vertical_down(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.scroll(0, 300)  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.scroll(-3)" in code

    def test_vertical_up(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.scroll(0, -200)  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.scroll(2)" in code

    def test_horizontal_right(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.scroll(400, 0)  # type: ignore[attr-defined]
        code = client.post_json.call_args.args[1]["code"]
        assert "pyautogui.hscroll(4)" in code

    def test_both_axes_issue_two_calls(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "success"}
        adapter = _make_adapter(client)
        adapter.scroll(100, -100)  # type: ignore[attr-defined]
        assert client.post_json.call_count == 2

    def test_zero_deltas_no_calls(self) -> None:
        client = MagicMock()
        adapter = _make_adapter(client)
        adapter.scroll(0, 0)  # type: ignore[attr-defined]
        client.post_json.assert_not_called()


class TestRunPythonFailure:
    def test_non_success_status_raises(self) -> None:
        client = MagicMock()
        client.post_json.return_value = {"status": "error", "message": "boom"}
        adapter = _make_adapter(client)
        with pytest.raises(OSWorldConnectionError, match="/run_python failed"):
            adapter.click(10.0, 20.0)  # type: ignore[attr-defined]


class TestCaptureScreen:
    def _png_bytes(self, size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
        img = Image.new("RGB", size, color=color)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_full_screen_returns_bytes(self) -> None:
        client = MagicMock()
        data = self._png_bytes((200, 200), (255, 0, 0))
        client.get_bytes.return_value = data
        adapter = _make_adapter(client)
        result = adapter.capture_screen()  # type: ignore[attr-defined]
        assert result == data
        client.get_bytes.assert_called_once_with("/screenshot")

    def test_empty_body_raises(self) -> None:
        client = MagicMock()
        client.get_bytes.return_value = b""
        adapter = _make_adapter(client)
        with pytest.raises(OSWorldConnectionError, match="empty body"):
            adapter.capture_screen()  # type: ignore[attr-defined]

    def test_region_crops_output(self) -> None:
        client = MagicMock()
        data = self._png_bytes((200, 200), (0, 0, 255))
        client.get_bytes.return_value = data
        adapter = _make_adapter(client)
        result = adapter.capture_screen(  # type: ignore[attr-defined]
            ScreenRegion(x=10, y=20, width=50, height=60)
        )
        cropped = Image.open(io.BytesIO(result))
        assert cropped.size == (50, 60)

    def test_region_clamped_to_image_bounds(self) -> None:
        client = MagicMock()
        data = self._png_bytes((100, 100), (0, 255, 0))
        client.get_bytes.return_value = data
        adapter = _make_adapter(client)
        result = adapter.capture_screen(  # type: ignore[attr-defined]
            ScreenRegion(x=80, y=80, width=500, height=500)
        )
        cropped = Image.open(io.BytesIO(result))
        assert cropped.size == (20, 20)


class TestMiscProperties:
    def test_dpi_scale_is_1(self) -> None:
        client = MagicMock()
        adapter = _make_adapter(client)
        assert adapter.get_dpi_scale_for_point(100.0, 200.0) == 1.0  # type: ignore[attr-defined]

    def test_get_screen_size(self) -> None:
        client = MagicMock()
        adapter = _make_adapter(client, screen=(1366, 768))
        assert adapter.get_screen_size() == (1366, 768)  # type: ignore[attr-defined]
