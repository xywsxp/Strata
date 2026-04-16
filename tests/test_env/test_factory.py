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
        with pytest.raises(UnsupportedPlatformError, match="当前仅支持 Linux"):
            from strata.core.config import get_default_config

            EnvironmentFactory.create(get_default_config())

    @patch("strata.env.factory.sys")
    def test_unsupported_darwin(self, mock_sys: object) -> None:
        import unittest.mock as um

        assert isinstance(mock_sys, um.MagicMock)
        mock_sys.platform = "darwin"
        with pytest.raises(UnsupportedPlatformError, match="当前仅支持 Linux"):
            from strata.core.config import get_default_config

            EnvironmentFactory.create(get_default_config())
