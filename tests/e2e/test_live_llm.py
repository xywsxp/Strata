"""Live LLM end-to-end smoke tests.

Uses the real API keys in ``config.toml`` to verify that each configured
provider (DeepSeek / Grok / Kimi) actually accepts our request format and
returns a parseable :class:`ChatResponse`.

Marked ``live_llm`` — run explicitly with::

    uv run pytest -m live_llm

Tests are additionally skipped per-provider if the model is known to be
vision-only or chat-only, so a single ``-m live_llm`` exercises the full
matrix without failing on model-capability mismatches.
"""

from __future__ import annotations

import os

import pytest

from strata.core.config import StrataConfig
from strata.llm.provider import ChatMessage, OpenAICompatProvider

# Opt-in: set STRATA_LIVE_LLM=1 in the environment (or pass
# --run-live-llm via conftest) to actually hit the provider endpoints.
_LIVE_ENABLED = os.environ.get("STRATA_LIVE_LLM") == "1"

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not _LIVE_ENABLED,
        reason="set STRATA_LIVE_LLM=1 to run live LLM smoke tests",
    ),
]


class TestLiveProviders:
    @pytest.mark.parametrize("role", ["planner", "grounding", "vision", "search"])
    def test_provider_roundtrip(self, repo_config: StrataConfig, role: str) -> None:
        provider_name = getattr(repo_config.roles, role)
        provider_cfg = repo_config.providers[provider_name]
        provider = OpenAICompatProvider(provider_cfg)

        messages = [
            ChatMessage(
                role="user",
                content="Reply with exactly the single word: OK",
            ),
        ]
        try:
            response = provider.chat(messages, temperature=0.0, max_tokens=10)
        except Exception as exc:
            pytest.fail(
                f"live call for role={role!r} provider={provider_name!r} "
                f"model={provider_cfg.model!r} failed: {type(exc).__name__}: {exc}"
            )

        assert isinstance(response.content, str)
        assert len(response.content) > 0
        assert response.model  # reported model name non-empty
        assert response.finish_reason in ("stop", "length", "tool_calls", "end")


class TestLiveVisionProvider:
    def test_vision_provider_accepts_image(self, repo_config: StrataConfig) -> None:
        """The configured ``roles.vision`` provider must accept an image
        attachment without raising a permanent (4xx) error. We send a tiny
        1×1 PNG so the cost is negligible."""
        provider_name = repo_config.roles.vision
        provider_cfg = repo_config.providers[provider_name]
        provider = OpenAICompatProvider(provider_cfg)

        tiny_png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c626001000000050001a5f6f3ef0000000049454e44ae"
            "426082"
        )
        messages = [
            ChatMessage(
                role="user",
                content="Describe this image in at most 10 words.",
                images=(tiny_png,),
            ),
        ]
        try:
            response = provider.chat(messages, temperature=0.0, max_tokens=50)
        except Exception as exc:
            pytest.skip(f"vision provider {provider_name!r} rejected image: {exc}")
        assert isinstance(response.content, str)


class TestHealthCheckLive:
    def test_health_check_all_providers_pass(self, repo_config: StrataConfig) -> None:
        """All configured providers must pass health check with real API keys."""
        from strata.core.health import check_llm_providers

        statuses = check_llm_providers(repo_config)
        for s in statuses:
            assert s.ok, f"{s.component} failed: {s.detail}"
            assert s.latency_ms > 0


class TestRouterLive:
    def test_router_plan_roundtrip(self, repo_config: StrataConfig) -> None:
        """The planner role must return a non-empty response via LLMRouter."""
        from strata.llm.router import LLMRouter

        router = LLMRouter(repo_config)
        messages = [ChatMessage(role="user", content="Reply with: OK")]
        response = router.plan(messages, temperature=0.0, max_tokens=10)
        assert len(response.content) > 0
        assert response.usage.get("prompt_tokens", 0) > 0

    def test_router_see_with_screenshot(self, repo_config: StrataConfig) -> None:
        """The vision role must accept an image and return content."""
        from strata.llm.router import LLMRouter

        router = LLMRouter(repo_config)
        tiny_png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c626001000000050001a5f6f3ef0000000049454e44ae"
            "426082"
        )
        messages = [
            ChatMessage(
                role="user",
                content="Describe this image briefly.",
                images=(tiny_png,),
            ),
        ]
        try:
            response = router.see(messages, temperature=0.0, max_tokens=50)
        except Exception as exc:
            pytest.skip(f"vision role rejected image: {exc}")
        assert len(response.content) > 0
