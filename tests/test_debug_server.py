"""Tests for strata.debug.server — DebugServer HTTP + WS + auth."""

from __future__ import annotations

import asyncio
from collections.abc import Generator

import aiohttp
import pytest

from strata.core.config import DebugConfig
from strata.core.types import TaskGraph, TaskNode
from strata.debug.controller import DebugController
from strata.debug.server import DebugServer

_ServerCtx = tuple[int, str, DebugController, DebugServer]


def _make_cfg(port: int = 18390) -> DebugConfig:
    return DebugConfig(enabled=True, port=port, token="test-token-xyz")


def _make_graph() -> TaskGraph:
    return TaskGraph(
        goal="test",
        tasks=(TaskNode(id="t1", task_type="primitive", action="click"),),
    )


class TestServerLifecycle:
    def test_start_stop(self) -> None:
        cfg = _make_cfg(18391)
        ctrl = DebugController(cfg)
        server = DebugServer(ctrl, cfg)
        server.start()
        assert server.is_running
        server.stop()
        assert not server.is_running

    def test_stop_idempotent(self) -> None:
        cfg = _make_cfg(18392)
        ctrl = DebugController(cfg)
        server = DebugServer(ctrl, cfg)
        server.start()
        server.stop()
        server.stop()
        assert not server.is_running


class TestHTTPEndpoints:
    @pytest.fixture()
    def _server_ctx(self) -> Generator[_ServerCtx]:
        port = 18393
        cfg = _make_cfg(port)
        ctrl = DebugController(cfg)
        graph = _make_graph()
        server = DebugServer(
            ctrl,
            cfg,
            graph_fn=lambda: graph,
            task_states_fn=lambda: {"t1": "PENDING"},
        )
        server.start()
        yield port, cfg.token, ctrl, server
        server.stop()

    def test_state_returns_json(self, _server_ctx: _ServerCtx) -> None:
        port, token, _ctrl, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/api/state",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp,
            ):
                assert resp.status == 200
                data = await resp.json()
                assert "debug_state" in data
                assert "global_state" in data

        asyncio.run(_check())

    def test_graph_returns_json(self, _server_ctx: _ServerCtx) -> None:
        port, token, _, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/api/graph",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp,
            ):
                assert resp.status == 200
                data = await resp.json()
                assert "graph" in data
                assert data["graph"] is not None

        asyncio.run(_check())

    def test_auth_rejects_bad_token(self, _server_ctx: _ServerCtx) -> None:
        port, _, _, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/api/state",
                    headers={"Authorization": "Bearer wrong-token"},
                ) as resp,
            ):
                assert resp.status == 401

        asyncio.run(_check())

    def test_auth_accepts_query_token(self, _server_ctx: _ServerCtx) -> None:
        port, token, _, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(f"http://127.0.0.1:{port}/api/state?token={token}") as resp,
            ):
                assert resp.status == 200

        asyncio.run(_check())

    def test_index_returns_html(self, _server_ctx: _ServerCtx) -> None:
        port, token, _, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp,
            ):
                assert resp.status == 200
                assert "text/html" in resp.content_type

        asyncio.run(_check())

    def test_step_and_continue(self, _server_ctx: _ServerCtx) -> None:
        port, token, _ctrl, _ = _server_ctx

        async def _check() -> None:
            hdrs = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{port}/api/step", headers=hdrs) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["step_mode"] is True

                async with s.post(f"http://127.0.0.1:{port}/api/continue", headers=hdrs) as resp:
                    assert resp.status == 200

        asyncio.run(_check())

    def test_step_once_http_endpoint(self, _server_ctx: _ServerCtx) -> None:
        port, token, _ctrl, _ = _server_ctx

        async def _check() -> None:
            hdrs = {"Authorization": f"Bearer {token}"}
            async with (
                aiohttp.ClientSession() as s,
                s.post(
                    f"http://127.0.0.1:{port}/api/step",
                    headers=hdrs,
                    json={"action": "once"},
                ) as resp,
            ):
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True

        asyncio.run(_check())

    def test_step_toggle_still_works(self, _server_ctx: _ServerCtx) -> None:
        port, token, _ctrl, _ = _server_ctx

        async def _check() -> None:
            hdrs = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as s:
                # Enable
                async with s.post(
                    f"http://127.0.0.1:{port}/api/step",
                    headers=hdrs,
                    json={"enabled": True},
                ) as resp:
                    data = await resp.json()
                    assert data["step_mode"] is True
                # Disable
                async with s.post(
                    f"http://127.0.0.1:{port}/api/step",
                    headers=hdrs,
                    json={"enabled": False},
                ) as resp:
                    data = await resp.json()
                    assert data["step_mode"] is False

        asyncio.run(_check())

    def test_breakpoint_add_remove(self, _server_ctx: _ServerCtx) -> None:
        port, token, _ctrl, _ = _server_ctx

        async def _check() -> None:
            hdrs = {"Authorization": f"Bearer {token}"}
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"http://127.0.0.1:{port}/api/breakpoint",
                    headers=hdrs,
                    json={"task_id": "t1", "action": "add"},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "t1" in data["breakpoints"]

                async with s.post(
                    f"http://127.0.0.1:{port}/api/breakpoint",
                    headers=hdrs,
                    json={"task_id": "t1", "action": "remove"},
                ) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "t1" not in data["breakpoints"]

        asyncio.run(_check())

    def test_screenshot_no_gui_returns_503(self, _server_ctx: _ServerCtx) -> None:
        port, token, _, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/api/screenshot",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp,
            ):
                assert resp.status == 503

        asyncio.run(_check())

    def test_ws_receives_events(self, _server_ctx: _ServerCtx) -> None:
        port, token, ctrl, _ = _server_ctx

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.ws_connect(
                    f"http://127.0.0.1:{port}/ws/events?token={token}",
                ) as ws,
            ):
                ctrl.notify("task_dispatched", "EXECUTING", {"t1": "RUNNING"})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                assert msg["event"] == "task_dispatched"

        asyncio.run(_check())


