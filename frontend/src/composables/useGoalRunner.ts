/**
 * Goal runner composable — submit/cancel goals, status polling.
 *
 * CONVENTION: goalState is computed by useDebugStore via computeGoalState().
 * This composable manages the busy flag and polling lifecycle.
 */

import { ref, watch, type Ref } from 'vue'
import type { DebugStore } from './useDebugStore'
import { GoalStatusSchema, StateSnapshotSchema } from '../types/api'
import { useApi } from './useApi'

const POLL_INTERVAL = 5000

export function useGoalRunner(
  store: DebugStore,
  wsConnected: Ref<boolean>,
): {
  submitGoal: (goal: string) => Promise<void>
  cancelGoal: () => Promise<void>
} {
  const { get, post } = useApi()
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function submitGoal(goal: string): Promise<void> {
    const trimmed = goal.trim()
    if (trimmed.length === 0) return

    store.setGoalBusy(true)
    await post('/api/goal', { goal: trimmed })
    startPolling()
  }

  async function cancelGoal(): Promise<void> {
    await post('/api/goal/cancel')
    store.setGoalBusy(false)
    stopPolling()
  }

  async function pollStatus(): Promise<void> {
    const status = await get('/api/goal/status', GoalStatusSchema)
    if (status) {
      store.setGoalBusy(status.busy)
      if (!status.busy) {
        // Fetch full state to get correct globalState for goalState computation
        const snap = await get('/api/state', StateSnapshotSchema)
        if (snap) {
          store.applyStateSnapshot(snap)
        }
        stopPolling()
      }
    }
  }

  function startPolling(): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      pollStatus()
    }, POLL_INTERVAL)
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // When WS is connected, rely on WS events; don't poll
  watch(wsConnected, (connected) => {
    if (connected && store.goalBusy.value) {
      stopPolling()
    } else if (!connected && store.goalBusy.value) {
      startPolling()
    }
  })

  return { submitGoal, cancelGoal }
}
