"""Layer 2: execution orchestration.

Public API surface exported for consumers (``strata.__main__`` / CLI / tests).
Internal helpers remain un-exported; add them explicitly when a new consumer
is introduced.
"""

from strata.harness.actions import (
    ACTION_OPTIONAL_PARAMS,
    ACTION_PARAM_SCHEMA,
    ACTION_VOCABULARY,
    DESTRUCTIVE_ACTIONS,
    ActionName,
    format_action_catalog_for_llm,
)
from strata.harness.context import (
    AuditLogger,
    ContextFact,
    ContextManager,
    LocalContext,
    WorkingMemory,
    extract_local_context,
)
from strata.harness.executor import PrimitiveTaskExecutor
from strata.harness.gui_lock import AtomicGUITransaction, GUILock
from strata.harness.orchestrator import AgentOrchestrator, AgentUI, ExecutionResult
from strata.harness.persistence import Checkpoint, PersistenceManager, atomic_write
from strata.harness.recovery import RecoveryAction, RecoveryLevel, RecoveryPipeline
from strata.harness.scheduler import LinearRunner, TaskExecutor
from strata.harness.state_machine import (
    StateMachine,
    create_global_state_machine,
    create_task_state_machine,
)

__all__ = [
    "ACTION_OPTIONAL_PARAMS",
    "ACTION_PARAM_SCHEMA",
    "ACTION_VOCABULARY",
    "DESTRUCTIVE_ACTIONS",
    "ActionName",
    "AgentOrchestrator",
    "AgentUI",
    "AtomicGUITransaction",
    "AuditLogger",
    "Checkpoint",
    "ContextFact",
    "ContextManager",
    "ExecutionResult",
    "GUILock",
    "LinearRunner",
    "LocalContext",
    "PersistenceManager",
    "PrimitiveTaskExecutor",
    "RecoveryAction",
    "RecoveryLevel",
    "RecoveryPipeline",
    "StateMachine",
    "TaskExecutor",
    "WorkingMemory",
    "atomic_write",
    "create_global_state_machine",
    "create_task_state_machine",
    "extract_local_context",
    "format_action_catalog_for_llm",
]
