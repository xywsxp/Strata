"""Tests for strata.env.linux.system — Linux system adapter."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from strata.core.errors import StrataEnvironmentError

_HAS_CLIPBOARD = shutil.which("xclip") is not None or shutil.which("xsel") is not None


class TestConstructionDoesNotRequireClipboard:
    def test_construction_succeeds_without_clipboard_tool(self) -> None:
        """# CONVENTION: ctor is lazy — clipboard tool lookup deferred to
        the first clipboard call so headless / OSWorld setups can build the
        bundle even without xclip/xsel installed."""
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        assert adapter is not None


class TestClipboardCallFailsWithoutTool:
    @pytest.mark.skipif(_HAS_CLIPBOARD, reason="clipboard tool is available")
    def test_get_clipboard_without_tool_raises(self) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        with pytest.raises(StrataEnvironmentError, match="clipboard tool not found"):
            adapter.get_clipboard_text()

    @pytest.mark.skipif(_HAS_CLIPBOARD, reason="clipboard tool is available")
    def test_set_clipboard_without_tool_raises(self) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        with pytest.raises(StrataEnvironmentError, match="clipboard tool not found"):
            adapter.set_clipboard_text("foo")


class TestEnvVarRoundtrip:
    def test_set_get_env(self) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        adapter.set_environment_variable("_STRATA_TEST_VAR", "test_value")
        assert adapter.get_environment_variable("_STRATA_TEST_VAR") == "test_value"
        os.environ.pop("_STRATA_TEST_VAR", None)


class TestCwdRoundtrip:
    def test_set_get_cwd(self, tmp_path: Path) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        original = os.getcwd()
        try:
            adapter.set_cwd(str(tmp_path))
            assert adapter.get_cwd() == str(tmp_path)
        finally:
            os.chdir(original)
