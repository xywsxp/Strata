"""Tests for strata.harness.debug — ActionDebugLogger."""

from __future__ import annotations

import json
from pathlib import Path

from strata.harness.debug import ActionDebugLogger, NullActionDebugLogger


class TestActionDebugLogger:
    def test_creates_file_on_first_log(self, tmp_path: Path) -> None:
        logger = ActionDebugLogger(run_dir=tmp_path)
        logger.log("click", params={"x": 10, "y": 20}, result="ok")
        assert (tmp_path / "debug_actions.jsonl").exists()

    def test_entries_are_jsonl(self, tmp_path: Path) -> None:
        logger = ActionDebugLogger(run_dir=tmp_path)
        logger.log("click", result="ok")
        logger.log("type_text", params={"text": "hello"}, result="ok")
        lines = (tmp_path / "debug_actions.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "action" in entry
            assert "timestamp" in entry
            assert entry["schema_version"] == 1

    def test_screenshot_encoded(self, tmp_path: Path) -> None:
        logger = ActionDebugLogger(run_dir=tmp_path)
        logger.log("click", pre_screenshot=b"\x89PNG", post_screenshot=b"\x89PNG")
        line = (tmp_path / "debug_actions.jsonl").read_text().strip()
        entry = json.loads(line)
        assert "pre_screenshot_b64" in entry
        assert "post_screenshot_b64" in entry

    def test_error_field(self, tmp_path: Path) -> None:
        logger = ActionDebugLogger(run_dir=tmp_path)
        logger.log("click", error="element not found")
        line = (tmp_path / "debug_actions.jsonl").read_text().strip()
        entry = json.loads(line)
        assert entry["error"] == "element not found"

    def test_no_op_without_run_dir(self) -> None:
        logger = ActionDebugLogger(run_dir=None)
        logger.log("click", result="ok")  # Should not raise


class TestNullActionDebugLogger:
    def test_no_side_effects(self, tmp_path: Path) -> None:
        logger = NullActionDebugLogger()
        logger.log("click", result="ok")
        # No files should exist anywhere
        assert not list(tmp_path.iterdir())
