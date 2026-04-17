"""Tests for strata.observability.transcript — ChatTranscriptSink."""

from __future__ import annotations

import json
from pathlib import Path

import icontract
import pytest

from strata.llm.provider import ChatMessage, ChatResponse
from strata.observability.transcript import (
    ChatTranscriptSink,
    FileChatTranscriptSink,
    NullTranscriptSink,
)


class TestNullTranscriptSink:
    def test_implements_protocol(self) -> None:
        sink = NullTranscriptSink()
        assert isinstance(sink, ChatTranscriptSink)

    def test_record_is_noop(self) -> None:
        sink = NullTranscriptSink()
        msg = ChatMessage(role="user", content="hi")
        resp = ChatResponse(content="ok", model="m", usage={}, finish_reason="stop")
        sink.record("planner", [msg], resp, None)


class TestFileChatTranscriptSink:
    def test_implements_protocol(self, tmp_path: Path) -> None:
        sink = FileChatTranscriptSink(tmp_path / "llm")
        assert isinstance(sink, ChatTranscriptSink)

    def test_writes_req_and_resp_json_files(self, tmp_path: Path) -> None:
        out = tmp_path / "llm"
        sink = FileChatTranscriptSink(out)
        msg = ChatMessage(role="user", content="hello world")
        resp = ChatResponse(
            content="reply", model="test-model", usage={"total_tokens": 10}, finish_reason="stop"
        )
        sink.record("planner", [msg], resp, None)

        req_file = out / "0001_planner_req.json"
        resp_file = out / "0001_planner_resp.json"
        assert req_file.exists()
        assert resp_file.exists()

        req_data = json.loads(req_file.read_text())
        assert req_data["role"] == "planner"
        assert req_data["messages"][0]["content"] == "hello world"

        resp_data = json.loads(resp_file.read_text())
        assert resp_data["content"] == "reply"
        assert resp_data["model"] == "test-model"

    def test_extracts_images_to_png_siblings(self, tmp_path: Path) -> None:
        out = tmp_path / "llm"
        sink = FileChatTranscriptSink(out)
        img1 = b"\x89PNG_fake_image_1"
        img2 = b"\x89PNG_fake_image_2"
        msg = ChatMessage(role="user", content="describe this", images=(img1, img2))
        sink.record("vision", [msg], None, None)

        assert (out / "0001_vision_img_0.png").read_bytes() == img1
        assert (out / "0001_vision_img_1.png").read_bytes() == img2

        req_data = json.loads((out / "0001_vision_req.json").read_text())
        assert req_data["messages"][0]["images"] == [
            "0001_vision_img_0.png",
            "0001_vision_img_1.png",
        ]

    def test_records_error_when_response_is_none(self, tmp_path: Path) -> None:
        out = tmp_path / "llm"
        sink = FileChatTranscriptSink(out)
        msg = ChatMessage(role="system", content="sys prompt")
        err = RuntimeError("API timeout")
        sink.record("grounding", [msg], None, err)

        assert (out / "0001_grounding_req.json").exists()
        assert not (out / "0001_grounding_resp.json").exists()
        err_data = json.loads((out / "0001_grounding_err.json").read_text())
        assert err_data["error_type"] == "RuntimeError"
        assert "API timeout" in err_data["error_message"]

    def test_sequence_numbers_increment(self, tmp_path: Path) -> None:
        out = tmp_path / "llm"
        sink = FileChatTranscriptSink(out)
        msg = ChatMessage(role="user", content="x")
        resp = ChatResponse(content="y", model="m", usage={}, finish_reason="stop")
        sink.record("planner", [msg], resp, None)
        sink.record("vision", [msg], resp, None)

        assert (out / "0001_planner_req.json").exists()
        assert (out / "0002_vision_req.json").exists()

    def test_rejects_empty_role(self, tmp_path: Path) -> None:
        sink = FileChatTranscriptSink(tmp_path)
        msg = ChatMessage(role="user", content="x")
        with pytest.raises(icontract.ViolationError):
            sink.record("", [msg], None, None)

    def test_rejects_empty_messages(self, tmp_path: Path) -> None:
        sink = FileChatTranscriptSink(tmp_path)
        with pytest.raises(icontract.ViolationError):
            sink.record("planner", [], None, None)
