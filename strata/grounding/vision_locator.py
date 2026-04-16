"""VisionLocator — pure VLM UI element location with scroll search.

No accessibility API is used. All UI element positioning relies on
screenshot + VLM inference.
"""

from __future__ import annotations

import json
import time
from typing import Final

import icontract

from strata.core.config import GUIConfig
from strata.core.errors import ElementNotFoundError, SensitiveContentError, VisionLocatorError
from strata.core.types import Coordinate, VisionResponse
from strata.env.protocols import IGUIAdapter
from strata.grounding.filter import contains_sensitive
from strata.llm.provider import ChatMessage
from strata.llm.router import LLMRouter

_VLM_SYSTEM_PROMPT: Final[str] = """\
You are a visual UI element locator. Given a screenshot and an element description,
identify the element and respond with a JSON object:
- If found: {"action_type": "click", "x": <int>, "y": <int>, "confidence": <0-1>}
- If need to scroll: {"action_type": "scroll", "direction": "up"|"down"|"left"|"right"}
- If need next page: {"action_type": "next_page", "x": <int>, "y": <int>}
- If loading: {"action_type": "not_found"}
- If not found: {"action_type": "not_found"}
Output ONLY valid JSON, no other text.
"""


class VisionLocator:
    """Pure VLM element locator with optional scroll-search loop."""

    def __init__(self, gui: IGUIAdapter, router: LLMRouter, config: GUIConfig) -> None:
        self._gui = gui
        self._router = router
        self._config = config
        self._screen_w, self._screen_h = gui.get_screen_size()
        self._next_page_cache: Coordinate | None = None

    @icontract.require(
        lambda description: len(description.strip()) > 0,
        "description must be non-empty",
    )
    @icontract.require(
        lambda description: not contains_sensitive(description),
        "description must not contain sensitive information",
        error=lambda description: SensitiveContentError(
            f"sensitive content in description: {description!r}"
        ),
    )
    @icontract.ensure(
        lambda self, result: 0 <= result.x < self._screen_w and 0 <= result.y < self._screen_h,
        "returned coordinate must be within screen bounds",
    )
    def locate(self, description: str, role: str | None = None) -> Coordinate:
        """Single-shot locate: screenshot -> VLM -> coordinate."""
        screenshot = self._gui.capture_screen()
        response = self._call_vlm(screenshot, description, role)
        if response.action_type != "click" or response.coordinate is None:
            raise VisionLocatorError(
                f"VLM did not return a click action for {description!r}, got {response.action_type}"
            )
        return response.coordinate

    @icontract.require(
        lambda description: len(description.strip()) > 0,
        "description must be non-empty",
    )
    @icontract.require(
        lambda description: not contains_sensitive(description),
        "description must not contain sensitive information",
        error=lambda description: SensitiveContentError(
            f"sensitive content in description: {description!r}"
        ),
    )
    @icontract.ensure(
        lambda self, result: 0 <= result.x < self._screen_w and 0 <= result.y < self._screen_h,
        "returned coordinate must be within screen bounds",
    )
    def locate_with_scroll(
        self,
        description: str,
        role: str | None = None,
        timeout: float = 30.0,
    ) -> Coordinate:
        """Locate with scroll search. Falls back to single locate if scroll disabled."""
        if not self._config.enable_scroll_search:
            return self.locate(description, role)

        start = time.monotonic()
        scroll_count = 0

        while scroll_count < self._config.max_scroll_attempts:
            if time.monotonic() - start > timeout:
                raise ElementNotFoundError(f"timeout ({timeout}s) locating {description!r}")

            screenshot = self._gui.capture_screen()
            response = self._call_vlm(screenshot, description, role)

            if response.action_type == "click" and response.coordinate is not None:
                return response.coordinate

            if response.action_type == "scroll":
                self._execute_scroll_action(response)
                scroll_count += 1
                continue

            if response.action_type == "next_page" and response.coordinate is not None:
                self._next_page_cache = response.coordinate
                self._gui.click(response.coordinate.x, response.coordinate.y)
                time.sleep(self._config.wait_interval)
                scroll_count += 1
                continue

            if response.action_type == "not_found":
                if scroll_count < self._config.max_scroll_attempts - 1:
                    self._gui.scroll(0, self._config.scroll_step_pixels)
                    scroll_count += 1
                    continue
                break

        raise ElementNotFoundError(
            f"element {description!r} not found after {scroll_count} scroll attempts"
        )

    def _call_vlm(
        self,
        screenshot: bytes,
        description: str,
        role: str | None,
    ) -> VisionResponse:
        """Core VLM call: build message, call router.see, parse response."""
        user_content = f"Find the UI element: {description}"
        if role:
            user_content += f" (role: {role})"

        messages = [
            ChatMessage(role="system", content=_VLM_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content, images=(screenshot,)),
        ]

        try:
            response = self._router.see(messages, json_mode=True, temperature=0.1)
        except Exception as exc:
            raise VisionLocatorError(f"VLM call failed: {exc}") from exc

        return self._parse_vlm_response(response.content)

    def _parse_vlm_response(self, raw: str) -> VisionResponse:
        """Parse JSON response from VLM into VisionResponse."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VisionLocatorError(f"VLM returned invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise VisionLocatorError(f"expected JSON object, got {type(data).__name__}")

        action_type = data.get("action_type", "not_found")
        if action_type not in ("click", "scroll", "next_page", "not_found"):
            raise VisionLocatorError(f"invalid action_type: {action_type!r}")

        coordinate: Coordinate | None = None
        if "x" in data and "y" in data:
            coordinate = Coordinate(x=float(data["x"]), y=float(data["y"]))

        scroll_direction = data.get("direction")
        confidence = float(data.get("confidence", 0.0))

        return VisionResponse(
            action_type=action_type,
            coordinate=coordinate,
            scroll_direction=scroll_direction,
            confidence=confidence,
            raw_text=raw,
        )

    def _execute_scroll_action(self, response: VisionResponse) -> None:
        """Execute a scroll action based on VisionResponse direction."""
        step = self._config.scroll_step_pixels
        direction = response.scroll_direction or "down"
        dx, dy = 0, 0
        if direction == "down":
            dy = step
        elif direction == "up":
            dy = -step
        elif direction == "right":
            dx = step
        elif direction == "left":
            dx = -step
        self._gui.scroll(dx, dy)
