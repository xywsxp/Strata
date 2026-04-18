"""Strata exception hierarchy.

Every subpackage has a dedicated base exception derived from StrataError.
Leaf exceptions are grouped under their respective subpackage base.
Cross-package exception inheritance is forbidden.
"""

from __future__ import annotations

from strata import StrataError

# ── Core / Config ──


class ConfigError(StrataError):
    """Configuration loading, parsing, or validation failure."""


class SerializationError(StrataError):
    """Failure to (de)serialize a value object (task node/graph, checkpoint, ...)."""


# ── Security ──


class SandboxViolationError(StrataError):
    """A file-system operation attempted to escape the sandbox boundary."""


# ── Planner (L1) ──


class PlannerError(StrataError):
    """Task planning or graph manipulation failure."""


# ── Harness (L2) ──


class HarnessError(StrataError):
    """Execution orchestration failure."""


class StateTransitionError(HarnessError):
    """An illegal state-machine transition was attempted."""


class GUILockTimeoutError(HarnessError):
    """Failed to acquire the GUI mutex within the allowed timeout."""


class AdjusterNotAvailableError(HarnessError):
    """The plan adjuster dependency is missing or failed to initialize."""


class MaxIterationsExceededError(HarnessError):
    """A loop (repeat / for_each) exceeded its configured iteration cap."""


class ContextError(HarnessError):
    """Working memory / context extraction failure."""


class PersistenceError(HarnessError):
    """Checkpoint save/load failure."""


class PersistenceSchemaVersionError(PersistenceError):
    """Checkpoint schema version is missing or unsupported."""


# ── Orchestration (L2 subset) ──


class OrchestrationError(HarnessError):
    """Raised when the agent main loop cannot complete a goal lifecycle."""


class UnknownActionError(OrchestrationError):
    """Raised when a TaskNode.action is absent from ACTION_VOCABULARY."""


class ActionParamsError(OrchestrationError):
    """Raised when a TaskNode.params set is missing required keys or wrong types."""


class GoalDecompositionError(OrchestrationError):
    """Raised when ``decompose_goal`` fails terminally after its internal retries."""


class PlanConfirmationAbortedError(OrchestrationError):
    """Raised when the user rejects the plan at the CONFIRMING gate."""


# ── Grounding (L3) ──


class GroundingError(StrataError):
    """Action grounding failure (VLM perception or coordinate processing)."""


class VisionLocatorError(GroundingError):
    """VLM call failed or returned an unparseable response."""


class InvalidCoordinateError(GroundingError):
    """A coordinate fell outside the valid screen boundary."""


class ElementNotFoundError(GroundingError):
    """The target UI element could not be located after exhaustive search."""


class SensitiveContentError(GroundingError):
    """A request contained sensitive information that must not be sent to a cloud VLM."""


# ── Environment (L4) ──


class StrataEnvironmentError(StrataError):
    """Environment adapter failure (within strata namespace — no builtin conflict)."""


class UnsupportedPlatformError(StrataEnvironmentError):
    """The current OS platform has no implemented adapter."""


class CommandTimeoutError(StrataEnvironmentError):
    """A terminal command exceeded its wall-clock timeout."""


class SilenceTimeoutError(CommandTimeoutError):
    """A terminal command produced no output for longer than the silence threshold."""


class OSWorldConnectionError(StrataEnvironmentError):
    """Failed to connect to the OSWorld Docker/VM backend."""


# ── LLM ──


class LLMError(StrataError):
    """LLM provider call or configuration failure."""


class LLMAPIError(LLMError):
    """Wraps provider SDK exceptions (network, auth, quota, etc.)."""


class LLMTransientError(LLMAPIError):
    """Transient LLM failure safe to retry (network, rate-limit, 5xx)."""


class LLMPermanentError(LLMAPIError):
    """Permanent LLM failure — retry would not help (auth, 4xx, malformed)."""


class LLMFeatureNotSupportedError(LLMError):
    """The requested feature (e.g. json_mode) is not supported by this provider."""


# ── Interaction (L0) ──


class InteractionError(StrataError):
    """User interaction layer failure."""


# ── Debug ──


class DebugError(StrataError):
    """Debug subsystem failure."""


class DebugServerError(DebugError):
    """HTTP/WS server lifecycle error (start/stop/bind)."""


class DebugRollbackError(DebugError):
    """Rollback operation failed (invalid version, empty undo stack, etc.)."""
