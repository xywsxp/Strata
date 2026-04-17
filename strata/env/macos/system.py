"""macOS system adapter — construction-time fail-fast stub."""

from __future__ import annotations

from strata.core.errors import UnsupportedPlatformError


class MacOSSystemAdapter:
    def __init__(self) -> None:
        raise UnsupportedPlatformError("macOS native system adapter not implemented")

    def get_clipboard_text(self) -> str:
        raise NotImplementedError("macOS support planned")

    def set_clipboard_text(self, text: str) -> None:
        raise NotImplementedError("macOS support planned")

    def get_environment_variable(self, name: str) -> str | None:
        raise NotImplementedError("macOS support planned")

    def set_environment_variable(self, name: str, value: str) -> None:
        raise NotImplementedError("macOS support planned")

    def get_cwd(self) -> str:
        raise NotImplementedError("macOS support planned")

    def set_cwd(self, path: str) -> None:
        raise NotImplementedError("macOS support planned")
