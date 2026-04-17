"""EnvironmentFactory — platform dispatch for adapter creation.

# CONVENTION: GUI backend abstraction — IGUIAdapter Protocol is the
# extension point for platform-specific backends:
#   * osworld.enabled=true → OSWorldGUIAdapter (any platform, Docker)
#   * osworld.enabled=false + Linux + DISPLAY → LinuxGUIAdapter (Phase 12+)
#   * macOS → MacOSGUIAdapter (Phase 12+)
# Non-supported paths raise UnsupportedPlatformError with actionable message.
"""

from __future__ import annotations

import sys

import icontract

from strata.core.config import StrataConfig
from strata.core.errors import UnsupportedPlatformError
from strata.env.protocols import EnvironmentBundle


class EnvironmentFactory:
    @staticmethod
    @icontract.ensure(
        lambda result: isinstance(result, EnvironmentBundle),
        "must return EnvironmentBundle",
    )
    def create(config: StrataConfig) -> EnvironmentBundle:
        """Build an EnvironmentBundle for the current platform.

        Dispatch:
        - osworld.enabled=True → OSWorldGUIAdapter (any platform)
        - osworld.enabled=False + Linux → LinuxGUIAdapter (stub, Phase 12+)
        - otherwise → UnsupportedPlatformError with actionable guidance
        """
        if sys.platform == "linux":
            return _create_linux(config)

        # macOS extension point (Phase 12+)
        raise UnsupportedPlatformError(
            f"Platform {sys.platform!r} not yet supported. "
            f"Options: (1) set osworld.enabled=true in config.toml; "
            f"(2) run on Linux; (3) wait for macOS backend (Phase 12+)."
        )


def _create_linux(config: StrataConfig) -> EnvironmentBundle:
    from strata.core.sandbox import SandboxGuard
    from strata.env.filesystem import SandboxedFileSystemAdapter
    from strata.env.linux.app_manager import LinuxAppManagerAdapter
    from strata.env.linux.system import LinuxSystemAdapter
    from strata.env.pty_terminal import PTYTerminalAdapter

    guard = SandboxGuard(config.sandbox)

    gui: object
    if config.osworld.enabled:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        gui = OSWorldGUIAdapter(config.osworld)
    else:
        from strata.env.linux.gui import LinuxGUIAdapter

        gui = LinuxGUIAdapter()

    return EnvironmentBundle(
        gui=gui,
        terminal=PTYTerminalAdapter(config.terminal),
        filesystem=SandboxedFileSystemAdapter(guard, config.trash_dir),
        app_manager=LinuxAppManagerAdapter(),
        system=LinuxSystemAdapter(),
    )