class TestGraphHistory:
    @pytest.fixture()
    def _server_with_history(self) -> Generator[_ServerCtx]:
        from strata.core.types import TaskGraph, TaskNode

        port = 18397
        cfg = _make_cfg(port)
        ctrl = DebugController(cfg)
        graph1 = TaskGraph(goal="g", tasks=(TaskNode(id="t1", task_type="primitive"),))
        graph2 = TaskGraph(
            goal="g",
            tasks=(
                TaskNode(id="t1", task_type="primitive"),
                TaskNode(id="t2", task_type="primitive"),
            ),
        )
        history: list[tuple[TaskGraph, str, float]] = [
            (graph1, "initial_plan", 1713400000.0),
            (graph2, "replan", 1713400060.0),
        ]
        server = DebugServer(
            ctrl,
            cfg,
            graph_fn=lambda: graph2,
            task_states_fn=lambda: {"t1": "SUCCEEDED", "t2": "PENDING"},
            graph_history_fn=lambda: history,
        )
        server.start()
        yield port, cfg.token, ctrl, server
        server.stop()

    def test_graph_history_returns_versions(self, _server_with_history: _ServerCtx) -> None:
        port, token, _, _ = _server_with_history

        async def _check() -> None:
            async with (
                aiohttp.ClientSession() as s,
                s.get(
                    f"http://127.0.0.1:{port}/api/graph/history",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp,
            ):
                assert resp.status == 200
                data = await resp.json()
                assert data["current_version"] == 2
                assert len(data["versions"]) == 2
                assert data["versions"][0]["reason"] == "initial_plan"
                assert data["versions"][0]["task_ids"] == ["t1"]
                assert data["versions"][1]["task_ids"] == ["t1", "t2"]

        asyncio.run(_check())

    def test_graph_history_empty_when_no_fn(self) -> None:
        port = 18398
        cfg = _make_cfg(port)
        ctrl = DebugController(cfg)
        graph = _make_graph()
        server = DebugServer(
            ctrl,
            cfg,
            graph_fn=lambda: graph,
            task_states_fn=lambda: {"t1": "PENDING"},
        )
        server.start()
        try:

            async def _check() -> None:
                async with (
                    aiohttp.ClientSession() as s,
                    s.get(
                        f"http://127.0.0.1:{port}/api/graph/history",
                        headers={"Authorization": f"Bearer {cfg.token}"},
                    ) as resp,
                ):
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["versions"] == []

            asyncio.run(_check())
        finally:
            server.stop()


class TestPanelHTML:
    """Static assertions on the served panel.html content."""

    def test_panel_html_no_orphan_css(self) -> None:
        """Confirm orphan CSS classes were removed in Step 10.1."""
        import importlib.resources

        html = importlib.resources.read_text("strata.debug", "panel.html")
        for orphan in (".llm-msg-block", ".llm-modal", ".llm-msg-role", ".llm-msg-content"):
            assert orphan not in html, f"orphan CSS {orphan} still present"

    def test_panel_html_vis_network_ref(self) -> None:
        """vis-network CDN reference must still exist."""
        import importlib.resources

        html = importlib.resources.read_text("strata.debug", "panel.html")
        assert "vis-network" in html

    def test_panel_html_no_transcript_modal(self) -> None:
        """Confirm llm-transcript-modal was removed in Step 10.2."""
        import importlib.resources

        html = importlib.resources.read_text("strata.debug", "panel.html")
        assert "llm-transcript-modal" not in html

    def test_panel_html_valid(self) -> None:
        """Confirm script tags are balanced."""
        import importlib.resources

        html = importlib.resources.read_text("strata.debug", "panel.html")
        assert html.count("<script") == html.count("</script>")
