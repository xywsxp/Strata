"""Embedded aiohttp debug server — HTTP + WebSocket with Bearer token auth.

Runs in a daemon thread with its own asyncio event loop.
Lazy-imported: ``debug.enabled = false`` never loads aiohttp.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import icontract

from strata.core.config import DebugConfig
from strata.core.errors import DebugRollbackError, DebugServerError
from strata.core.types import TaskGraph, task_graph_to_dict
from strata.debug.controller import DebugController
from strata.debug.rollback import RollbackEngine

if TYPE_CHECKING:
    from strata.env.protocols import IGUIAdapter
    from strata.harness.persistence import Checkpoint
    from strata.llm.provider import ChatMessage

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

    def __init__(
        self,
        controller: DebugController,
        config: DebugConfig,
        gui: IGUIAdapter | None = None,
        graph_fn: Callable[[], TaskGraph | None] | None = None,
        task_states_fn: Callable[[], Mapping[str, str]] | None = None,
        rollback_engine: RollbackEngine | None = None,
        task_dir: str | None = None,
        goal_fn: Callable[[str], None] | None = None,
        cancel_fn: Callable[[], None] | None = None,
        restore_fn: Callable[[Checkpoint], None] | None = None,
        graph_history_fn: Callable[[], Sequence[tuple[object, str, float]]] | None = None,
    ) -> None:
        self._controller = controller
        self._config = config
        self._gui = gui
        self._graph_fn = graph_fn
        self._task_states_fn = task_states_fn
        self._rollback = rollback_engine
        self._task_dir = task_dir
        self._goal_fn = goal_fn
        self._cancel_fn = cancel_fn
        self._restore_fn = restore_fn
        self._graph_history_fn = graph_history_fn
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: aiohttp.web.AppRunner | None = None
        self._running = False
        self._active_goal: str | None = None
        self._goal_lock = threading.Lock()

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
            if self._thread.is_alive():
                return
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
        app.router.add_post("/api/prompt/enable", self._handle_prompt_enable)
        app.router.add_post("/api/rollback/task", self._handle_rollback_task)
        app.router.add_post("/api/rollback/checkpoint", self._handle_rollback_checkpoint)
        app.router.add_post("/api/rollback/graph", self._handle_rollback_graph)
        app.router.add_get("/api/rollback/versions", self._handle_rollback_versions)
        app.router.add_get("/api/tasks", self._handle_tasks)
        app.router.add_post("/api/goal", self._handle_goal)
        app.router.add_get("/api/goal/status", self._handle_goal_status)
        app.router.add_post("/api/goal/cancel", self._handle_goal_cancel)
        app.router.add_get("/api/llm/history", self._handle_llm_history)
        app.router.add_get("/api/llm/history/{seq}", self._handle_llm_record)
        app.router.add_get("/api/graph/history", self._handle_graph_history)
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
                        "task_id": ev.task_id,
                        "detail": ev.detail,
                    }
                    await ws.send_json(payload)
                await asyncio.sleep(0.1)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return ws

    async def _handle_step(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        enabled = True
        action: str | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                action = body.get("action")
                if action is None:
                    enabled = bool(body.get("enabled", True))
        except Exception:
            pass
        if action == "once":
            self._controller.step_once()
            return aiohttp.web.json_response({"ok": True})
        if enabled:
            self._controller.enable_step_mode()
        else:
            self._controller.disable_step_mode()
        return aiohttp.web.json_response({"ok": True, "step_mode": enabled})

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
        from typing import Literal, cast

        from strata.llm.provider import ChatMessage

        _VALID_ROLES = frozenset({"system", "user", "assistant"})

        edited: list[ChatMessage] | None = None
        try:
            body = await request.json()
            if isinstance(body, dict) and "messages" in body:
                raw_msgs = body["messages"]
                if isinstance(raw_msgs, list):
                    edited = []
                    for m in raw_msgs:
                        if not isinstance(m, dict):
                            continue
                        role_raw = str(m.get("role", "user"))
                        role = role_raw if role_raw in _VALID_ROLES else "user"
                        edited.append(
                            ChatMessage(
                                role=cast(Literal["system", "user", "assistant"], role),
                                content=str(m.get("content", "")),
                            )
                        )
        except Exception:
            pass
        self._controller.approve_prompt(edited_messages=edited)
        return aiohttp.web.json_response({"ok": True})

    async def _handle_prompt_skip(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.skip_interception()
        return aiohttp.web.json_response({"ok": True, "intercept_prompts": False})

    async def _handle_prompt_enable(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self._controller.enable_interception()
        return aiohttp.web.json_response({"ok": True, "intercept_prompts": True})

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
        if self._restore_fn is not None:
            self._restore_fn(cp)
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
                "versions": self._rollback.list_checkpoint_versions(),
                "undo_depth": self._rollback.undo_depth,
            }
        )

    # ── task file browser ──

    async def _handle_tasks(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Scan task_dir for *.toml files and return parsed metadata."""
        tasks: list[dict[str, object]] = []
        scan_dir = Path(self._task_dir) if self._task_dir else None
        if scan_dir and scan_dir.is_dir():
            import tomllib

            for f in sorted(scan_dir.glob("*.toml")):
                try:
                    data = tomllib.loads(f.read_text(encoding="utf-8"))
                    task_sect = data.get("task", data)
                    tasks.append(
                        {
                            "file": f.name,
                            "id": str(task_sect.get("id", f.stem)),
                            "goal": str(task_sect.get("goal", "")),
                            "tags": list(task_sect.get("tags", [])),
                            "timeout_s": task_sect.get("timeout_s", 300),
                        }
                    )
                except Exception:
                    tasks.append({"file": f.name, "id": f.stem, "goal": "", "tags": []})
        return aiohttp.web.json_response({"tasks": tasks, "task_dir": str(scan_dir or "")})

    # ── goal submission ──

    async def _handle_goal(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        if self._goal_fn is None:
            return aiohttp.web.json_response({"error": "goal_fn not configured"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return aiohttp.web.json_response({"error": "invalid JSON"}, status=400)
        goal = str(body.get("goal", "")).strip()
        if not goal:
            return aiohttp.web.json_response({"error": "goal must be non-empty"}, status=400)
        with self._goal_lock:
            if self._active_goal is not None:
                return aiohttp.web.json_response(
                    {"error": "a goal is already running", "active": self._active_goal},
                    status=409,
                )
            self._active_goal = goal

        loop = asyncio.get_running_loop()

        def _run_and_clear() -> None:
            try:
                self._goal_fn(goal)  # type: ignore[misc]
            finally:
                with self._goal_lock:
                    self._active_goal = None

        # Fire-and-forget: return 202 immediately so the panel stays responsive.
        # Goal progress is reported via WS events + GET /api/goal/status polling.
        loop.run_in_executor(None, _run_and_clear)
        return aiohttp.web.json_response({"ok": True, "goal": goal}, status=202)

    async def _handle_goal_status(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        with self._goal_lock:
            active = self._active_goal
        return aiohttp.web.json_response({"active_goal": active, "busy": active is not None})

    async def _handle_goal_cancel(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Request cooperative cancellation of the running goal.

        The background thread will exit at the next task boundary. The
        ``_active_goal`` is cleared immediately so the panel can accept a new
        goal once the old one finishes.
        """
        with self._goal_lock:
            was_active = self._active_goal is not None
            self._active_goal = None
        if was_active and self._cancel_fn is not None:
            self._cancel_fn()
        self._controller.continue_execution()
        return aiohttp.web.json_response({"ok": True})

    # ── LLM transcript history ──

    async def _handle_llm_history(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        records = self._controller.get_llm_history()
        items = [
            {
                "seq": r.seq,
                "role": r.role,
                "started_at": r.started_at,
                "duration_ms": r.duration_ms,
                "status": r.status,
                "msg_count": len(r.request_messages),
                "response_len": len(r.response_text),
                "error_type": r.error_type,
            }
            for r in records
        ]
        return aiohttp.web.json_response({"records": items})

    async def _handle_llm_record(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        try:
            seq = int(request.match_info["seq"])
        except (KeyError, ValueError):
            return aiohttp.web.json_response({"error": "invalid seq"}, status=400)
        rec = self._controller.get_llm_record(seq)
        if rec is None:
            return aiohttp.web.json_response({"error": "record not found"}, status=404)
        return aiohttp.web.json_response(
            {
                "seq": rec.seq,
                "role": rec.role,
                "started_at": rec.started_at,
                "duration_ms": rec.duration_ms,
                "status": rec.status,
                "request_messages": [dict(m) for m in rec.request_messages],
                "response_text": rec.response_text,
                "error_type": rec.error_type,
                "error_msg": rec.error_msg,
            }
        )

    async def _handle_graph_history(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Return graph version history with task ID lists for diff computation."""
        if self._graph_history_fn is None:
            return aiohttp.web.json_response({"versions": [], "current_version": 0})
        history = self._graph_history_fn()
        versions: list[dict[str, object]] = []
        for idx, (graph, reason, ts) in enumerate(history, 1):
            task_ids: list[str] = []
            if hasattr(graph, "tasks"):
                task_ids = [t.id for t in graph.tasks]
            versions.append(
                {
                    "version": idx,
                    "reason": reason,
                    "timestamp": ts,
                    "task_ids": task_ids,
                }
            )
        current_version = len(versions)
        return aiohttp.web.json_response({"versions": versions, "current_version": current_version})
