/**
 * State machine transition tables — exact mirror of Python's
 * strata/harness/state_machine.py VALID_GLOBAL_TRANSITIONS and VALID_TASK_TRANSITIONS.
 *
 * CONVENTION: This file MUST stay in sync with Python. The cross-language test
 * (cross-language.test.ts) reads transitions.json (exported from Python) and
 * verifies byte-for-byte equality.
 */

import type { GlobalState, GlobalEvent, TaskState, TaskEvent } from './api'

export const VALID_GLOBAL_TRANSITIONS: Record<GlobalState, Partial<Record<GlobalEvent, GlobalState>>> = {
  INIT: { receive_goal: 'PLANNING' },
  PLANNING: { plan_ready: 'CONFIRMING', unrecoverable: 'FAILED' },
  CONFIRMING: {
    user_confirm: 'SCHEDULING',
    user_revise: 'PLANNING',
    user_abort: 'FAILED',
  },
  SCHEDULING: { task_dispatched: 'EXECUTING', all_done: 'COMPLETED' },
  EXECUTING: {
    task_done: 'SCHEDULING',
    task_failed: 'RECOVERING',
    user_abort: 'FAILED',
  },
  RECOVERING: {
    recovered: 'SCHEDULING',
    escalated: 'WAITING_USER',
    unrecoverable: 'FAILED',
  },
  WAITING_USER: {
    user_decision: 'SCHEDULING',
    user_abort: 'FAILED',
  },
  COMPLETED: {},
  FAILED: {},
}

export const VALID_TASK_TRANSITIONS: Record<TaskState, Partial<Record<TaskEvent, TaskState>>> = {
  PENDING: { start: 'RUNNING', skip: 'SKIPPED' },
  RUNNING: { succeed: 'SUCCEEDED', fail: 'FAILED' },
  SUCCEEDED: {},
  FAILED: {},
  SKIPPED: {},
}

/**
 * Compute the badge CSS class for a given GlobalState.
 * Total function: every GlobalState value maps to a class.
 */
export function badgeClass(state: GlobalState): string {
  switch (state) {
    case 'COMPLETED': return 'badge-ok'
    case 'FAILED': return 'badge-err'
    case 'EXECUTING':
    case 'SCHEDULING':
    case 'RECOVERING': return 'badge-running'
    case 'PLANNING':
    case 'CONFIRMING': return 'badge-warn'
    case 'WAITING_USER': return 'badge-warn'
    case 'INIT': return 'badge-idle'
  }
}

/**
 * Compute the goal UI state from busy flag and global state.
 * Total function: every (busy, globalState) pair maps to a GoalUiState.
 *
 * BUG FIX: The old panel.html used `!busy && !FAILED → done` which was wrong.
 * Now: done ONLY when globalState === 'COMPLETED'.
 */
export function computeGoalState(busy: boolean, globalState: GlobalState): 'idle' | 'running' | 'done' | 'failed' {
  if (busy) return 'running'
  if (globalState === 'COMPLETED') return 'done'
  if (globalState === 'FAILED') return 'failed'
  return 'idle'
}
