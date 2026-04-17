"""Trajectory recorder — dual-track: in-container ffmpeg + keyframe PNGs.

``TrajectoryRecorder`` is the Protocol. ``OSWorldFFmpegRecorder`` spawns
``ffmpeg x11grab`` inside a remote machine via ``RemoteCodeRunner``, then
downloads the mp4. ``NullRecorder`` is the no-op fallback.

Events (task state changes, etc.) are appended to ``events.jsonl`` for
subtitle overlay / timeline reconstruction.

# CONVENTION: recorder does NOT import strata.env.* — all remote I/O
# is injected via RemoteCodeRunner Protocol, cutting the observability→env
# reverse dependency.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

import icontract

from strata.core.errors import OSWorldConnectionError

_SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
_MAX_CONSECUTIVE_FAILURES = 3


@runtime_checkable
class RemoteCodeRunner(Protocol):
    """Minimal contract for running code / downloading files from a remote machine."""

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]: ...
    def post_form_get_bytes(self, path: str, fields: dict[str, str]) -> bytes: ...
    def get_bytes(self, path: str) -> bytes: ...


@runtime_checkable
class TrajectoryRecorder(Protocol):
    """Observability hook: screen recording + keyframe PNGs + event log."""

    def start(self, run_id: str) -> None: ...
    def stop(self) -> None: ...
    def note_keyframe(self, label: str) -> None: ...
    def note_event(self, kind: str, payload: Mapping[str, object]) -> None: ...


class NullRecorder:
    """No-op recorder for non-OSWorld environments."""

    def start(self, run_id: str) -> None:
        pass

    def stop(self) -> None:
        pass

    def note_keyframe(self, label: str) -> None:
        pass

    def note_event(self, kind: str, payload: Mapping[str, object]) -> None:
        pass


class OSWorldFFmpegRecorder:
    """In-container ffmpeg x11grab recorder via RemoteCodeRunner injection."""

    @icontract.require(lambda fps: 1 <= fps <= 60)
    @icontract.require(
        lambda screen_size: screen_size[0] > 0 and screen_size[1] > 0,
        "screen_size must be positive",
    )
    def __init__(
        self,
        runner: RemoteCodeRunner,
        screen_size: tuple[int, int],
        out_dir: Path,
        fps: int = 30,
    ) -> None:
        self._runner = runner
        self._screen_w, self._screen_h = screen_size
        self._out_dir = out_dir
        self._fps = fps
        self._started = False
        self._run_id = ""
        self._failures = 0
        self._disabled = False
        self._events: list[dict[str, object]] = []

    @icontract.require(
        lambda run_id: bool(_SAFE_RUN_ID.match(run_id)),
        "run_id must match ^[a-zA-Z0-9_-]+$",
    )
    def start(self, run_id: str) -> None:
        if self._disabled:
            return
        self._run_id = run_id
        self._events = []
        w, h = self._screen_w, self._screen_h
        try:
            self._exec_remote(
                "import subprocess, os\n"
                "subprocess.run(['pkill', '-2', 'ffmpeg'], capture_output=True)\n"
                "import time; time.sleep(0.5)\n"
                "os.makedirs('/tmp/strata_rec', exist_ok=True)\n"
            )
            self._exec_remote(
                "import subprocess, os\n"
                f"p = subprocess.Popen([\n"
                f"    'ffmpeg','-y','-loglevel','error',\n"
                f"    '-f','x11grab','-video_size','{w}x{h}','-framerate','{self._fps}',\n"
                f"    '-i',':0',\n"
                f"    '-codec:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',\n"
                f"    '/tmp/strata_rec/{run_id}.mp4'\n"
                f"],\n"
                f"    stdin=subprocess.DEVNULL,\n"
                f"    stdout=subprocess.DEVNULL,\n"
                f"    stderr=subprocess.DEVNULL,\n"
                f"    start_new_session=True,\n"
                f")\n"
                f"with open('/tmp/strata_rec/{run_id}.pid','w') as f:\n"
                f"    f.write(str(p.pid))\n"
            )
            self._started = True
            self._failures = 0
        except (OSWorldConnectionError, OSError) as exc:
            self._record_failure(exc, "start")

    def stop(self) -> None:
        if self._disabled or not self._started:
            return
        self._started = False
        try:
            self._exec_remote(
                "import os, signal, time\n"
                f"pid_path = '/tmp/strata_rec/{self._run_id}.pid'\n"
                "try:\n"
                "    pid = int(open(pid_path).read().strip())\n"
                "    os.kill(pid, signal.SIGINT)\n"
                "    time.sleep(2)\n"
                "except Exception:\n"
                "    pass\n"
            )
            mp4_bytes = self._runner.post_form_get_bytes(
                "/file",
                {"file_path": f"/tmp/strata_rec/{self._run_id}.mp4"},
            )
            self._out_dir.mkdir(parents=True, exist_ok=True)
            (self._out_dir / "osworld.mp4").write_bytes(mp4_bytes)
        except (OSWorldConnectionError, OSError) as exc:
            self._record_failure(exc, "stop")
            self._out_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(Exception):
                (self._out_dir / "osworld.mp4").write_bytes(b"")

        self._write_events_jsonl()

        with contextlib.suppress(Exception):
            self._exec_remote(
                "import subprocess\n"
                "subprocess.run(['rm','-rf','/tmp/strata_rec'],capture_output=True)\n"
            )

    def note_keyframe(self, label: str) -> None:
        if self._disabled:
            return
        try:
            png = self._runner.get_bytes("/screenshot")
            self._out_dir.mkdir(parents=True, exist_ok=True)
            screenshots_dir = self._out_dir.parent / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            (screenshots_dir / f"{label}.png").write_bytes(png)
        except (OSWorldConnectionError, OSError) as exc:
            self._record_failure(exc, "keyframe")

    @icontract.require(lambda kind: len(kind.strip()) > 0, "kind must be non-empty")
    def note_event(self, kind: str, payload: Mapping[str, object]) -> None:
        entry: dict[str, object] = {
            "ts": time.time(),
            "kind": kind,
            "payload": dict(payload),
        }
        self._events.append(entry)

    def _exec_remote(self, code: str) -> None:
        self._runner.post_json("/run_python", {"code": code})

    def _write_events_jsonl(self) -> None:
        if not self._events:
            return
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            path = self._out_dir / "events.jsonl"
            lines = [json.dumps(e, ensure_ascii=False) for e in self._events]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            with contextlib.suppress(Exception):
                print(
                    f"[strata.recorder] cannot write events: {exc}",
                    file=sys.stderr,
                )

    def _record_failure(self, exc: Exception, context: str) -> None:
        self._failures += 1
        with contextlib.suppress(Exception):
            limit = _MAX_CONSECUTIVE_FAILURES
            print(
                f"[strata.recorder] {context} failed ({self._failures}/{limit}): {exc}",
                file=sys.stderr,
            )
        if self._failures >= _MAX_CONSECUTIVE_FAILURES:
            self._disabled = True
            with contextlib.suppress(Exception):
                print(
                    "[strata.recorder] disabled after consecutive failures",
                    file=sys.stderr,
                )
