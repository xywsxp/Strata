"""LLM role router — maps logical roles to provider instances.

Each role (planner, grounding, vision, search) is resolved to a concrete
OpenAICompatProvider based on StrataConfig.  Provider instances are cached
to avoid redundant client construction.
"""

from __future__ import annotations

from collections.abc import Sequence

import icontract

from strata.core.config import StrataConfig
from strata.core.errors import ConfigError
from strata.core.types import LLMRole
from strata.llm.provider import ChatMessage, ChatResponse, LLMProvider, OpenAICompatProvider


class LLMRouter:
    """Dispatches LLM calls to the correct provider based on role."""

    @icontract.require(
        lambda config: all(
            getattr(config.roles, r) in config.providers
            for r in ("planner", "grounding", "vision", "search")
        ),
        "all role references must exist in providers",
    )
    def __init__(self, config: StrataConfig) -> None:
        self._config = config
        self._cache: dict[str, OpenAICompatProvider] = {}

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

    def plan(
        self,
        messages: Sequence[ChatMessage],
        **kwargs: object,
    ) -> ChatResponse:
        """Convenience: dispatch to the planner role provider."""
        provider = self.get_provider("planner")
        return provider.chat(messages, **kwargs)  # type: ignore[arg-type]

    def ground(
        self,
        messages: Sequence[ChatMessage],
        **kwargs: object,
    ) -> ChatResponse:
        """Convenience: dispatch to the grounding role provider."""
        provider = self.get_provider("grounding")
        return provider.chat(messages, **kwargs)  # type: ignore[arg-type]

    def see(
        self,
        messages: Sequence[ChatMessage],
        **kwargs: object,
    ) -> ChatResponse:
        """Convenience: dispatch to the vision role provider."""
        provider = self.get_provider("vision")
        return provider.chat(messages, **kwargs)  # type: ignore[arg-type]

    def search(
        self,
        messages: Sequence[ChatMessage],
        **kwargs: object,
    ) -> ChatResponse:
        """Convenience: dispatch to the search role provider."""
        provider = self.get_provider("search")
        return provider.chat(messages, **kwargs)  # type: ignore[arg-type]
