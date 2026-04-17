"""LLMProvider Protocol and OpenAI-compatible implementation.

The Protocol defines a synchronous chat interface. The concrete implementation
wraps the ``openai`` SDK, supporting any API-compatible endpoint (DeepSeek,
Grok, Kimi, etc.).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

import icontract
import openai

from strata.core.config import LLMProviderConfig
from strata.core.errors import (
    LLMAPIError,
    LLMFeatureNotSupportedError,
    LLMPermanentError,
    LLMTransientError,
)

# ── Value objects ──


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str
    images: Sequence[bytes] = field(default_factory=tuple)


@dataclass(frozen=True)
class ChatResponse:
    content: str
    model: str
    usage: Mapping[str, int]
    finish_reason: str


# ── Protocol ──


@runtime_checkable
class LLMProvider(Protocol):
    def chat(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse: ...

    @property
    def model_name(self) -> str: ...


# ── Concrete implementation ──


def _classify_openai_error(exc: openai.APIError) -> LLMAPIError:
    """Classify an OpenAI SDK exception as transient or permanent.

    Transient (safe to retry):
      * network / timeout failures
      * rate-limit (429)
      * server 5xx
    Permanent (retry would not help):
      * authentication / permission (401 / 403)
      * bad request (400)
      * unsupported / not-found (404)
    """
    transient_types = (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )
    permanent_types = (
        openai.AuthenticationError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
        openai.BadRequestError,
        openai.UnprocessableEntityError,
    )
    if isinstance(exc, transient_types):
        return LLMTransientError(f"transient LLM error: {exc}")
    if isinstance(exc, permanent_types):
        return LLMPermanentError(f"permanent LLM error: {exc}")
    return LLMTransientError(f"unclassified OpenAI API error (retrying): {exc}")


def _message_to_openai(msg: ChatMessage) -> dict[str, object]:
    """Convert a ChatMessage to the OpenAI API format.

    For messages with images, constructs multipart content blocks with
    base64-encoded data URLs suitable for vision-capable models.
    """
    if not msg.images:
        return {"role": msg.role, "content": msg.content}

    content_parts: list[dict[str, object]] = [{"type": "text", "text": msg.content}]
    for img_bytes in msg.images:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        )
    return {"role": msg.role, "content": content_parts}


class OpenAICompatProvider:
    """OpenAI-compatible LLM provider (works with DeepSeek, Grok, Kimi, etc.)."""

    def __init__(self, config: LLMProviderConfig) -> None:
        self._config = config
        self._client = openai.OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        self._model = config.model

    @property
    def model_name(self) -> str:
        return self._model

    @icontract.require(lambda messages: len(messages) > 0, "messages must be non-empty")
    @icontract.require(
        lambda temperature: 0.0 <= temperature <= 2.0,
        "temperature must be in [0, 2]",
    )
    @icontract.ensure(lambda result: len(result.content) > 0, "response content must be non-empty")
    def chat(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Send a chat completion request to the provider."""
        openai_messages = [_message_to_openai(m) for m in messages]

        response_format = {"type": "json_object"} if json_mode else openai.NOT_GIVEN

        try:
            # CONVENTION: type: ignore — we build messages as plain dicts from
            # ChatMessage; the openai SDK accepts them at runtime but its type
            # stubs demand vendor-specific TypedDicts we intentionally avoid.
            response = self._client.chat.completions.create(  # type: ignore[call-overload]
                model=self._model,
                messages=openai_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except openai.APIError as exc:
            raise _classify_openai_error(exc) from exc
        except Exception as exc:
            raise LLMTransientError(f"LLM call failed: {exc}") from exc

        choice = response.choices[0]
        content = choice.message.content or ""

        if json_mode and not content.strip():
            raise LLMFeatureNotSupportedError(
                f"json_mode requested but provider returned empty content (model={self._model})"
            )

        usage_dict: dict[str, int] = {}
        if response.usage:
            usage_dict = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return ChatResponse(
            content=content,
            model=response.model,
            usage=usage_dict,
            finish_reason=choice.finish_reason or "unknown",
        )
