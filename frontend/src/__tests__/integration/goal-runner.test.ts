/**
 * useGoalRunner integration tests — L3 regression.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { useDebugStore } from '../../composables/useDebugStore'
import { useGoalRunner } from '../../composables/useGoalRunner'
import type { StateSnapshot } from '../../types/api'

beforeEach(() => {
  Object.defineProperty(globalThis, 'window', {
    value: {
      location: {
        search: '?token=test',
        protocol: 'http:',
        host: 'localhost:8080',
      },
    },
    writable: true,
    configurable: true,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

const baseSnapshot: StateSnapshot = {
  global_state: 'INIT',
  debug_state: 'INACTIVE',
  task_states: {},
  step_mode: false,
  breakpoints: [],
  debug_enabled: true,
  intercept_prompts: false,
}

describe('useGoalRunner', () => {
  it('test_goal_status_completed_requires_global_completed', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'PLANNING' })
    store.setGoalBusy(false)
    expect(store.goalState.value).toBe('idle')
  })

  it('test_goal_status_failed_on_global_failed', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'FAILED' })
    store.setGoalBusy(false)
    expect(store.goalState.value).toBe('failed')
  })

  it('test_goal_status_running_when_busy', () => {
    const store = useDebugStore()
    store.applyStateSnapshot({ ...baseSnapshot, global_state: 'EXECUTING' })
    store.setGoalBusy(true)
    expect(store.goalState.value).toBe('running')
  })

  it('test_submit_goal_sets_busy', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const store = useDebugStore()
    const wsConnected = ref(true)
    const { submitGoal } = useGoalRunner(store, wsConnected)

    await submitGoal('test goal')

    expect(store.goalBusy.value).toBe(true)
  })

  it('test_cancel_goal_clears_busy', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const store = useDebugStore()
    store.setGoalBusy(true)
    const wsConnected = ref(true)
    const { cancelGoal } = useGoalRunner(store, wsConnected)

    await cancelGoal()

    expect(store.goalBusy.value).toBe(false)
  })
})
