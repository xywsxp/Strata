"""Embedded aiohttp debug server — HTTP + WebSocket with Bearer token auth.

Runs in a daemon thread with its own asyncio event loop.
Lazy-imported: ``debug.enabled = false`` never loads aiohttp.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import icontract

from strata.core.config import DebugConfig
from strata.core.errors import DebugRollbackError, DebugServerError
from strata.core.types import TaskGraph, task_graph_to_dict
from strata.debug.controller import DebugController
from strata.debug.rollback import RollbackEngine

if TYPE_CHECKING:
    from strata.env.protocols import IGUIAdapter

import aiohttp.web

_TOKEN_KEY: aiohttp.web.AppKey[str] = aiohttp.web.AppKey("debug_token")


def _check_token(request: aiohttp.web.Request, token: str) -> bool:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and auth[7:] == token:
        return True
    q = request.query.get("token", "")
    return q == token


@aiohttp.web.middleware
async def _auth_middleware(
    request: aiohttp.web.Request,
    handler: Callable[[aiohttp.web.Request], object],
) -> aiohttp.web.StreamResponse:
    token: str = request.app[_TOKEN_KEY]
    if not _check_token(request, token):
        return aiohttp.web.json_response({"error": "unauthorized"}, status=401)
    resp = await handler(request)  # type: ignore[misc]
    if not isinstance(resp, aiohttp.web.StreamResponse):
        return aiohttp.web.Response(status=500, text="internal error")
    return resp


class DebugServer:
    """HTTP + WebSocket server for the debug panel.

    # CONVENTION: daemon 线程 + 独立 event loop；stop() 幂等。
    """

    @icontract.require(lambda self: True)
    def __init__(
        self,
        controller: DebugController,
        config: DebugConfig,
        gui: IGUIAdapter | None = None,
        graph_fn: Callable[[], TaskGraph | None] | None = None,
        task_states_fn: Callable[[], Mapping[str, str]] | None = None,
        rollback_engine: RollbackEngine | None = None,
    ) -> None:
        self._controller = controller
        self._config = config
        self._gui = gui
        self._graph_fn = graph_fn
        self._task_states_fn = task_states_fn
        self._rollback = rollback_engine
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: aiohttp.web.AppRunner | None = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @icontract.require(
        lambda self: not self.is_running,
        "server must not be already running",
    )
    def start(self) -> None:
        """Boot the aiohttp server in a daemon thread."""
        ready = threading.Event()
        err_box: list[Exception] = []

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(self._start_app(ready))
            except Exception as exc:
                err_box.append(exc)
                ready.set()
                return
            try:
                loop.run_forever()
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self._thread = threading.Thread(target=_run, daemon=True, name="strata-debug")
        self._thread.start()
        ready.wait(timeout=10.0)
        if err_box:
            raise DebugServerError(f"debug server failed to start: {err_box[0]}") from err_box[0]
        self._running = True

    def stop(self) -> None:
        """Shut down the server and join the thread (idempotent)."""
        if not self._running:
            return
        if self._loop is not None and self._runner is not None:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._running = False

    async def _start_app(self, ready: threading.Event) -> None:
        app = aiohttp.web.Application(middlewares=[_auth_middleware])
        app[_TOKEN_KEY] = self._config.token
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/state", self._handle_state)
        app.router.add_get("/api/graph", self._handle_graph)
        app.router.add_get("/api/screenshot", self._handle_screenshot)
        app.router.add_get("/ws/events", self._handle_ws)
        app.router.add_post("/api/step", self._handle_step)
        app.router.add_post("/api/continue", self._handle_continue)
        app.router.add_post("/api/breakpoint", self._handle_breakpoint)
        app.router.add_get("/api/prompt/pending", self._handle_prompt_pending)
        app.router.add_post("/api/prompt/approve", self._handle_prompt_approve)
        app.router.add_post("/api/prompt/skip", self._handle_prompt_skip)
        app.router.add_post("/api/rollback/task", self._handle_rollback_task)
        app.router.add_post("/api/rollback/checkpoint", self._handle_rollback_checkpoint)
        app.router.add_post("/api/rollback/graph", self._handle_rollback_graph)
        app.router.add_get("/api/rollback/versions", self._handle_rollback_versions)
        self._runner = aiohttp.web.AppRunner(app)
        await self._runner.setup()
        site = aiohttp.web.TCPSite(self._runner, "0.0.0.0", self._config.port)
        await site.start()
        ready.set()

    async def _shutdown(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
        loop = asyncio.get_event_loop()
        loop.stop()

    # ── handlers ──

    async def _handle_index(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            ref = importlib.resources.files("strata.debug").joinpath("panel.html")
            html = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, TypeError):
            html = (
                "<html><body><h1>Strata Debug Panel</h1><p>panel.html not found</p></body></html>"
            )
        return aiohttp.web.Response(text=html, content_type="text/html")

    async def _handle_state(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        snap = self._controller.get_state_snapshot()
        return aiohttp.web.json_response(snap)

    async def _handle_graph(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._graph_fn is None:
            return aiohttp.web.json_response({"graph": None, "task_states": {}})
        graph = self._graph_fn()
        ts = dict(self._task_states_fn()) if self._task_states_fn else {}
        if graph is None:
            return aiohttp.web.json_response({"graph": None, "task_states": ts})
        return aiohttp.web.json_response(
            {
                "graph": task_graph_to_dict(graph),
                "task_states": ts,
            }
        )

    async def _handle_screenshot(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._gui is None:
            return aiohttp.web.Response(status=503, text="no GUI adapter")
        try:
            data = self._gui.capture_screen(None)
        except Exception as exc:
            return aiohttp.web.Response(status=500, text=str(exc))
        return aiohttp.web.Response(body=data, content_type="image/png")

    async def _handle_ws(self, request: aiohttp.web.Request) -> aiohttp.web.WebSocketResponse:
        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)
        try:
            while not ws.closed:
                events = self._controller.drain_events()
                for ev in events:
                    payload = {
                        "event": ev.event,
                        "global_state": ev.global_state,
                        "task_states": dict(ev.task_states),
                        "timestamp": ev.timestamp,
                    }
                    await ws.send_json(payload)
                await asyncio.sleep(0.1)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return ws

    async def _handle_step(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.enable_step_mode()
        return aiohttp.web.json_response({"ok": True, "step_mode": True})

    async def _handle_continue(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.continue_execution()
        return aiohttp.web.json_response({"ok": True})

    async def _handle_breakpoint(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return aiohttp.web.json_response({"error": "invalid JSON"}, status=400)
        task_id = body.get("task_id", "")
        action = body.get("action", "add")
        if not isinstance(task_id, str) or not task_id.strip():
            return aiohttp.web.json_response({"error": "task_id required"}, status=400)
        if action == "remove":
            self._controller.remove_breakpoint(task_id)
        else:
            self._controller.add_breakpoint(task_id)
        return aiohttp.web.json_response(
            {
                "ok": True,
                "breakpoints": sorted(self._controller.list_breakpoints()),
            }
        )

    # ── prompt interception handlers ──

    async def _handle_prompt_pending(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        pending = self._controller.get_pending_prompt()
        return aiohttp.web.json_response({"pending": pending})

    async def _handle_prompt_approve(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.approve_prompt()
        return aiohttp.web.json_response({"ok": True})

    async def _handle_prompt_skip(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.skip_interception()
        return aiohttp.web.json_response({"ok": True, "intercept_prompts": False})

    # ── rollback handlers ──

    async def _handle_rollback_task(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._rollback is None:
            return aiohttp.web.json_response({"error": "rollback not available"}, status=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        n = int(body.get("n", 1)) if isinstance(body, dict) else 1
        try:
            record = self._rollback.undo_tasks(n)
        except DebugRollbackError as exc:
            return aiohttp.web.json_response({"error": str(exc)}, status=400)
        return aiohttp.web.json_response(
            {
                "ok": True,
                "undone_task": record.task_id,
                "checkpoint_version": record.checkpoint_version,
            }
        )

    async def _handle_rollback_checkpoint(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        if self._rollback is None:
            return aiohttp.web.json_response({"error": "rollback not available"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "invalid JSON"}, status=400)
        version = body.get("version")
        if not isinstance(version, int):
            return aiohttp.web.json_response({"error": "version (int) required"}, status=400)
        try:
            cp = self._rollback.rollback_to_checkpoint(version)
        except DebugRollbackError as exc:
            return aiohttp.web.json_response({"error": str(exc)}, status=400)
        return aiohttp.web.json_response(
            {
                "ok": True,
                "restored_version": version,
                "global_state": cp.global_state,
            }
        )

    async def _handle_rollback_graph(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._rollback is None:
            return aiohttp.web.json_response({"error": "rollback not available"}, status=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        steps = int(body.get("steps", 1)) if isinstance(body, dict) else 1
        try:
            graph = self._rollback.rollback_graph(steps)
        except DebugRollbackError as exc:
            return aiohttp.web.json_response({"error": str(exc)}, status=400)
        return aiohttp.web.json_response({"ok": True, "graph_goal": graph.goal})

    async def _handle_rollback_versions(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._rollback is None:
            return aiohttp.web.json_response({"error": "rollback not available"}, status=503)
        return aiohttp.web.json_response(
            {
                "versions": self._rollback._persistence.list_versions(),
                "undo_depth": self._rollback.undo_depth,
            }
        )
