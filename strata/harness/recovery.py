"""5-level error recovery pipeline.

Levels: RETRY → ALTERNATIVE → REPLAN → SKIP → USER_INTERVENTION.
Attempt count thresholds are hardcoded for predictable, debuggable behavior.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import icontract

from strata.core.config import StrataConfig
from strata.core.errors import PlannerError
from strata.core.types import TaskNode


class RecoveryLevel(enum.IntEnum):
    RETRY = 1
    ALTERNATIVE = 2
    REPLAN = 3
    SKIP = 4
    USER_INTERVENTION = 5


@dataclass(frozen=True)
class RecoveryAction:
    level: RecoveryLevel
    description: str
    replacement_tasks: Sequence[TaskNode] = ()


class RecoveryPipeline:
    """Escalating recovery: retry → alternative → replan → skip → user."""

    def __init__(
        self,
        config: StrataConfig,
        adjuster: Callable[[TaskNode, Exception], Sequence[TaskNode]],
    ) -> None:
        self._config = config
        self._adjuster = adjuster

    # CONVENTION: Recovery thresholds are hardcoded rather than read from
    # StrataConfig — StrataConfig has no recovery-specific section yet.
    # Thresholds: ≤1 → RETRY, 2 → ALTERNATIVE, 3 → REPLAN, 4 → SKIP, ≥5 → USER.
    # attempt_count=0 means first failure (before any retry), so 0 and 1 both
    # map to RETRY giving exactly one retry attempt before escalating.
    _RETRY_THRESHOLD: int = 1
    _ALTERNATIVE_THRESHOLD: int = 2
    _REPLAN_THRESHOLD: int = 3
    _SKIP_THRESHOLD: int = 4

    @icontract.require(lambda attempt_count: attempt_count >= 0, "attempt_count must be >= 0")
    def attempt_recovery(
        self,
        failed_task: TaskNode,
        error: Exception,
        attempt_count: int,
    ) -> RecoveryAction:
        """Determine recovery action based on attempt count (monotonically escalating)."""
        if attempt_count <= self._RETRY_THRESHOLD:
            return RecoveryAction(
                level=RecoveryLevel.RETRY,
                description=f"retry task {failed_task.id} (attempt {attempt_count})",
            )

        if attempt_count == self._ALTERNATIVE_THRESHOLD:
            return RecoveryAction(
                level=RecoveryLevel.ALTERNATIVE,
                description=f"try alternative for {failed_task.id}",
            )

        if attempt_count == self._REPLAN_THRESHOLD:
            return self._try_replan(failed_task, error)

        if attempt_count == self._SKIP_THRESHOLD:
            return RecoveryAction(
                level=RecoveryLevel.SKIP,
                description=f"skip task {failed_task.id}",
            )

        return RecoveryAction(
            level=RecoveryLevel.USER_INTERVENTION,
            description=f"escalate {failed_task.id} to user",
        )

    def _try_replan(self, failed_task: TaskNode, error: Exception) -> RecoveryAction:
        try:
            replacements = self._adjuster(failed_task, error)
        except PlannerError:
            return RecoveryAction(
                level=RecoveryLevel.SKIP,
                description=f"adjuster failed for {failed_task.id}, skipping",
            )

        if not replacements:
            return RecoveryAction(
                level=RecoveryLevel.SKIP,
                description=f"adjuster returned empty for {failed_task.id}, skipping",
            )

        return RecoveryAction(
            level=RecoveryLevel.REPLAN,
            description=f"replan {failed_task.id} with {len(replacements)} replacements",
            replacement_tasks=tuple(replacements),
        )
