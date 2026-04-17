"""End-to-end task execution tests.

These tests require BOTH a running OSWorld container and working LLM API keys.
They use the real pipeline: config → health check → orchestrator → run_goal.

Run with::

    STRATA_LIVE_LLM=1 uv run pytest tests/e2e/test_e2e_tasks.py -v -s
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from strata.core.config import StrataConfig

_LIVE_ENABLED = os.environ.get("STRATA_LIVE_LLM") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not _LIVE_ENABLED,
        reason="set STRATA_LIVE_LLM=1 to run E2E task tests",
    ),
]


class TestE2ETaskExecution:
    def test_create_hello_txt(
        self, repo_config: StrataConfig, osworld_url: str, tmp_path: Path
    ) -> None:
        """Execute create-hello-txt task through the full pipeline."""
        from scripts.run_tasks import TaskReport, _run_single
        from strata.planner.tasks import TaskFile

        task_path = Path("tasks/create-hello-txt.toml")
        if not task_path.exists():
            pytest.skip("tasks/create-hello-txt.toml not found")

        task = TaskFile.load(task_path)
        report: TaskReport = _run_single(task, "./config.toml")

        assert report.task_id == "create-hello-txt"
        assert report.verdict in ("PASS", "FAIL", "ERROR", "TIMEOUT")
        print(f"\n  verdict={report.verdict} duration={report.duration_s:.1f}s")
        if report.error:
            print(f"  error={report.error}")
        if report.verify_output:
            print(f"  verify_output={report.verify_output!r}")

    def test_read_hostname(
        self, repo_config: StrataConfig, osworld_url: str, tmp_path: Path
    ) -> None:
        """Execute read-hostname task through the full pipeline."""
        from scripts.run_tasks import TaskReport, _run_single
        from strata.planner.tasks import TaskFile

        task_path = Path("tasks/read-hostname.toml")
        if not task_path.exists():
            pytest.skip("tasks/read-hostname.toml not found")

        task = TaskFile.load(task_path)
        report: TaskReport = _run_single(task, "./config.toml")

        assert report.task_id == "read-hostname"
        assert report.verdict in ("PASS", "FAIL", "ERROR", "TIMEOUT")
        print(f"\n  verdict={report.verdict} duration={report.duration_s:.1f}s")
        if report.error:
            print(f"  error={report.error}")

    def test_report_json_valid(
        self, repo_config: StrataConfig, osworld_url: str, tmp_path: Path
    ) -> None:
        """Run a single task and verify the report JSON structure."""
        from scripts.run_tasks import TaskReport, _run_single, _write_report
        from strata.planner.tasks import TaskFile

        task_path = Path("tasks/read-hostname.toml")
        if not task_path.exists():
            pytest.skip("tasks/read-hostname.toml not found")

        task = TaskFile.load(task_path)
        report: TaskReport = _run_single(task, "./config.toml")

        report_path = _write_report([report], tmp_path / "reports")
        assert report_path.exists()

        data = json.loads(report_path.read_text())
        assert "tasks" in data
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "read-hostname"
        assert data["tasks"][0]["verdict"] in ("PASS", "FAIL", "ERROR", "TIMEOUT")
