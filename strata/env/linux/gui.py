"""Linux native GUI adapter — construction-time fail-fast stub.

# CONVENTION: LinuxGUIAdapter stub — Phase 12+ will implement a real
# pyautogui/xdotool backend for native Linux desktop automation.
# Headless servers must use OSWorld (osworld.enabled=true).
# Future macOS support will follow the same IGUIAdapter Protocol.
# IA11yAdapter design is ABANDONED — perception is permanently pure VLM.
"""

from __future__ import annotations

from strata.core.errors import UnsupportedPlatformError
from strata.core.types import ScreenRegion


class LinuxGUIAdapter:
    """Stub: raises on instantiation with actionable guidance."""

    def __init__(self) -> None:
        raise UnsupportedPlatformError(
            "Native Linux GUI backend not yet implemented. "
            "Options: (1) set osworld.enabled=true in config.toml "
            "and start an OSWorld Docker container; "
            "(2) wait for Phase 12+ native pyautogui backend."
        )

    def click(self, x: float, y: float, button: str = "left") -> None:
        raise NotImplementedError

    def double_click(self, x: float, y: float) -> None:
        raise NotImplementedError

    def move_mouse(self, x: float, y: float) -> None:
        raise NotImplementedError

    def type_text(self, text: str, interval: float = 0.05) -> None:
        raise NotImplementedError

    def press_key(self, key: str) -> None:
        raise NotImplementedError

    def hotkey(self, *keys: str) -> None:
        raise NotImplementedError

    def scroll(self, delta_x: int, delta_y: int) -> None:
        raise NotImplementedError

    def get_screen_size(self) -> tuple[int, int]:
        raise NotImplementedError

    def capture_screen(self, region: ScreenRegion | None = None) -> bytes:
        raise NotImplementedError

    def get_dpi_scale_for_point(self, x: float, y: float) -> float:
        raise NotImplementedError
