"""Tests for strata.env.factory — EnvironmentFactory platform dispatch."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from strata.core.errors import UnsupportedPlatformError
from strata.env.factory import EnvironmentFactory


class TestFactoryPlatformCheck:
    @patch("strata.env.factory.sys")
    def test_unsupported_win32(self, mock_sys: object) -> None:
        import unittest.mock as um

        assert isinstance(mock_sys, um.MagicMock)
        mock_sys.platform = "win32"
        with pytest.raises(UnsupportedPlatformError, match="not yet supported"):
            from strata.core.config import get_default_config

            EnvironmentFactory.create(get_default_config())

    @patch("strata.env.factory.sys")
    def test_unsupported_darwin(self, mock_sys: object) -> None:
        import unittest.mock as um

        assert isinstance(mock_sys, um.MagicMock)
        mock_sys.platform = "darwin"
        with pytest.raises(UnsupportedPlatformError, match="not yet supported"):
            from strata.core.config import get_default_config

            EnvironmentFactory.create(get_default_config())


class TestFactoryLinuxStubFailFast:
    def test_linux_without_osworld_raises_on_gui_stub(self) -> None:
        """On Linux with osworld.enabled=false, LinuxGUIAdapter stub triggers
        UnsupportedPlatformError at construction time — factory must propagate.
        """
        import sys

        if sys.platform != "linux":
            pytest.skip("Linux-only check")
        from strata.core.config import get_default_config

        with pytest.raises(UnsupportedPlatformError, match="Native Linux GUI"):
            EnvironmentFactory.create(get_default_config())
