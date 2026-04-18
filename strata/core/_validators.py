"""Internal Literal-deserialization whitelist validator.

Not exported via ``core/__init__.py``.
"""

from __future__ import annotations

from typing import Final

import icontract

from strata.core.errors import ConfigError, SerializationError

# Re-usable valid-value sets, co-located with the validator to keep
# Literal ↔ frozenset in sync.

VALID_TASK_TYPES: Final[frozenset[str]] = frozenset(
    {"primitive", "compound", "repeat", "if_then", "for_each"}
)

VALID_TASK_STATES: Final[frozenset[str]] = frozenset(
    {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"}
)

VALID_GLOBAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "INIT",
        "PLANNING",
        "CONFIRMING",
        "SCHEDULING",
        "EXECUTING",
        "RECOVERING",
        "WAITING_USER",
        "COMPLETED",
        "FAILED",
    }
)

VALID_AUTO_CONFIRM: Final[frozenset[str]] = frozenset({"none", "low", "medium", "high"})

VALID_OSWORLD_PROVIDERS: Final[frozenset[str]] = frozenset({"vmware", "virtualbox", "docker"})

VALID_SETUP_TARGETS: Final[frozenset[str]] = frozenset({"host", "osworld"})


@icontract.require(lambda valid: len(valid) > 0, "valid set must be non-empty")
@icontract.ensure(lambda result, valid: result in valid, "result must be in valid set")
def validate_literal(
    value: str,
    valid: frozenset[str],
    field_name: str,
    *,
    fallback: str | None = None,
    config_error: bool = False,
) -> str:
    """Validate *value* is in *valid*; return it or *fallback*.

    Raises ``SerializationError`` by default, or ``ConfigError`` when
    *config_error* is ``True``.
    """
    if value in valid:
        return value
    if fallback is not None:
        return fallback
    exc_cls = ConfigError if config_error else SerializationError
    raise exc_cls(f"invalid {field_name}: {value!r}; expected one of {sorted(valid)}")
