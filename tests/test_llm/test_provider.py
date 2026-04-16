"""Tests for strata.llm.provider — LLMProvider Protocol and OpenAI-compat implementation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import icontract
import pytest

from strata.core.config import LLMProviderConfig
from strata.core.errors import LLMAPIError
from strata.llm.provider import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    OpenAICompatProvider,
    _message_to_openai,
)


def _make_config() -> LLMProviderConfig:
    return LLMProviderConfig(
        api_key="sk-test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
    )


def _mock_response(
    content: str = "hello",
    model: str = "test-model",
    finish_reason: str = "stop",
) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage = usage
    return resp


class TestProviderInitFromConfig:
    def test_init_sets_model(self) -> None:
        cfg = _make_config()
        provider = OpenAICompatProvider(cfg)
        assert provider.model_name == "test-model"


class TestProviderProtocolConformance:
    def test_isinstance_check(self) -> None:
        cfg = _make_config()
        provider = OpenAICompatProvider(cfg)
        assert isinstance(provider, LLMProvider)


class TestProviderChatMock:
    @patch("strata.llm.provider.openai.OpenAI")
    def test_chat_returns_response(self, mock_openai_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _mock_response()

        provider = OpenAICompatProvider(_make_config())
        msgs = [ChatMessage(role="user", content="hello")]
        result = provider.chat(msgs)

        assert isinstance(result, ChatResponse)
        assert result.content == "hello"
        assert result.model == "test-model"
        assert result.usage["total_tokens"] == 15

    @patch("strata.llm.provider.openai.OpenAI")
    def test_chat_api_error_wraps(self, mock_openai_cls: MagicMock) -> None:
        import openai as openai_mod

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai_mod.APIError(
            message="rate limit",
            request=MagicMock(),
            body=None,
        )

        provider = OpenAICompatProvider(_make_config())
        with pytest.raises(LLMAPIError, match="rate limit"):
            provider.chat([ChatMessage(role="user", content="test")])


class TestProviderContractViolations:
    def test_empty_messages_contract(self) -> None:
        provider = OpenAICompatProvider(_make_config())
        with pytest.raises(icontract.ViolationError, match="non-empty"):
            provider.chat([])

    def test_temperature_out_of_range(self) -> None:
        provider = OpenAICompatProvider(_make_config())
        with pytest.raises(icontract.ViolationError, match="temperature"):
            provider.chat(
                [ChatMessage(role="user", content="hi")],
                temperature=3.0,
            )


class TestMessageToOpenAI:
    def test_text_only(self) -> None:
        msg = ChatMessage(role="user", content="hello")
        result = _message_to_openai(msg)
        assert result == {"role": "user", "content": "hello"}

    def test_with_images(self) -> None:
        msg = ChatMessage(role="user", content="describe", images=(b"\x89PNG",))
        result = _message_to_openai(msg)
        assert result["role"] == "user"
        content = result["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
