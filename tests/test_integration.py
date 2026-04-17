"""End-to-end integration tests.

Most tests use mock LLM. Tests marked @pytest.mark.integration require
OSWorld Docker. Tests marked @pytest.mark.live_llm require real API keys.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from unittest.mock import MagicMock

from strata.core.config import (
    GUIConfig,
    MemoryConfig,
    TerminalConfig,
    get_default_config,
)
from strata.core.types import ActionResult, CommandResult, TaskGraph, TaskNode
from strata.grounding.terminal_handler import TerminalHandler
from strata.grounding.vision_locator import VisionLocator
from strata.harness.context import AuditLogger, ContextManager
from strata.harness.recovery import RecoveryLevel, RecoveryPipeline
from strata.harness.scheduler import LinearRunner
from strata.harness.state_machine import create_global_state_machine
from strata.llm.provider import ChatResponse
from strata.planner.htn import deserialize_graph, serialize_graph, validate_graph


class TestE2ETerminalCommand:
    def test_terminal_roundtrip(self) -> None:
        """CLI -> plan -> schedule -> terminal execute -> exit code 0."""
        terminal = MagicMock()
        terminal.run_command.return_value = CommandResult(
            stdout="hello\n",
            stderr="",
            returncode=0,
        )
        config = TerminalConfig(
            command_timeout=30.0, silence_timeout=10.0, default_shell="/bin/bash"
        )
        handler = TerminalHandler(terminal, config)
        result = handler.execute_command("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout


class TestE2EFileOperation:
    def test_plan_serialize_validate(self) -> None:
        """Plan 'create test.txt in sandbox' -> serialize -> validate."""
        graph = TaskGraph(
            goal="create test.txt in sandbox",
            tasks=(TaskNode(id="t1", task_type="primitive", action="write_file"),),
        )
        serialized = serialize_graph(graph)
        restored = deserialize_graph(serialized)
        assert restored == graph
        assert validate_graph(restored) == []


class TestE2ERecoveryPipeline:
    def test_first_failure_retries(self) -> None:
        """Simulate first failure -> retry succeeds."""
        config = get_default_config()
        adjuster = MagicMock(return_value=[])
        pipeline = RecoveryPipeline(config, adjuster)
        task = TaskNode(id="t1", task_type="primitive", action="click")
        error = RuntimeError("click missed")
        action = pipeline.attempt_recovery(task, error, attempt_count=0)
        assert action.level == RecoveryLevel.RETRY


class TestE2EScheduler:
    def test_sequential_execution(self) -> None:
        """3 tasks, all succeed."""
        graph = TaskGraph(
            goal="test",
            tasks=(
                TaskNode(id="t1", task_type="primitive", action="a"),
                TaskNode(id="t2", task_type="primitive", action="b"),
                TaskNode(id="t3", task_type="primitive", action="c"),
            ),
        )

        class _MockExecutor:
            def execute(self, task: TaskNode, context: Mapping[str, object]) -> ActionResult:
                return ActionResult(success=True)

        scheduler = LinearRunner(get_default_config())
        results = scheduler.run(graph, _MockExecutor())
        assert len(results) == 3
        assert all(r.success for r in results.values())


class TestE2EStateMachineFlow:
    def test_full_happy_path(self) -> None:
        """INIT -> PLANNING -> CONFIRMING -> SCHEDULING -> EXECUTING -> SCHEDULING -> COMPLETED."""
        sm = create_global_state_machine()
        sm.transition("receive_goal")
        sm.transition("plan_ready")
        sm.transition("user_confirm")
        sm.transition("task_dispatched")
        sm.transition("task_done")
        sm.transition("all_done")
        attr = "state"
        assert str(getattr(sm, attr)) == "COMPLETED"


class TestE2EAuditLogger:
    def test_full_log_cycle(self) -> None:
        """Log multiple actions and verify JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            logger = AuditLogger(log_path)
            logger.log("t1", "click", {"x": 100}, "success")
            logger.log("t2", "type", {"text": "hello"}, "success")

            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 2
            for line in lines:
                entry = json.loads(line)
                assert "timestamp" in entry


class TestE2EContextManager:
    def test_window_and_compress(self) -> None:
        """Add entries, compress, verify snapshot."""
        cm = ContextManager(MemoryConfig(sliding_window_size=3, max_facts_in_slot=10))
        for i in range(5):
            cm.add_entry({"step": i, "action": f"action_{i}"})
        cm.add_fact("goal", "open firefox")

        assert len(cm.get_window()) == 3
        with tempfile.TemporaryDirectory() as tmpdir:
            cm.compress(snapshot_dir=tmpdir)
            files = os.listdir(tmpdir)
            assert len(files) == 1


class TestE2EVisionLocatorMocked:
    def test_locate_with_mock_vlm(self) -> None:
        gui = MagicMock()
        gui.get_screen_size.return_value = (1920, 1080)
        gui.capture_screen.return_value = b"fake_png"

        router = MagicMock()
        router.see.return_value = ChatResponse(
            content=json.dumps({"action_type": "click", "x": 500, "y": 300, "confidence": 0.95}),
            model="test-vlm",
            usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
            finish_reason="stop",
        )

        config = GUIConfig(
            lock_timeout=10.0,
            wait_interval=0.01,
            screenshot_without_lock=False,
            enable_scroll_search=True,
            max_scroll_attempts=5,
            scroll_step_pixels=300,
        )
        locator = VisionLocator(gui, router, config)
        coord = locator.locate("submit button")
        assert 0 <= coord.x < 1920
        assert 0 <= coord.y < 1080
