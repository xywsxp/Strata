"""Linux system adapter — clipboard, env vars, CWD."""

from __future__ import annotations

import os
import shutil
import subprocess

import icontract

from strata.core.errors import StrataEnvironmentError


class LinuxSystemAdapter:
    """ISystemAdapter implementation for Linux (xclip/xsel clipboard).

    # CONVENTION: clipboard tool lookup is **lazy** — we detect xclip/xsel on
    # first clipboard call, not in ``__init__``. A headless server / OSWorld
    # setup where the *local* machine lacks xclip must still be able to
    # construct the bundle; the error surface is pushed to the call site that
    # actually needs the clipboard.
    """

    def __init__(self) -> None:
        self._clip_cmd: str | None = None
        self._clip_probed: bool = False
        self._is_xclip: bool = False

    def _require_clip(self) -> str:
        if not self._clip_probed:
            self._clip_cmd = shutil.which("xclip") or shutil.which("xsel")
            self._clip_probed = True
            if self._clip_cmd is not None:
                self._is_xclip = "xclip" in self._clip_cmd
        if self._clip_cmd is None:
            raise StrataEnvironmentError("clipboard tool not found: install xclip or xsel")
        return self._clip_cmd

    def get_clipboard_text(self) -> str:
        clip = self._require_clip()
        if self._is_xclip:
            cmd = [clip, "-selection", "clipboard", "-o"]
        else:
            cmd = [clip, "--clipboard", "--output"]
        try:
            result: str = subprocess.check_output(cmd, text=True, timeout=5.0)
            return result
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""

    def set_clipboard_text(self, text: str) -> None:
        clip = self._require_clip()
        if self._is_xclip:
            cmd = [clip, "-selection", "clipboard", "-i"]
        else:
            cmd = [clip, "--clipboard", "--input"]
        subprocess.run(cmd, input=text, text=True, check=True, timeout=5.0)

    def get_environment_variable(self, name: str) -> str | None:
        return os.environ.get(name)

    def set_environment_variable(self, name: str, value: str) -> None:
        os.environ[name] = value

    def get_cwd(self) -> str:
        return os.getcwd()

    @icontract.require(lambda path: os.path.isdir(path), "path must be an existing directory")
    def set_cwd(self, path: str) -> None:
        os.chdir(path)
