/**
 * useDebugStore integration tests — L3 regression.
 */

import { describe, it, expect } from 'vitest'
import { useDebugStore } from '../../composables/useDebugStore'
import type { StateSnapshot, WSEvent } from '../../types/api'

const baseSnapshot: StateSnapshot = {
  global_state: 'INIT',
  debug_state: 'INACTIVE',
  task_states: {},
  step_mode: false,
  breakpoints: [],
  debug_enabled: true,
  intercept_prompts: false,
}

describe('useDebugStore', () => {
  it('test_apply_ws_event_partial_update', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'EXECUTING' })
    expect(store.globalState.value).toBe('EXECUTING')

    const ev: WSEvent = {
      event: 'task_done',
      global_state: 'EXECUTING',
      task_states: { t1: 'SUCCEEDED' },
      timestamp: Date.now() / 1000,
    }
    store.applyWSEvent(ev)

    // globalState unchanged
    expect(store.globalState.value).toBe('EXECUTING')
    // task state updated
    expect(store.taskStates.value['t1']).toBe('SUCCEEDED')
  })

  it('test_goal_state_completed_only_when_global_completed', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'PLANNING' })
    store.setGoalBusy(false)
    expect(store.goalState.value).toBe('idle')
    expect(store.goalState.value).not.toBe('done')
  })

  it('test_goal_state_busy_true_always_running', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'FAILED' })
    store.setGoalBusy(true)
    expect(store.goalState.value).toBe('running')
  })

  it('test_goal_state_done_when_completed', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'COMPLETED' })
    store.setGoalBusy(false)
    expect(store.goalState.value).toBe('done')
  })

  it('test_goal_state_failed_when_global_failed', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'FAILED' })
    store.setGoalBusy(false)
    expect(store.goalState.value).toBe('failed')
  })

  it('test_events_bounded', () => {
    const store = useDebugStore()
    for (let i = 0; i < 350; i++) {
      store.pushEvent({
        event: 'task_done',
        global_state: 'EXECUTING',
        task_states: {},
        timestamp: i,
      })
    }
    expect(store.events.value.length).toBeLessThanOrEqual(300)
  })
})
