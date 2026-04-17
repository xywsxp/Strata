"""Tests for strata.observability.recorder — TrajectoryRecorder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import icontract
import pytest

from strata.core.errors import OSWorldConnectionError
from strata.observability.recorder import (
    NullRecorder,
    OSWorldFFmpegRecorder,
    RemoteCodeRunner,
    TrajectoryRecorder,
)

_SCREEN = (1920, 1080)


def _mock_runner() -> MagicMock:
    runner = MagicMock(spec=RemoteCodeRunner)
    runner.post_json.return_value = {"status": "success"}
    runner.post_form_get_bytes.return_value = b"fake-mp4"
    runner.get_bytes.return_value = b"\x89PNG_data"
    return runner


class TestNullRecorder:
    def test_implements_protocol(self) -> None:
        rec = NullRecorder()
        assert isinstance(rec, TrajectoryRecorder)

    def test_all_methods_noop(self) -> None:
        rec = NullRecorder()
        rec.start("test-run")
        rec.note_keyframe("step_0001")
        rec.note_event("task_state", {"id": "t1", "state": "SUCCEEDED"})
        rec.stop()


class TestOSWorldFFmpegRecorder:
    def test_start_spawns_ffmpeg_via_run_python(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        rec.start("test-run-id")

        calls = [str(c) for c in runner.post_json.call_args_list]
        assert any("ffmpeg" in c for c in calls)

    def test_stop_sends_sigint_and_downloads_mp4(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        rec._started = True
        rec._run_id = "test-run"
        rec.stop()

        runner.post_form_get_bytes.assert_called_once()
        mp4_path = tmp_path / "rec" / "osworld.mp4"
        assert mp4_path.exists()
        assert mp4_path.read_bytes() == b"fake-mp4"

    def test_stop_writes_empty_mp4_on_http_error(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        runner.post_form_get_bytes.side_effect = OSWorldConnectionError("timeout")
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        rec._started = True
        rec._run_id = "test-run"
        rec.stop()

        mp4_path = tmp_path / "rec" / "osworld.mp4"
        assert mp4_path.exists()
        assert mp4_path.read_bytes() == b""

    def test_keyframe_writes_png(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec_dir = tmp_path / "rec"
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, rec_dir, fps=30)
        rec.note_keyframe("step_0001_pre")

        screenshots_dir = rec_dir.parent / "screenshots"
        assert (screenshots_dir / "step_0001_pre.png").read_bytes() == b"\x89PNG_data"

    def test_note_event_appends_to_events(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        rec._started = True
        rec._run_id = "test"

        rec.note_event("task_state", {"id": "t1", "state": "SUCCEEDED"})
        rec.note_event("plan_ready", {"tasks": 3})
        rec.stop()

        events_path = tmp_path / "rec" / "events.jsonl"
        assert events_path.exists()
        lines = events_path.read_text().strip().split("\n")
        assert len(lines) == 2
        e0 = json.loads(lines[0])
        assert e0["kind"] == "task_state"

    def test_refuses_unsafe_run_id(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        with pytest.raises(icontract.ViolationError):
            rec.start("bad;id")
        with pytest.raises(icontract.ViolationError):
            rec.start("rm -rf /")

    def test_disables_after_consecutive_failures(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        runner.post_json.side_effect = OSWorldConnectionError("down")
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)

        for _ in range(3):
            rec.start("attempt")
        assert rec._disabled is True

        rec.start("another")
        assert runner.post_json.call_count == 3

    def test_note_event_rejects_empty_kind(self, tmp_path: Path) -> None:
        runner = _mock_runner()
        rec = OSWorldFFmpegRecorder(runner, _SCREEN, tmp_path / "rec", fps=30)
        with pytest.raises(icontract.ViolationError):
            rec.note_event("", {"x": 1})

    def test_runner_satisfies_protocol(self) -> None:
        runner = _mock_runner()
        assert isinstance(runner, RemoteCodeRunner)
