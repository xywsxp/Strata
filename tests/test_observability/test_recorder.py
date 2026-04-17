"""Tests for strata.observability.recorder — TrajectoryRecorder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import icontract
import pytest

from strata.core.config import OSWorldConfig
from strata.core.errors import OSWorldConnectionError
from strata.observability.recorder import (
    NullRecorder,
    OSWorldFFmpegRecorder,
    TrajectoryRecorder,
)


def _osworld_config() -> OSWorldConfig:
    return OSWorldConfig(
        enabled=True,
        provider="docker",
        os_type="Ubuntu",
        screen_size=(1920, 1080),
        headless=True,
        action_space="pyautogui",
        docker_image=None,
        server_url="http://localhost:5000",
        request_timeout=10.0,
    )


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
    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_start_spawns_ffmpeg_via_run_python(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post_json.return_value = {"status": "success"}

        rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
        rec._client = mock_client
        rec.start("test-run-id")

        calls = [str(c) for c in mock_client.post_json.call_args_list]
        ffmpeg_found = any("ffmpeg" in c for c in calls)
        assert ffmpeg_found

    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_stop_sends_sigint_and_downloads_mp4(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post_json.return_value = {"status": "success"}
        mock_client.post_form_get_bytes.return_value = b"fake-mp4-data"

        rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
        rec._client = mock_client
        rec._started = True
        rec._run_id = "test-run"
        rec.stop()

        mock_client.post_form_get_bytes.assert_called_once()
        mp4_path = tmp_path / "rec" / "osworld.mp4"
        assert mp4_path.exists()
        assert mp4_path.read_bytes() == b"fake-mp4-data"

    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_stop_writes_empty_mp4_on_http_error(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post_json.return_value = {"status": "success"}
        mock_client.post_form_get_bytes.side_effect = OSWorldConnectionError("timeout")

        rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
        rec._client = mock_client
        rec._started = True
        rec._run_id = "test-run"
        rec.stop()

        mp4_path = tmp_path / "rec" / "osworld.mp4"
        assert mp4_path.exists()
        assert mp4_path.read_bytes() == b""

    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_keyframe_writes_png(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        fake_png = b"\x89PNG_screenshot_data"
        mock_client.get_bytes.return_value = fake_png

        rec_dir = tmp_path / "rec"
        rec = OSWorldFFmpegRecorder(_osworld_config(), rec_dir, fps=30)
        rec._client = mock_client
        rec.note_keyframe("step_0001_pre")

        screenshots_dir = rec_dir.parent / "screenshots"
        assert (screenshots_dir / "step_0001_pre.png").read_bytes() == fake_png

    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_note_event_appends_to_events(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post_json.return_value = {"status": "success"}
        mock_client.post_form_get_bytes.return_value = b"mp4"

        rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
        rec._client = mock_client
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
        with patch("strata.observability.recorder._OSWorldHTTPClient"):
            rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
            with pytest.raises(icontract.ViolationError):
                rec.start("bad;id")
            with pytest.raises(icontract.ViolationError):
                rec.start("rm -rf /")

    @patch("strata.observability.recorder._OSWorldHTTPClient")
    def test_disables_after_consecutive_failures(
        self, mock_client_cls: MagicMock, tmp_path: Path
    ) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.post_json.side_effect = OSWorldConnectionError("down")

        rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
        rec._client = mock_client

        for _ in range(3):
            rec.start("attempt")
        assert rec._disabled is True

        rec.start("another")
        assert mock_client.post_json.call_count == 3

    def test_note_event_rejects_empty_kind(self, tmp_path: Path) -> None:
        with patch("strata.observability.recorder._OSWorldHTTPClient"):
            rec = OSWorldFFmpegRecorder(_osworld_config(), tmp_path / "rec", fps=30)
            with pytest.raises(icontract.ViolationError):
                rec.note_event("", {"x": 1})
