"""Sensitive-information detection — VLM pre-send guard.

``contains_sensitive`` is called at the entry of VisionLocator.locate to
prevent descriptions containing passwords, tokens, or secrets from being
sent to a cloud VLM endpoint.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

SENSITIVE_PATTERNS: Final[Sequence[str]] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "api-key",
    "apikey",
    "credential",
    "private_key",
    "private-key",
    "ssn",
    "credit card",
    "credit_card",
    "creditcard",
)


def contains_sensitive(text: str, extra_patterns: Sequence[str] = ()) -> bool:
    """Return True if *text* matches any built-in or extra sensitive pattern."""
    lower = text.lower()
    return any(pat.lower() in lower for pat in (*SENSITIVE_PATTERNS, *extra_patterns))


def redact(text: str, extra_patterns: Sequence[str] = ()) -> str:
    """Replace occurrences of sensitive patterns with ``[REDACTED]``."""
    result = text
    for pat in (*SENSITIVE_PATTERNS, *extra_patterns):
        result = re.sub(re.escape(pat), "[REDACTED]", result, flags=re.IGNORECASE)
    return result
