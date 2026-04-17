"""LLM chat transcript sink — records every LLM request/response to disk.

``ChatTranscriptSink`` is the Protocol; ``FileChatTranscriptSink`` persists
messages as numbered JSON files with images extracted as sibling PNGs.
``NullTranscriptSink`` is the no-op fallback when observability is disabled.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

import icontract

from strata.llm.provider import ChatMessage, ChatResponse


@runtime_checkable
class ChatTranscriptSink(Protocol):
    """Observability hook: records LLM interactions for offline debugging."""

    def record(
        self,
        role: str,
        messages: Sequence[ChatMessage],
        response: ChatResponse | None,
        error: Exception | None,
    ) -> None: ...


class NullTranscriptSink:
    """No-op sink — satisfies the protocol with zero I/O."""

    def record(
        self,
        role: str,
        messages: Sequence[ChatMessage],
        response: ChatResponse | None,
        error: Exception | None,
    ) -> None:
        pass


class FileChatTranscriptSink:
    """Persist LLM interactions as numbered JSON + sibling PNG files.

    File naming: ``<seq:04d>_<role>_req.json``, ``..._resp.json`` (or
    ``..._err.json`` on failure). Images from ``ChatMessage.images`` are
    extracted to ``<seq>_<role>_img_<i>.png`` and referenced by relative
    filename inside the JSON.

    All I/O errors are caught and reported to stderr without propagating
    (observability must never crash the agent).
    """

    @icontract.require(lambda out_dir: out_dir is not None)
    def __init__(self, out_dir: Path) -> None:
        self._out_dir = out_dir
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        with contextlib.suppress(OSError):
            out_dir.mkdir(parents=True, exist_ok=True)

    @icontract.require(lambda role: len(role.strip()) > 0, "role must be non-empty")
    @icontract.require(lambda messages: len(messages) > 0, "messages must be non-empty")
    def record(
        self,
        role: str,
        messages: Sequence[ChatMessage],
        response: ChatResponse | None,
        error: Exception | None,
    ) -> None:
        with self._lock:
            seq = next(self._counter)
        prefix = f"{seq:04d}_{role}"
        try:
            self._write_request(prefix, role, messages)
            if response is not None:
                self._write_response(prefix, response)
            if error is not None:
                self._write_error(prefix, error)
        except OSError as exc:
            with contextlib.suppress(Exception):
                print(
                    f"[strata.transcript] write failed: {exc}",
                    file=sys.stderr,
                )

    def _write_request(self, prefix: str, role: str, messages: Sequence[ChatMessage]) -> None:
        serialized: list[dict[str, object]] = []
        for msg in messages:
            img_refs: list[str] = []
            for i, img_bytes in enumerate(msg.images):
                fname = f"{prefix}_img_{i}.png"
                (self._out_dir / fname).write_bytes(img_bytes)
                img_refs.append(fname)
            entry: dict[str, object] = {
                "role": msg.role,
                "content": msg.content,
            }
            if img_refs:
                entry["images"] = img_refs
            serialized.append(entry)

        payload: dict[str, object] = {
            "role": role,
            "messages": serialized,
        }
        path = self._out_dir / f"{prefix}_req.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_response(self, prefix: str, response: ChatResponse) -> None:
        payload: dict[str, object] = {
            "content": response.content,
            "model": response.model,
            "usage": dict(response.usage),
            "finish_reason": response.finish_reason,
        }
        path = self._out_dir / f"{prefix}_resp.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_error(self, prefix: str, error: Exception) -> None:
        payload: dict[str, object] = {
            "error_type": type(error).__name__,
            "error_message": str(error),
        }
        path = self._out_dir / f"{prefix}_err.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
