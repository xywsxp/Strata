"""Entry point for ``python -m strata`` and the ``strata`` console script."""

from __future__ import annotations

import argparse
import contextlib
import sys
import threading

from strata import StrataError
from strata.core.config import load_config, load_config_with_overlays
from strata.core.errors import ConfigError
from strata.env.factory import EnvironmentFactory
from strata.grounding.filter import redact
from strata.harness.orchestrator import AgentOrchestrator, HeadlessUI
from strata.interaction.cli import CLI


def main() -> None:
    """Parse --config, build environment bundle, launch the CLI or headless loop.

    Exit-code contract (三档兜底):
      * ``0`` — normal exit
      * ``1`` — ``ConfigError`` (user-actionable misconfiguration)
      * ``2`` — other ``StrataError`` subclasses (domain failure)
      * ``3`` — unexpected ``Exception`` (bug / environment corruption)
      * ``130`` — ``KeyboardInterrupt`` (SIGINT, POSIX convention)

    When ``--headless`` is passed (or ``debug.enabled = true`` without
    ``--no-headless``), the CLI REPL is skipped; the debug server handles goal
    submission and the main thread blocks until Ctrl+C.
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
    parser.add_argument(
        "--debug-config",
        type=str,
        default=None,
        metavar="PATH",
        help="Optional debug overlay TOML (e.g. config/debug.toml); "
        "merges [debug] section on top of the base config.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=None,
        help="Skip the CLI REPL; only the debug server accepts goals. "
        "Auto-enabled when debug.enabled = true.",
    )
    args = parser.parse_args()

    try:
        if args.debug_config:
            config = load_config_with_overlays(args.config, args.debug_config)
        else:
            config = load_config(args.config)
    except ConfigError as exc:
        print(f"[Strata] Config error: {redact(str(exc))}", file=sys.stderr)
        sys.exit(1)

    headless: bool = args.headless if args.headless is not None else config.debug.enabled

    from strata.core.health import check_all, require_healthy

    statuses = check_all(config)
    for s in statuses:
        mark = "+" if s.ok else "!"
        print(f"[{mark}] {s.component}: {s.detail} ({s.latency_ms:.0f}ms)")
    used_providers = {
        getattr(config.roles, r) for r in ("planner", "grounding", "vision", "search")
    }
    required = [f"llm/{p}" for p in used_providers] + (
        ["osworld"] if config.osworld.enabled else []
    )
    require_healthy(statuses, required_components=required)

    try:
        bundle = EnvironmentFactory.create(config)
    except StrataError as exc:
        print(
            f"[Strata] Environment error: {redact(str(exc))}",
            file=sys.stderr,
        )
        sys.exit(2)

    orchestrator: AgentOrchestrator | None = None
    try:
        if headless:
            ui = HeadlessUI()
            orchestrator = AgentOrchestrator(config=config, bundle=bundle, ui=ui)
            port = config.debug.port
            token = config.debug.token
            print(
                f"[Strata] Debug panel → http://localhost:{port}/?token={token}",
                flush=True,
            )
            print("[Strata] Submit goals via the debug panel. Ctrl+C to stop.", flush=True)
            stop = threading.Event()
            with contextlib.suppress(KeyboardInterrupt):
                stop.wait()
        else:
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
    finally:
        if orchestrator is not None:
            orchestrator.shutdown()


if __name__ == "__main__":
    main()
