"""Linux app manager stub (Phase 6 implementation)."""

from __future__ import annotations

from collections.abc import Sequence

from strata.core.types import AppInfo


class LinuxAppManagerAdapter:
    def launch_app(self, app_name: str, args: Sequence[str] | None = None) -> str:
        raise NotImplementedError("Phase 6")

    def close_app(self, app_identifier: str) -> None:
        raise NotImplementedError("Phase 6")

    def get_running_apps(self) -> Sequence[AppInfo]:
        raise NotImplementedError("Phase 6")

    def switch_to_app(self, app_identifier: str) -> None:
        raise NotImplementedError("Phase 6")
