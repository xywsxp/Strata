"""Embedded debug UI — HTTP/WS server and controller (optional dependency: aiohttp)."""

from strata.debug.controller import DebugController, DebugEvent, DebugState, PromptInterceptor

__all__ = [
    "DebugController",
    "DebugEvent",
    "DebugState",
    "PromptInterceptor",
]
