"""macOS app manager — construction-time fail-fast stub."""

from __future__ import annotations

from collections.abc import Sequence

from strata.core.errors import UnsupportedPlatformError
from strata.core.types import AppInfo


class MacOSAppManagerAdapter:
    def __init__(self) -> None:
        raise UnsupportedPlatformError(
            "macOS native app manager not implemented"
        )

    def launch_app(self, app_name: str, args: Sequence[str] | None = None) -> str:
        raise NotImplementedError("macOS support planned")

    def close_app(self, app_identifier: str) -> None:
        raise NotImplementedError("macOS support planned")

    def get_running_apps(self) -> Sequence[AppInfo]:
        raise NotImplementedError("macOS support planned")

    def switch_to_app(self, app_identifier: str) -> None:
        raise NotImplementedError("macOS support planned")
