"""Tests for strata.grounding.vision_locator — VLM-based element location."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from strata.core.config import GUIConfig
from strata.core.errors import ElementNotFoundError, SensitiveContentError, VisionLocatorError
from strata.core.types import Coordinate
from strata.grounding.vision_locator import VisionLocator
from strata.llm.provider import ChatResponse


def _default_gui_config(scroll_search: bool = True) -> GUIConfig:
    return GUIConfig(
        lock_timeout=10.0,
        wait_interval=0.01,
        screenshot_without_lock=False,
        enable_scroll_search=scroll_search,
        max_scroll_attempts=5,
        scroll_step_pixels=300,
    )


def _make_gui(w: int = 1920, h: int = 1080) -> MagicMock:
    gui = MagicMock()
    gui.get_screen_size.return_value = (w, h)
    gui.capture_screen.return_value = b"fake_screenshot_png"
    return gui


def _make_router_with_responses(*responses: str) -> MagicMock:
    router = MagicMock()
    side_effects = [
        ChatResponse(
            content=r,
            model="test-vlm",
            usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
            finish_reason="stop",
        )
        for r in responses
    ]
    router.see.side_effect = side_effects
    return router


class TestLocateSuccess:
    def test_click_response(self) -> None:
        resp = json.dumps({"action_type": "click", "x": 150, "y": 300, "confidence": 0.95})
        router = _make_router_with_responses(resp)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        result = locator.locate("Submit button")
        assert result == Coordinate(x=150.0, y=300.0)


class TestLocateVLMError:
    def test_vlm_network_error(self) -> None:
        router = MagicMock()
        router.see.side_effect = RuntimeError("network timeout")
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(VisionLocatorError, match="VLM call failed"):
            locator.locate("any element")


class TestLocateSensitiveRejected:
    def test_password_in_description(self) -> None:
        router = MagicMock()
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(SensitiveContentError):
            locator.locate("type my password")


class TestLocateWithScrollFindsAfterScroll:
    def test_scroll_then_click(self) -> None:
        responses = [
            json.dumps({"action_type": "scroll", "direction": "down"}),
            json.dumps({"action_type": "click", "x": 500, "y": 400, "confidence": 0.9}),
        ]
        router = _make_router_with_responses(*responses)
        gui = _make_gui()
        locator = VisionLocator(gui, router, _default_gui_config())
        result = locator.locate_with_scroll("hidden element")
        assert result == Coordinate(x=500.0, y=400.0)
        gui.scroll.assert_called_once()


class TestLocateWithScrollMaxAttempts:
    def test_always_not_found(self) -> None:
        responses = [json.dumps({"action_type": "not_found"}) for _ in range(10)]
        router = _make_router_with_responses(*responses)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(ElementNotFoundError):
            locator.locate_with_scroll("nonexistent element")


class TestLocateWithScrollNextPage:
    def test_next_page_click(self) -> None:
        responses = [
            json.dumps({"action_type": "next_page", "x": 800, "y": 550}),
            json.dumps({"action_type": "click", "x": 200, "y": 100, "confidence": 0.88}),
        ]
        router = _make_router_with_responses(*responses)
        gui = _make_gui()
        locator = VisionLocator(gui, router, _default_gui_config())
        result = locator.locate_with_scroll("item on next page")
        assert result == Coordinate(x=200.0, y=100.0)
        gui.click.assert_called_once_with(800.0, 550.0)


class TestLocateWithScrollDisabled:
    def test_degrades_to_single_locate(self) -> None:
        resp = json.dumps({"action_type": "click", "x": 100, "y": 200, "confidence": 0.9})
        router = _make_router_with_responses(resp)
        config = _default_gui_config(scroll_search=False)
        locator = VisionLocator(_make_gui(), router, config)
        result = locator.locate_with_scroll("element")
        assert result == Coordinate(x=100.0, y=200.0)


class TestParseVLMResponseErrorWrapping:
    def test_non_numeric_x_raises_vision_locator_error(self) -> None:
        resp = json.dumps({"action_type": "click", "x": "left", "y": 10})
        router = _make_router_with_responses(resp)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(VisionLocatorError, match="non-numeric"):
            locator.locate("button")

    def test_non_numeric_confidence_raises(self) -> None:
        resp = json.dumps({"action_type": "click", "x": 1, "y": 2, "confidence": "high"})
        router = _make_router_with_responses(resp)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(VisionLocatorError, match="non-numeric"):
            locator.locate("button")

    def test_invalid_scroll_direction_rejected(self) -> None:
        resp = json.dumps({"action_type": "scroll", "direction": "sideways"})
        router = _make_router_with_responses(resp)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(VisionLocatorError, match="invalid scroll direction"):
            locator.locate_with_scroll("element")

    def test_scroll_without_direction_rejected(self) -> None:
        resp = json.dumps({"action_type": "scroll"})
        router = _make_router_with_responses(resp)
        locator = VisionLocator(_make_gui(), router, _default_gui_config())
        with pytest.raises(VisionLocatorError, match="invalid scroll direction"):
            locator.locate_with_scroll("element")


class TestLiveScreenBounds:
    def test_screen_size_queried_on_each_locate(self) -> None:
        """Bounds contract uses live get_screen_size, not a ctor-cached value."""
        resp = json.dumps({"action_type": "click", "x": 10, "y": 10, "confidence": 0.9})
        router = _make_router_with_responses(resp)
        gui = _make_gui(w=1920, h=1080)
        locator = VisionLocator(gui, router, _default_gui_config())
        call_count_before = gui.get_screen_size.call_count
        locator.locate("button")
        assert gui.get_screen_size.call_count > call_count_before
