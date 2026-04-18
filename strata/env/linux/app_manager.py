"""Linux app manager stub.

# CONVENTION: 仅支持 OSWorld — 本地 Linux app 管理待 Phase 12+ 实现。
# 当前 Strata 的 GUI 交互全部通过 OSWorldGUIAdapter 完成，不依赖本地
# 进程管理。所有方法抛 NotImplementedError 而非 UnsupportedPlatformError，
# 因为这不是平台不支持，而是功能尚未实现。
"""

from __future__ import annotations

from collections.abc import Sequence

from strata.core.types import AppInfo


class LinuxAppManagerAdapter:
    # CONVENTION: 仅支持 OSWorld — 本地 Linux app 管理待 Phase 12+ 实现
    def launch_app(self, app_name: str, args: Sequence[str] | None = None) -> str:
        raise NotImplementedError("Phase 12+: local Linux app management not yet implemented")

    def close_app(self, app_identifier: str) -> None:
        raise NotImplementedError("Phase 12+: local Linux app management not yet implemented")

    def get_running_apps(self) -> Sequence[AppInfo]:
        raise NotImplementedError("Phase 12+: local Linux app management not yet implemented")

    def switch_to_app(self, app_identifier: str) -> None:
        raise NotImplementedError("Phase 12+: local Linux app management not yet implemented")
