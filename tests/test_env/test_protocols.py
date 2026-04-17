"""Tests for strata.env.protocols — Protocol conformance + stub fail-fast.

Stubs (`LinuxGUIAdapter`, `MacOS*Adapter`) fail on construction with
``UnsupportedPlatformError`` — verifies the construction-time guard closes the
``@runtime_checkable`` duck-typing loophole where a stub instance would
erroneously pass ``isinstance(..., IGUIAdapter)``.
"""

from __future__ import annotations

import pytest

from strata.core.errors import UnsupportedPlatformError
from strata.env.linux.app_manager import LinuxAppManagerAdapter
from strata.env.linux.gui import LinuxGUIAdapter
from strata.env.macos.app_manager import MacOSAppManagerAdapter
from strata.env.macos.gui import MacOSGUIAdapter
from strata.env.macos.system import MacOSSystemAdapter
from strata.env.protocols import IAppManagerAdapter


class TestProtocolRuntimeCheckable:
    def test_linux_app_manager_is_iappmanager(self) -> None:
        assert isinstance(LinuxAppManagerAdapter(), IAppManagerAdapter)


class TestStubFailFast:
    def test_linux_gui_construction_fails(self) -> None:
        with pytest.raises(UnsupportedPlatformError, match="Linux native GUI"):
            LinuxGUIAdapter()

    def test_macos_gui_construction_fails(self) -> None:
        with pytest.raises(UnsupportedPlatformError, match="macOS native GUI"):
            MacOSGUIAdapter()

    def test_macos_app_manager_construction_fails(self) -> None:
        with pytest.raises(UnsupportedPlatformError, match="macOS"):
            MacOSAppManagerAdapter()

    def test_macos_system_construction_fails(self) -> None:
        with pytest.raises(UnsupportedPlatformError, match="macOS"):
            MacOSSystemAdapter()
