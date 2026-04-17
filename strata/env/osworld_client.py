"""OSWorld HTTP client — shared by GUI adapter, recorder, and task runner.

Extracted from ``gui_osworld.py`` so that non-env layers (observability,
scripts) can talk to the OSWorld Docker server without importing the GUI
adapter's internals.
"""

from __future__ import annotations

import json as _json
import urllib.error
import urllib.parse
import urllib.request
from typing import cast

import icontract

from strata.core.errors import OSWorldConnectionError


class OSWorldHTTPClient:
    """Stdlib-based HTTP client for the OSWorld Docker server."""

    @icontract.require(
        lambda base_url: base_url.startswith("http"),
        "base_url must start with http",
    )
    def __init__(self, base_url: str, timeout: float) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
        except urllib.error.URLError as exc:
            raise OSWorldConnectionError(f"POST {path} failed: {exc}") from exc
        if not body:
            return {}
        try:
            parsed = _json.loads(body)
        except _json.JSONDecodeError as exc:
            raise OSWorldConnectionError(f"POST {path} returned non-JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise OSWorldConnectionError(
                f"POST {path} returned non-object JSON: {type(parsed).__name__}"
            )
        return cast(dict[str, object], parsed)

    def post_form_get_bytes(self, path: str, fields: dict[str, str]) -> bytes:
        """POST form-urlencoded data and return raw response bytes."""
        encoded = urllib.parse.urlencode(fields).encode("utf-8")
        req = urllib.request.Request(
            self._base + path,
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return cast(bytes, resp.read())
        except urllib.error.URLError as exc:
            raise OSWorldConnectionError(f"POST(form) {path} failed: {exc}") from exc

    def get_bytes(self, path: str) -> bytes:
        req = urllib.request.Request(self._base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return cast(bytes, resp.read())
        except urllib.error.URLError as exc:
            raise OSWorldConnectionError(f"GET {path} failed: {exc}") from exc

    @icontract.require(lambda command: len(command.strip()) > 0, "command must be non-empty")
    def execute_shell(self, command: str) -> dict[str, object]:
        """Run a shell command inside the container via POST /execute."""
        return self.post_json("/execute", {"command": command, "shell": True})

    @icontract.require(lambda code: len(code.strip()) > 0, "code must be non-empty")
    def run_python(self, code: str) -> dict[str, object]:
        """Run Python code inside the container via POST /run_python."""
        return self.post_json("/run_python", {"code": code})

    def health_check(self) -> bool:
        """Return True if the server is reachable (POST /screen_size)."""
        try:
            self.post_json("/screen_size", {})
            return True
        except Exception:
            return False
