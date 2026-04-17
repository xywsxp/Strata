"""Entry point for ``python -m strata`` and the ``strata`` console script."""

from __future__ import annotations

import argparse
import sys

from strata import StrataError
from strata.core.config import load_config
from strata.core.errors import ConfigError
from strata.env.factory import EnvironmentFactory
from strata.grounding.filter import redact
from strata.harness.orchestrator import AgentOrchestrator
from strata.interaction.cli import CLI


def main() -> None:
    """Parse --config, build environment bundle, launch the CLI loop.

    Exit-code contract (三档兜底):
      * ``0`` — normal exit
      * ``1`` — ``ConfigError`` (user-actionable misconfiguration)
      * ``2`` — other ``StrataError`` subclasses (domain failure)
      * ``3`` — unexpected ``Exception`` (bug / environment corruption)
      * ``130`` — ``KeyboardInterrupt`` (SIGINT, POSIX convention)

    All error messages are passed through ``redact`` before printing to avoid
    leaking API keys / tokens that may appear in exception strings.
    """
    parser = argparse.ArgumentParser(
        prog="strata",
        description="Strata — FV-first autonomous desktop agent",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config TOML (default: ~/.strata/config.toml)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"[Strata] Config error: {redact(str(exc))}", file=sys.stderr)
        sys.exit(1)

    from strata.health import check_all, require_healthy

    statuses = check_all(config)
    for s in statuses:
        mark = "+" if s.ok else "!"
        print(f"[{mark}] {s.component}: {s.detail} ({s.latency_ms:.0f}ms)")
    require_healthy(statuses)

    try:
        bundle = EnvironmentFactory.create(config)
    except StrataError as exc:
        print(
            f"[Strata] Environment error: {redact(str(exc))}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        cli = CLI(config, bundle=bundle)
        orchestrator = AgentOrchestrator(config=config, bundle=bundle, ui=cli)
        cli.run(orchestrator)
    except KeyboardInterrupt:
        print("\n[Strata] Interrupted.", file=sys.stderr)
        sys.exit(130)
    except ConfigError as exc:
        print(f"[Strata] Config error: {redact(str(exc))}", file=sys.stderr)
        sys.exit(1)
    except StrataError as exc:
        print(f"[Strata] Error: {redact(str(exc))}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(
            f"[Strata] Unexpected error ({type(exc).__name__}): {redact(str(exc))}",
            file=sys.stderr,
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
