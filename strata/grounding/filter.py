"""Sensitive-information detection — VLM pre-send guard.

``contains_sensitive`` is called at the entry of :class:`VisionLocator.locate`
to prevent descriptions containing passwords, tokens, or secrets from being
sent to a cloud VLM endpoint.

# CONVENTION: extra_patterns 语义从子串改为正则 — breaking change，调用方需
# 自行 re.escape 包装字面量；无自动回退。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Final

import icontract

# Built-in secret-shape regexes (word-bounded keyword forms + concrete token
# shapes). Each pattern is intentionally narrow to minimize false positives.
_BUILTIN_PATTERNS: Final[tuple[str, ...]] = (
    # Keyword indicators with explicit key=value / key: value syntax
    r"(?i)\b(?:api[_-]?key|apikey|password|passwd|secret|token|credential|"
    r"private[_-]?key)\b\s*[=:]",
    # Keyword on its own (e.g. a user asking to "type my password")
    r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|apikey|credential|"
    r"private[_-]?key|ssn|credit[_-]?card)\b",
    # OpenAI-style API keys: sk-... (32+ chars)
    r"sk-[A-Za-z0-9_\-]{20,}",
    # AWS access key IDs: AKIA / ASIA followed by 16 uppercase alphanum
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    # JWT: three dot-separated base64url segments
    r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b",
    # HTTP Bearer tokens
    r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=]{16,}",
)

# Convenience re-export (read-only tuple of keyword roots, used for awareness
# in tests / docs — NOT for matching; matching goes through the regexes above).
SENSITIVE_KEYWORDS: Final[Sequence[str]] = (
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


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _compiled_patterns(extra: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(_compile(p) for p in (*_BUILTIN_PATTERNS, *extra))


@icontract.require(
    lambda extra_patterns: all(isinstance(p, str) for p in extra_patterns),
    "extra_patterns must be strings",
)
def contains_sensitive(text: str, extra_patterns: Sequence[str] = ()) -> bool:
    """Return True if *text* matches any built-in or extra sensitive pattern.

    ``extra_patterns`` are interpreted as **regular expressions** (breaking
    change from the previous substring semantics; wrap literals with
    :func:`re.escape` if needed).
    """
    patterns = _compiled_patterns(tuple(extra_patterns))
    return any(p.search(text) is not None for p in patterns)


@icontract.require(
    lambda extra_patterns: all(isinstance(p, str) for p in extra_patterns),
    "extra_patterns must be strings",
)
def redact(text: str, extra_patterns: Sequence[str] = ()) -> str:
    """Replace every match of a sensitive pattern with ``[REDACTED]``.

    Like :func:`contains_sensitive`, ``extra_patterns`` are regular
    expressions. Applied iteratively until no further substitutions occur so
    that overlapping matches collapse cleanly (idempotent fixpoint).
    """
    patterns = _compiled_patterns(tuple(extra_patterns))
    current = text
    for _ in range(8):  # fixpoint cap — in practice converges in 1-2 passes
        previous = current
        for pat in patterns:
            current = pat.sub("[REDACTED]", current)
        if current == previous:
            break
    return current
