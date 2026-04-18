"""Tests for strata.harness.context — WorkingMemory, ContextManager, AuditLogger, pruning."""

from __future__ import annotations

import json
import os
import tempfile

from strata.core.config import MemoryConfig
from strata.core.types import TaskGraph, TaskNode
from strata.harness.context import (
    AuditLogger,
    ContextManager,
    WorkingMemory,
    extract_local_context,
)


class TestVarRoundtrip:
    def test_set_get(self) -> None:
        mem = WorkingMemory(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        mem.set_var("x", 42)
        assert mem.get_var("x") == 42

    def test_get_missing_returns_none(self) -> None:
        mem = WorkingMemory(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        assert mem.get_var("nonexistent") is None


class TestFactsFIFO:
    def test_eviction(self) -> None:
        mem = WorkingMemory(MemoryConfig(sliding_window_size=5, max_facts_in_slot=3))
        for i in range(5):
            mem.add_fact(f"key{i}", f"val{i}")
        facts = mem.get_facts()
        assert len(facts) == 3
        assert facts[0].key == "key2"

    def test_clear(self) -> None:
        mem = WorkingMemory(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        mem.set_var("a", 1)
        mem.add_fact("f", "v")
        mem.clear()
        assert mem.get_var("a") is None
        assert len(mem.get_facts()) == 0


class TestTopologicalPruning:
    def test_extract_linear_neighbors(self) -> None:
        tasks = (
            TaskNode(id="t0", task_type="primitive", action="a"),
            TaskNode(id="t1", task_type="primitive", action="b"),
            TaskNode(id="t2", task_type="primitive", action="c"),
        )
        graph = TaskGraph(goal="test", tasks=tasks)
        ctx = extract_local_context(graph, "t1")
        assert ctx.failed_node.id == "t1"
        sibling_ids = {s.id for s in ctx.siblings}
        assert "t0" in sibling_ids
        assert "t2" in sibling_ids

    def test_extract_from_method(self) -> None:
        sub1 = TaskNode(id="s1", task_type="primitive", action="x")
        sub2 = TaskNode(id="s2", task_type="primitive", action="y")
        parent = TaskNode(id="p1", task_type="compound", method="do_things")
        graph = TaskGraph(
            goal="test",
            tasks=(parent, sub1, sub2),
            methods={"do_things": (sub1, sub2)},
        )
        ctx = extract_local_context(graph, "s1")
        assert ctx.failed_node.id == "s1"
        assert ctx.parent_id == "p1"
        assert len(ctx.siblings) == 1
        assert ctx.siblings[0].id == "s2"

    def test_missing_task_raises_context_error(self) -> None:
        import pytest

        from strata import StrataError
        from strata.core.errors import ContextError, HarnessError

        graph = TaskGraph(goal="test", tasks=(TaskNode(id="t1", task_type="primitive"),))
        with pytest.raises(ContextError, match="not in graph") as exc_info:
            extract_local_context(graph, "nonexistent")
        assert isinstance(exc_info.value, HarnessError)
        assert isinstance(exc_info.value, StrataError)


# ── ContextManager ──


class TestContextManagerWindow:
    def test_window_size_limit(self) -> None:
        cm = ContextManager(MemoryConfig(sliding_window_size=3, max_facts_in_slot=20))
        for i in range(10):
            cm.add_entry({"step": i})
        window = cm.get_window()
        assert len(window) == 3
        assert window[0]["step"] == 7

    def test_facts_through_manager(self) -> None:
        cm = ContextManager(MemoryConfig(sliding_window_size=5, max_facts_in_slot=3))
        for i in range(5):
            cm.add_fact(f"k{i}", f"v{i}")
        facts = cm.get_facts()
        assert len(facts) == 3

    def test_compress_creates_snapshot(self) -> None:
        cm = ContextManager(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        cm.add_entry({"action": "click"})
        cm.add_fact("key", "value")
        cm.memory.set_var("x", 42)
        with tempfile.TemporaryDirectory() as tmpdir:
            cm.compress(snapshot_dir=tmpdir)
            files = os.listdir(tmpdir)
            assert len(files) == 1
            with open(os.path.join(tmpdir, files[0]), encoding="utf-8") as f:
                content = json.load(f)
            assert isinstance(content, dict)
            assert "window" in content
            assert "facts" in content

    def test_compress_trims_window(self) -> None:
        """compress() must halve the sliding window after snapshotting."""
        cm = ContextManager(MemoryConfig(sliding_window_size=20, max_facts_in_slot=20))
        for i in range(10):
            cm.add_entry({"step": i})
        assert len(cm.get_window()) == 10
        with tempfile.TemporaryDirectory() as tmpdir:
            cm.compress(snapshot_dir=tmpdir)
        after = cm.get_window()
        assert len(after) == 5, "compress should keep half the entries"
        # Recent entries are retained.
        assert after[-1]["step"] == 9

    def test_compress_retains_at_least_one_entry(self) -> None:
        """compress() never empties the window entirely."""
        cm = ContextManager(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        cm.add_entry({"step": 0})
        with tempfile.TemporaryDirectory() as tmpdir:
            cm.compress(snapshot_dir=tmpdir)
        assert len(cm.get_window()) == 1

    def test_clear(self) -> None:
        cm = ContextManager(MemoryConfig(sliding_window_size=5, max_facts_in_slot=20))
        cm.add_entry({"action": "test"})
        cm.add_fact("k", "v")
        cm.clear()
        assert len(cm.get_window()) == 0
        assert len(cm.get_facts()) == 0


# ── AuditLogger ──


class TestAuditLogger:
    def test_writes_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            logger = AuditLogger(log_path)
            logger.log("t1", "click", {"x": 100, "y": 200}, "success")
            logger.log("t2", "type", {"text": "hello"}, "success")
            logger.log("t3", "scroll", {}, "done")

            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 3
            for line in lines:
                parsed = json.loads(line)
                assert "task_id" in parsed
                assert "action" in parsed

    def test_redacts_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            logger = AuditLogger(log_path)
            logger.log("t1", "type", {"text": "password is 123"}, "done")

            with open(log_path, encoding="utf-8") as f:
                line = f.readline()
            parsed = json.loads(line)
            assert "password" not in parsed["params"]["text"].lower()
            assert "[REDACTED]" in parsed["params"]["text"]
