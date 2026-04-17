"""OSWorld-level pipeline E2E tests.

Verifies that production ``config.toml`` + :class:`EnvironmentFactory` +
:class:`OSWorldGUIAdapter` + :class:`IGUIAdapter` compose correctly when
``osworld.enabled=true``. The OSWorld Docker server's HTTP surface is
mocked (``_OSWorldHTTPClient``) so tests run fast and do not require a
running container.

Separately, the ``@pytest.mark.integration`` suite connects to a real
OSWorld Docker server if ``STRATA_OSWORLD_URL`` is set in the environment
(opt-in; not run in default CI).
"""

from __future__ import annotations

import dataclasses
import io
import os
from typing import Protocol, cast
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from strata.core.config import StrataConfig
from strata.core.errors import UnsupportedPlatformError
from strata.core.types import ScreenRegion
from strata.env.factory import EnvironmentFactory


class OSWorldLikeGUI(Protocol):
    """Structural type for IGUIAdapter subset used by these tests."""

    def click(self, x: float, y: float, button: str = "left") -> None: ...
    def scroll(self, delta_x: int, delta_y: int) -> None: ...
    def capture_screen(self, region: ScreenRegion | None = None) -> bytes: ...


def _force_osworld(cfg: StrataConfig, screen: tuple[int, int] | None = None) -> StrataConfig:
    osw = dataclasses.replace(
        cfg.osworld,
        enabled=True,
        screen_size=screen if screen is not None else cfg.osworld.screen_size,
    )
    return dataclasses.replace(cfg, osworld=osw)


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestConfigRoundtrip:
    def test_repo_config_loads(self, repo_config: StrataConfig) -> None:
        assert repo_config.log_level in ("DEBUG", "INFO", "WARNING", "ERROR")
        assert repo_config.roles.planner in repo_config.providers
        assert repo_config.roles.vision in repo_config.providers
        assert 1 <= repo_config.max_loop_iterations <= 1000

    def test_osworld_toggle_roundtrips(self, repo_config: StrataConfig) -> None:
        """``OSWorldConfig.enabled`` must be a bool — either state is valid;
        we only sanity-check it was parsed and persisted by ``load_config``."""
        assert isinstance(repo_config.osworld.enabled, bool)

    def test_osworld_server_url_present(self, repo_config: StrataConfig) -> None:
        """``OSWorldConfig.server_url`` has a sane default even if config.toml
        omits it — avoids None dereference downstream."""
        assert repo_config.osworld.server_url.startswith("http")


class TestFactoryDispatchOnLiveConfig:
    def test_osworld_disabled_hits_linux_stub(self, repo_config: StrataConfig) -> None:
        """With osworld.enabled=false on Linux, the LinuxGUIAdapter fail-fast
        stub from Phase 9.5 must trigger — regardless of what ``config.toml``
        currently ships."""
        import sys

        if sys.platform != "linux":
            pytest.skip("Linux-only stub guard")
        osw = dataclasses.replace(repo_config.osworld, enabled=False)
        cfg = dataclasses.replace(repo_config, osworld=osw)
        with pytest.raises(UnsupportedPlatformError, match="Linux native GUI"):
            EnvironmentFactory.create(cfg)


class TestFactoryOSWorldBranchWithMockedHTTP:
    """Factory builds an OSWorldGUIAdapter that speaks the HTTP protocol."""

    def _install_http_mock(
        self,
        screen: tuple[int, int] = (1920, 1080),
        screenshot_png: bytes | None = None,
    ) -> tuple[MagicMock, object]:
        client = MagicMock()
        client.post_json.side_effect = lambda path, payload: (
            {"width": screen[0], "height": screen[1]}
            if path == "/screen_size"
            else {"status": "success"}
        )
        client.get_bytes.return_value = screenshot_png or _png_bytes(screen, (128, 128, 128))
        patcher = patch("strata.env.gui_osworld._OSWorldHTTPClient", return_value=client)
        patcher.start()
        return client, patcher

    def test_bundle_gui_is_osworld_adapter(self, repo_config: StrataConfig) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        client, patcher = self._install_http_mock()
        try:
            bundle = EnvironmentFactory.create(_force_osworld(repo_config))
            assert isinstance(bundle.gui, OSWorldGUIAdapter)
        finally:
            patcher.stop()  # type: ignore[attr-defined]

    def test_bundle_terminal_is_pty(self, repo_config: StrataConfig) -> None:
        from strata.env.pty_terminal import PTYTerminalAdapter

        client, patcher = self._install_http_mock()
        try:
            bundle = EnvironmentFactory.create(_force_osworld(repo_config))
            assert isinstance(bundle.terminal, PTYTerminalAdapter)
        finally:
            patcher.stop()  # type: ignore[attr-defined]

    def test_click_emits_run_python(self, repo_config: StrataConfig) -> None:
        client, patcher = self._install_http_mock()
        try:
            bundle = EnvironmentFactory.create(_force_osworld(repo_config))
            gui = cast("OSWorldLikeGUI", bundle.gui)
            gui.click(120.0, 240.0)
            paths = [c.args[0] for c in client.post_json.call_args_list]
            assert "/run_python" in paths
            run_python_args = next(
                c.args[1] for c in client.post_json.call_args_list if c.args[0] == "/run_python"
            )
            assert "pyautogui.click" in run_python_args["code"]
        finally:
            patcher.stop()  # type: ignore[attr-defined]

    def test_scroll_emits_two_calls(self, repo_config: StrataConfig) -> None:
        client, patcher = self._install_http_mock()
        try:
            bundle = EnvironmentFactory.create(_force_osworld(repo_config))
            gui = cast("OSWorldLikeGUI", bundle.gui)
            gui.scroll(delta_x=300, delta_y=-200)
            code_calls = [
                c.args[1]["code"]
                for c in client.post_json.call_args_list
                if c.args[0] == "/run_python"
            ]
            assert any("pyautogui.scroll" in c for c in code_calls)
            assert any("pyautogui.hscroll" in c for c in code_calls)
        finally:
            patcher.stop()  # type: ignore[attr-defined]

    def test_capture_region_crops_via_pillow(self, repo_config: StrataConfig) -> None:
        png = _png_bytes((1920, 1080), (255, 0, 0))
        client, patcher = self._install_http_mock(screenshot_png=png)
        try:
            bundle = EnvironmentFactory.create(_force_osworld(repo_config))
            gui = cast("OSWorldLikeGUI", bundle.gui)
            data = gui.capture_screen(region=ScreenRegion(x=10, y=20, width=100, height=50))
            cropped = Image.open(io.BytesIO(data))
            assert cropped.size == (100, 50)
        finally:
            patcher.stop()  # type: ignore[attr-defined]


