"""Tests for strata.env.protocols — Protocol conformance checks."""

from __future__ import annotations

from strata.env.linux.app_manager import LinuxAppManagerAdapter
from strata.env.linux.gui import LinuxGUIAdapter
from strata.env.macos.app_manager import MacOSAppManagerAdapter
from strata.env.macos.gui import MacOSGUIAdapter
from strata.env.macos.system import MacOSSystemAdapter
from strata.env.protocols import (
    IAppManagerAdapter,
    IGUIAdapter,
    ISystemAdapter,
)


class TestProtocolRuntimeCheckable:
    def test_linux_gui_is_iguiadapter(self) -> None:
        assert isinstance(LinuxGUIAdapter(), IGUIAdapter)

    def test_macos_gui_is_iguiadapter(self) -> None:
        assert isinstance(MacOSGUIAdapter(), IGUIAdapter)

    def test_linux_app_manager_is_iappmanager(self) -> None:
        assert isinstance(LinuxAppManagerAdapter(), IAppManagerAdapter)

    def test_macos_app_manager_is_iappmanager(self) -> None:
        assert isinstance(MacOSAppManagerAdapter(), IAppManagerAdapter)

    def test_macos_system_is_isystem(self) -> None:
        assert isinstance(MacOSSystemAdapter(), ISystemAdapter)
