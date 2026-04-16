"""Tests for strata.harness.context — WorkingMemory + topological pruning."""

from __future__ import annotations

from strata.core.config import MemoryConfig
from strata.core.types import TaskGraph, TaskNode
from strata.harness.context import WorkingMemory, extract_local_context


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

    def test_missing_task_raises(self) -> None:
        import pytest

        graph = TaskGraph(goal="test", tasks=(TaskNode(id="t1", task_type="primitive"),))
        with pytest.raises(ValueError, match="not in graph"):
            extract_local_context(graph, "nonexistent")
