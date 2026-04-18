#!/usr/bin/env python3
"""Export Python state machine transition tables to JSON for cross-language verification.

Usage:
    uv run python scripts/export_transitions.py

Output: frontend/src/types/transitions.json
"""

from __future__ import annotations

import json
from pathlib import Path

from strata.harness.state_machine import VALID_GLOBAL_TRANSITIONS, VALID_TASK_TRANSITIONS

OUTFILE = Path(__file__).resolve().parent.parent / "frontend" / "src" / "types" / "transitions.json"


def main() -> None:
    data = {
        "global": {state: dict(events) for state, events in VALID_GLOBAL_TRANSITIONS.items()},
        "task": {state: dict(events) for state, events in VALID_TASK_TRANSITIONS.items()},
    }
    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUTFILE}")


if __name__ == "__main__":
    main()
