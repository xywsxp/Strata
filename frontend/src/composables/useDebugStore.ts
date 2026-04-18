/**
 * Reactive debug store — single source of truth for all UI state.
 *
 * Replaces the old panel.html globals: lastGlobalState, lastStateChangeTs,
 * microEvents, activeTab, _cachedGraphData, _cachedTaskStates, etc.
 *
 * CONVENTION: goalState is computed via computeGoalState() from state-machine.ts.
 * BUG FIX: goalState === 'done' ONLY when globalState === 'COMPLETED'.
 */

import { ref, computed, type Ref, type ComputedRef } from 'vue'
import type {
  GlobalState, TaskState, DebugState, GoalUiState,
  StateSnapshot, WSEvent, TaskGraph, LLMRecordSummary,
} from '../types/api'
import { GlobalStateEnum, TaskStateEnum } from '../types/api'
import { computeGoalState } from '../types/state-machine'

const MAX_EVENTS = 300
const MAX_MICRO_EVENTS = 20
const MAX_LLM_CACHE = 50

export interface DebugStore {
  // State
  globalState: Ref<GlobalState>
  debugState: Ref<DebugState>
  taskStates: Ref<Record<string, TaskState>>
  breakpoints: Ref<string[]>
  stepMode: Ref<boolean>
  interceptEnabled: Ref<boolean>
  activeTab: Ref<'graph' | 'screenshot' | 'prompt'>
  graphData: Ref<TaskGraph | null>
  events: Ref<WSEvent[]>
  microEvents: Ref<WSEvent[]>
  llmRecords: Ref<LLMRecordSummary[]>
  goalBusy: Ref<boolean>
  goalState: ComputedRef<GoalUiState>
  lastWsMessageTs: Ref<number>
  lastStateChangeTs: Ref<number>
  llmDetailCache: Ref<Map<number, unknown>>

  // Actions
  applyStateSnapshot: (snap: StateSnapshot) => void
  applyWSEvent: (ev: WSEvent) => void
  pushEvent: (ev: WSEvent) => void
  setGoalBusy: (busy: boolean) => void
  setGraphData: (graph: TaskGraph | null) => void
}

export function useDebugStore(): DebugStore {
  const globalState = ref<GlobalState>('INIT')
  const debugState = ref<DebugState>('INACTIVE')
  const taskStates = ref<Record<string, TaskState>>({})
  const breakpoints = ref<string[]>([])
  const stepMode = ref(false)
  const interceptEnabled = ref(false)
  const activeTab = ref<'graph' | 'screenshot' | 'prompt'>('graph')
  const graphData = ref<TaskGraph | null>(null)
  const events = ref<WSEvent[]>([])
  const microEvents = ref<WSEvent[]>([])
  const llmRecords = ref<LLMRecordSummary[]>([])
  const goalBusy = ref(false)
  const lastWsMessageTs = ref(0)
  const lastStateChangeTs = ref(0)
  const llmDetailCache = ref(new Map<number, unknown>())

  const goalState = computed<GoalUiState>(() =>
    computeGoalState(goalBusy.value, globalState.value),
  )

  function applyStateSnapshot(snap: StateSnapshot): void {
    const parsedGs = GlobalStateEnum.safeParse(snap.global_state)
    if (parsedGs.success) {
      if (globalState.value !== parsedGs.data) {
        lastStateChangeTs.value = Date.now()
      }
      globalState.value = parsedGs.data
    }
    debugState.value = snap.debug_state
    taskStates.value = { ...snap.task_states }
    breakpoints.value = [...snap.breakpoints]
    stepMode.value = snap.step_mode
    if (snap.intercept_prompts !== undefined) {
      interceptEnabled.value = snap.intercept_prompts
    }
  }

  function applyWSEvent(ev: WSEvent): void {
    lastWsMessageTs.value = Date.now()

    // Update global state if present and valid
    const parsedGs = GlobalStateEnum.safeParse(ev.global_state)
    if (parsedGs.success) {
      if (globalState.value !== parsedGs.data) {
        lastStateChangeTs.value = Date.now()
      }
      globalState.value = parsedGs.data
    }

    // Update task states (partial merge)
    if (ev.task_states && Object.keys(ev.task_states).length > 0) {
      const merged = { ...taskStates.value }
      for (const [id, state] of Object.entries(ev.task_states)) {
        const parsed = TaskStateEnum.safeParse(state)
        if (parsed.success) {
          merged[id] = parsed.data
        }
      }
      taskStates.value = merged
    }

    pushEvent(ev)
  }

  function pushEvent(ev: WSEvent): void {
    // Prepend (newest first), enforce bounded size
    const updated = [ev, ...events.value]
    if (updated.length > MAX_EVENTS) {
      updated.length = MAX_EVENTS
    }
    events.value = updated

    // Micro timeline (newest last for visual display)
    const micro = [...microEvents.value, ev]
    if (micro.length > MAX_MICRO_EVENTS) {
      micro.splice(0, micro.length - MAX_MICRO_EVENTS)
    }
    microEvents.value = micro
  }

  function setGoalBusy(busy: boolean): void {
    goalBusy.value = busy
  }

  function setGraphData(graph: TaskGraph | null): void {
    graphData.value = graph
  }

  return {
    globalState,
    debugState,
    taskStates,
    breakpoints,
    stepMode,
    interceptEnabled,
    activeTab,
    graphData,
    events,
    microEvents,
    llmRecords,
    goalBusy,
    goalState,
    lastWsMessageTs,
    lastStateChangeTs,
    llmDetailCache,
    applyStateSnapshot,
    applyWSEvent,
    pushEvent,
    setGoalBusy,
    setGraphData,
  }
}
