"""Strata — FV-first autonomous desktop agent framework (pure VLM perception)."""

from typing import Final

__version__: Final[str] = "0.1.0"


class StrataError(Exception):
    """Root exception for the entire strata package.

    All strata subpackage exceptions must inherit from this class.
    External callers can ``except StrataError`` to catch any framework error.
    """
