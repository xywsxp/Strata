"""LLM role router — maps logical roles to provider instances.

Each role (planner, grounding, vision, search) is resolved to a concrete
OpenAICompatProvider based on StrataConfig.  Provider instances are cached
to avoid redundant client construction.

An optional :class:`ChatTranscriptSink` is called after every provider
``chat()`` call (success or failure) so that prompts, images, and responses
are persisted for offline debugging.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

import icontract

from strata.core.config import StrataConfig
from strata.core.errors import ConfigError
from strata.core.types import LLMRole
from strata.llm.provider import ChatMessage, ChatResponse, LLMProvider, OpenAICompatProvider
from strata.observability.transcript import ChatTranscriptSink, NullTranscriptSink

if TYPE_CHECKING:
    from strata.debug.controller import PromptInterceptor


class LLMRouter:
    """Dispatches LLM calls to the correct provider based on role."""

    @icontract.require(
        lambda config: all(
            getattr(config.roles, r) in config.providers
            for r in ("planner", "grounding", "vision", "search")
        ),
        "all role references must exist in providers",
    )
    def __init__(
        self,
        config: StrataConfig,
        sink: ChatTranscriptSink | None = None,
        prompt_interceptor: PromptInterceptor | None = None,
    ) -> None:
        self._config = config
        self._cache: dict[str, OpenAICompatProvider] = {}
        self._sink: ChatTranscriptSink = sink if sink is not None else NullTranscriptSink()
        self._prompt_interceptor = prompt_interceptor

        for role_name in ("planner", "grounding", "vision", "search"):
            provider_name: str = getattr(config.roles, role_name)
            if provider_name not in config.providers:
                raise ConfigError(
                    f"roles.{role_name} references '{provider_name}' which is not in [providers]"
                )

        self._build_cache()

    def _build_cache(self) -> None:
        for provider_name, provider_config in self._config.providers.items():
            if provider_name not in self._cache:
                self._cache[provider_name] = OpenAICompatProvider(provider_config)

    def get_provider(self, role: LLMRole) -> LLMProvider:
        """Return the provider instance for the given role."""
        provider_name: str = getattr(self._config.roles, role)
        return self._cache[provider_name]

    def _dispatch(
        self,
        role: LLMRole,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Central dispatch: call provider, record to sink, re-raise on error."""
        if self._prompt_interceptor is not None:
            messages = self._prompt_interceptor.gate(role, messages)
        provider = self.get_provider(role)
        t0 = time.perf_counter()
        try:
            response = provider.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        except Exception as exc:
            dur_ms = (time.perf_counter() - t0) * 1000
            if self._prompt_interceptor is not None and hasattr(
                self._prompt_interceptor, "record_llm_error"
            ):
                self._prompt_interceptor.record_llm_error(role, dur_ms, messages, exc)
            self._sink.record(role, messages, None, exc)
            raise
        dur_ms = (time.perf_counter() - t0) * 1000
        if self._prompt_interceptor is not None and hasattr(
            self._prompt_interceptor, "record_llm_done"
        ):
            self._prompt_interceptor.record_llm_done(role, dur_ms, messages, response)
        self._sink.record(role, messages, response, None)
        return response

    def plan(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Convenience: dispatch to the planner role provider."""
        return self._dispatch(
            "planner", messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )

    def ground(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Convenience: dispatch to the grounding role provider."""
        return self._dispatch(
            "grounding",
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    def see(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Convenience: dispatch to the vision role provider."""
        return self._dispatch(
            "vision", messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )

    def search(
        self,
        messages: Sequence[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        """Convenience: dispatch to the search role provider."""
        return self._dispatch(
            "search", messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )
