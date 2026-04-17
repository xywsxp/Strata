"""macOS GUI adapter — construction-time fail-fast stub.

# CONVENTION: 构造期 fail-fast 抛 UnsupportedPlatformError — 配合 Protocol
# @runtime_checkable 避免鸭子类型漏洞。
"""

from __future__ import annotations

from strata.core.errors import UnsupportedPlatformError
from strata.core.types import ScreenRegion


class MacOSGUIAdapter:
    def __init__(self) -> None:
        raise UnsupportedPlatformError(
            "macOS native GUI not implemented; "
            "use OSWorldGUIAdapter or set osworld.enabled=true"
        )

    def click(self, x: float, y: float, button: str = "left") -> None:
        raise NotImplementedError("macOS support planned")

    def double_click(self, x: float, y: float) -> None:
        raise NotImplementedError("macOS support planned")

    def move_mouse(self, x: float, y: float) -> None:
        raise NotImplementedError("macOS support planned")

    def type_text(self, text: str, interval: float = 0.05) -> None:
        raise NotImplementedError("macOS support planned")

    def press_key(self, key: str) -> None:
        raise NotImplementedError("macOS support planned")

    def hotkey(self, *keys: str) -> None:
        raise NotImplementedError("macOS support planned")

    def scroll(self, delta_x: int, delta_y: int) -> None:
        raise NotImplementedError("macOS support planned")

    def get_screen_size(self) -> tuple[int, int]:
        raise NotImplementedError("macOS support planned")

    def capture_screen(self, region: ScreenRegion | None = None) -> bytes:
        raise NotImplementedError("macOS support planned")

    def get_dpi_scale_for_point(self, x: float, y: float) -> float:
        raise NotImplementedError("macOS support planned")
