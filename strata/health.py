"""Startup health checks for LLM providers and OSWorld connection.

Health check functions never raise — they catch all errors and return
``HealthStatus(ok=False, detail=...)``. The ``require_healthy`` helper
exits the process when any component is unhealthy (fail-fast semantics).
"""

from __future__ import annotations

import json as _json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

import icontract

from strata.core.config import StrataConfig
from strata.llm.provider import ChatMessage, OpenAICompatProvider

_HEALTH_TIMEOUT: float = 5.0


@dataclass(frozen=True)
class HealthStatus:
    component: str
    ok: bool
    detail: str
    latency_ms: float


@icontract.require(lambda config: len(config.providers) > 0, "providers must be non-empty")
@icontract.ensure(
    lambda config, result: len(result) == len(config.providers),
    "must return one status per provider",
)
def check_llm_providers(config: StrataConfig) -> Sequence[HealthStatus]:
    """Ping each configured LLM provider with a minimal chat call."""
    statuses: list[HealthStatus] = []
    ping_msg = ChatMessage(role="user", content="Reply with exactly: OK")

    for name, prov_config in config.providers.items():
        t0 = time.monotonic()
        try:
            provider = OpenAICompatProvider(prov_config)
            provider.chat([ping_msg], temperature=0.0, max_tokens=50)
            latency = (time.monotonic() - t0) * 1000
            statuses.append(
                HealthStatus(
                    component=f"llm/{name} ({prov_config.model})",
                    ok=True,
                    detail="ok",
                    latency_ms=latency,
                )
            )
        except icontract.ViolationError:
            # CONVENTION: reasoning models (e.g. deepseek-reasoner) may return
            # empty content because all tokens went to the reasoning chain.
            # A successful API roundtrip is sufficient proof of connectivity.
            latency = (time.monotonic() - t0) * 1000
            statuses.append(
                HealthStatus(
                    component=f"llm/{name} ({prov_config.model})",
                    ok=True,
                    detail="ok (reasoning model, empty content expected)",
                    latency_ms=latency,
                )
            )
        except Exception as exc:
            latency = (time.monotonic() - t0) * 1000
            statuses.append(
                HealthStatus(
                    component=f"llm/{name} ({prov_config.model})",
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=latency,
                )
            )

    return tuple(statuses)


@icontract.require(lambda config: config.osworld.enabled, "osworld must be enabled")
def check_osworld(config: StrataConfig) -> HealthStatus:
    """Verify OSWorld Docker server reachability via POST /screen_size."""
    base_url = config.osworld.server_url.rstrip("/")
    t0 = time.monotonic()
    try:
        data = _json.dumps({}).encode("utf-8")
        req = urllib.request.Request(
            base_url + "/screen_size",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT) as resp:
            body = _json.loads(resp.read())
        latency = (time.monotonic() - t0) * 1000
        width = body.get("width", "?")
        height = body.get("height", "?")
        return HealthStatus(
            component=f"osworld ({base_url})",
            ok=True,
            detail=f"screen {width}x{height}",
            latency_ms=latency,
        )
    except Exception as exc:
        latency = (time.monotonic() - t0) * 1000
        return HealthStatus(
            component=f"osworld ({base_url})",
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=latency,
        )


def check_all(config: StrataConfig) -> Sequence[HealthStatus]:
    """Run all applicable health checks based on config."""
    statuses: list[HealthStatus] = []
    if config.providers:
        statuses.extend(check_llm_providers(config))
    if config.osworld.enabled:
        statuses.append(check_osworld(config))
    return tuple(statuses)


def require_healthy(
    statuses: Sequence[HealthStatus],
    required_components: Sequence[str] | None = None,
) -> None:
    """Exit the process if any required health check failed.

    When ``required_components`` is given, only those component names (substring
    match) trigger a hard exit. Unused providers failing is a warning, not fatal.
    """
    failed = [s for s in statuses if not s.ok]
    if not failed:
        return

    if required_components is not None:
        critical = [s for s in failed if any(rc in s.component for rc in required_components)]
        warnings = [s for s in failed if s not in critical]
        for s in warnings:
            print(f"[Strata] Warning: {s.component} unhealthy: {s.detail}", file=sys.stderr)
        if not critical:
            return
        failed = critical

    print("[Strata] Health check failed:", file=sys.stderr)
    for s in failed:
        print(f"  [{s.component}] {s.detail}", file=sys.stderr)
    sys.exit(1)
