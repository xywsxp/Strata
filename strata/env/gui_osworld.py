"""OSWorld GUI adapter — HTTP client for the OSWorld Docker server.

# CONVENTION: 本适配器**不**依赖 ``desktop_env`` Python 包 — 直接讲 OSWorld
# 容器内 Flask 服务器的 HTTP 协议（默认 http://localhost:5000）。好处：
#   * 避免 torch / easyocr / pillow<12 等重量级传递依赖冲突
#   * 与容器完全解耦，本地 venv 只需 stdlib ``urllib`` + Pillow
#   * ``--headless`` Docker 部署可直接使用，无需开发机本地 OSWorld 安装
#
# 动作通过 ``POST /run_python`` 下发 pyautogui 代码片段执行 — 这是 OSWorld
# 官方 ``action_space=pyautogui`` 的 wire format。
"""

from __future__ import annotations

import io
import json

import icontract

from strata.core.config import OSWorldConfig
from strata.core.errors import ConfigError, OSWorldConnectionError
from strata.core.types import ScreenRegion
from strata.env.osworld_client import OSWorldHTTPClient


def _json_param(value: object) -> str:
    """Serialize *value* to a JSON string safe for embedding in Python code."""
    return json.dumps(value, ensure_ascii=False)


class OSWorldGUIAdapter:
    """IGUIAdapter implementation backed by the OSWorld Docker HTTP server.

    The adapter is constructed greedily: a live ``POST /screen_size`` call
    is performed at init time to verify connectivity and confirm the
    configured screen dimensions match the running VM.
    """

    @icontract.require(lambda config: config.enabled, "OSWorld must be enabled in config")
    def __init__(self, config: OSWorldConfig) -> None:
        self._config = config
        self._screen_w, self._screen_h = config.screen_size
        self._client = OSWorldHTTPClient(
            base_url=config.server_url,
            timeout=config.request_timeout,
        )

        try:
            actual_w, actual_h = self._query_screen_size()
        except OSWorldConnectionError:
            raise
        except Exception as exc:
            raise OSWorldConnectionError(
                f"failed to reach OSWorld server at {config.server_url}: {exc}"
            ) from exc

        if (actual_w, actual_h) != config.screen_size:
            raise ConfigError(
                f"screen size mismatch: expected {config.screen_size}, got ({actual_w}, {actual_h})"
            )

    # ── Mouse / keyboard (via pyautogui over /run_python) ──

    def click(self, x: float, y: float, button: str = "left") -> None:
        if button not in ("left", "right", "middle"):
            raise ValueError(f"invalid mouse button: {button!r}")
        self._run_python(
            f"import pyautogui; pyautogui.click(x={float(x)}, y={float(y)}, button='{button}')"
        )

    def double_click(self, x: float, y: float) -> None:
        self._run_python(f"import pyautogui; pyautogui.doubleClick(x={float(x)}, y={float(y)})")

    def move_mouse(self, x: float, y: float) -> None:
        self._run_python(f"import pyautogui; pyautogui.moveTo(x={float(x)}, y={float(y)})")

    def type_text(self, text: str, interval: float = 0.05) -> None:
        # CONVENTION: pynput.keyboard.Controller.type() 替代 pyautogui.typewrite()。
        # typewrite() 只能按键名发送事件，无法正确输入需要 Shift 的字符
        # （< > { } " 等全部打错）。pynput.type() 通过 Xlib 正确处理修饰键。
        self._run_python(
            "import json, time\n"
            "from pynput.keyboard import Controller as _Kbd\n"
            "_kbd = _Kbd()\n"
            f"for _ch in json.loads({_json_param(text)!r}):\n"
            f"    _kbd.type(_ch)\n"
            f"    time.sleep({float(interval)})\n"
        )

    def press_key(self, key: str) -> None:
        if "'" in key or "\\" in key:
            raise ValueError(f"invalid key identifier: {key!r}")
        self._run_python(f"import pyautogui; pyautogui.press('{key}')")

    def hotkey(self, *keys: str) -> None:
        for k in keys:
            if "'" in k or "\\" in k:
                raise ValueError(f"invalid key identifier: {k!r}")
        key_args = ", ".join(f"'{k}'" for k in keys)
        self._run_python(f"import pyautogui; pyautogui.hotkey({key_args})")

    def scroll(self, delta_x: int, delta_y: int) -> None:
        """Issue vertical and/or horizontal scroll via pyautogui.

        pyautogui's ``scroll(clicks)`` is vertical (positive=up);
        ``hscroll(clicks)`` is horizontal (positive=right). OSWorld inverts
        neither, so we map delta_y>0 → "down" by negating.
        """
        if delta_y != 0:
            clicks = -int(delta_y // 100) if abs(delta_y) >= 100 else (-1 if delta_y > 0 else 1)
            self._run_python(f"import pyautogui; pyautogui.scroll({clicks})")
        if delta_x != 0:
            clicks = int(delta_x // 100) if abs(delta_x) >= 100 else (1 if delta_x > 0 else -1)
            self._run_python(f"import pyautogui; pyautogui.hscroll({clicks})")

    # ── Screen ──

    def get_screen_size(self) -> tuple[int, int]:
        return (self._screen_w, self._screen_h)

    def capture_screen(self, region: ScreenRegion | None = None) -> bytes:
        """Return a PNG screenshot via ``GET /screenshot``, optionally cropped
        locally with Pillow to *region*.
        """
        data = self._client.get_bytes("/screenshot")
        if not data:
            raise OSWorldConnectionError("capture_screen returned empty body")
        if region is None:
            return data
        return self._crop_png(data, region)

    def get_dpi_scale_for_point(self, x: float, y: float) -> float:
        return 1.0

    # ── Internals ──

    def _query_screen_size(self) -> tuple[int, int]:
        resp = self._client.post_json("/screen_size", {})
        width = resp.get("width")
        height = resp.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise OSWorldConnectionError(f"/screen_size returned unexpected payload: {resp!r}")
        return (width, height)

    def _run_python(self, code: str) -> None:
        resp = self._client.post_json("/run_python", {"code": code})
        status = resp.get("status")
        if status != "success":
            raise OSWorldConnectionError(
                f"/run_python failed: status={status!r}, message={resp.get('message')!r}"
            )

    def _crop_png(self, data: bytes, region: ScreenRegion) -> bytes:
        try:
            from PIL import Image
        except ImportError as exc:
            raise OSWorldConnectionError(
                "Pillow is required for regional capture_screen; run: uv add pillow"
            ) from exc

        img = Image.open(io.BytesIO(data))
        w, h = img.size
        left = max(0, min(region.x, w))
        top = max(0, min(region.y, h))
        right = max(left, min(region.x + region.width, w))
        bottom = max(top, min(region.y + region.height, h))
        cropped = img.crop((left, top, right, bottom))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
