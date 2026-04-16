"""Entry point for ``python -m strata`` and the ``strata`` console script."""

from __future__ import annotations

import argparse
import sys

from strata.core.config import load_config
from strata.core.errors import ConfigError
from strata.interaction.cli import CLI


def main() -> None:
    """Parse --config and launch the CLI loop."""
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
        print(f"[Strata] Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    cli = CLI(config)
    cli.run()


if __name__ == "__main__":
    main()