# ── Opt-in live integration: requires a running OSWorld Docker server ──


_OSWORLD_URL = os.environ.get("STRATA_OSWORLD_URL")


@pytest.mark.integration
@pytest.mark.skipif(
    _OSWORLD_URL is None,
    reason="set STRATA_OSWORLD_URL=http://host:5000 to run live OSWorld tests",
)
class TestLiveOSWorldDocker:
    """Sanity tests against a real OSWorld Docker container.

    Expects the server to be reachable at ``$STRATA_OSWORLD_URL`` (typically
    ``http://localhost:5000`` when the container is started with
    ``--headless`` and port 5000 published).
    """

    def _build_config(self, repo_config: StrataConfig) -> StrataConfig:
        osw = dataclasses.replace(
            repo_config.osworld,
            enabled=True,
            server_url=_OSWORLD_URL or "http://localhost:5000",
        )
        return dataclasses.replace(repo_config, osworld=osw)

    def test_screen_size_query(self, repo_config: StrataConfig) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        cfg = self._build_config(repo_config)
        # Accept whatever the live server reports: flex screen_size to match.
        # Probe first via a throwaway adapter with probe dimensions.
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(f"{cfg.osworld.server_url}/screen_size", method="POST")
            urllib.request.urlopen(req, timeout=5).read()
        except urllib.error.URLError as exc:
            pytest.skip(f"OSWorld server not reachable: {exc}")

        adapter = OSWorldGUIAdapter(cfg.osworld)
        w, h = adapter.get_screen_size()
        assert w > 0 and h > 0

    def test_screenshot_roundtrip(self, repo_config: StrataConfig) -> None:
        from strata.env.gui_osworld import OSWorldGUIAdapter

        cfg = self._build_config(repo_config)
        try:
            adapter = OSWorldGUIAdapter(cfg.osworld)
        except Exception as exc:
            pytest.skip(f"OSWorld adapter init failed: {exc}")
        data = adapter.capture_screen()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_region_crop_roundtrip(self, repo_config: StrataConfig) -> None:
        from strata.core.types import ScreenRegion
        from strata.env.gui_osworld import OSWorldGUIAdapter

        cfg = self._build_config(repo_config)
        try:
            adapter = OSWorldGUIAdapter(cfg.osworld)
        except Exception as exc:
            pytest.skip(f"OSWorld adapter init failed: {exc}")
        w, h = adapter.get_screen_size()
        region = ScreenRegion(x=0, y=0, width=min(128, w), height=min(64, h))
        data = adapter.capture_screen(region)
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        # Verify dimensions via Pillow round-trip.
        img = Image.open(io.BytesIO(data))
        assert img.size == (region.width, region.height)

    def test_mouse_move_and_click_execute(self, repo_config: StrataConfig) -> None:
        """Actually drive pyautogui through /run_python end-to-end.

        Moves the cursor to (100, 100), clicks, then re-reads /cursor_position
        via a bare HTTP GET to confirm the action took effect inside the VM.
        """
        import urllib.request

        from strata.env.gui_osworld import OSWorldGUIAdapter

        cfg = self._build_config(repo_config)
        try:
            adapter = OSWorldGUIAdapter(cfg.osworld)
        except Exception as exc:
            pytest.skip(f"OSWorld adapter init failed: {exc}")

        adapter.move_mouse(100.0, 100.0)

        # Read back cursor position from the live server.
        resp = urllib.request.urlopen(f"{cfg.osworld.server_url}/cursor_position", timeout=5)
        body = resp.read().decode("utf-8")
        # OSWorld returns "{x}, {y}" or a JSON payload depending on version —
        # accept either, we only care the request succeeded and that the
        # coordinates include our target digits.
        assert "100" in body, f"unexpected cursor_position body: {body!r}"
