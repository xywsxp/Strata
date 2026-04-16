"""Tests for strata.env.linux.system — Linux system adapter."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from strata.core.errors import EnvironmentError as StrataEnvironmentError

_HAS_CLIPBOARD = shutil.which("xclip") is not None or shutil.which("xsel") is not None


class TestInitFailsWithoutClipboard:
    @pytest.mark.skipif(_HAS_CLIPBOARD, reason="clipboard tool is available")
    def test_no_clipboard_tool_raises(self) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        with pytest.raises(StrataEnvironmentError, match="clipboard tool not found"):
            LinuxSystemAdapter()


@pytest.mark.skipif(not _HAS_CLIPBOARD, reason="no clipboard tool installed")
class TestEnvVarRoundtrip:
    def test_set_get_env(self) -> None:
        from strata.env.linux.system import LinuxSystemAdapter

        adapter = LinuxSystemAdapter()
        adapter.set_environment_variable("_STRATA_TEST_VAR", "test_value")
        assert adapter.get_environment_variable("_STRATA_TEST_VAR") == "test_value"
        os.environ.pop("_STRATA_TEST_VAR", None)


@pytest.mark.skipif(not _HAS_CLIPBOARD, reason="no clipboard tool installed")
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
