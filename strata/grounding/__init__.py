"""Layer 3: action grounding (pure VLM — no accessibility API)."""

from strata.grounding.filter import contains_sensitive, redact
from strata.grounding.scaler import CoordinateScaler
from strata.grounding.terminal_handler import TerminalHandler
from strata.grounding.validator import ActionValidator
from strata.grounding.vision_locator import VisionLocator

__all__ = [
    "ActionValidator",
    "CoordinateScaler",
    "TerminalHandler",
    "VisionLocator",
    "contains_sensitive",
    "redact",
]
