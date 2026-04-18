/**
 * L2 Property tests — State consistency.
 *
 * Verifies idempotency, partial update independence, and enum closure.
 */

import { describe, expect } from 'vitest'
import { fc, test as fcTest } from '@fast-check/vitest'
import { useDebugStore } from '../../composables/useDebugStore'
import {
  GlobalStateEnum,
  TaskStateEnum,
  DebugStateEnum,
  type StateSnapshot,
  type WSEvent,
} from '../../types/api'

const arbGlobalState = fc.constantFrom(...GlobalStateEnum.options)
const arbTaskState = fc.constantFrom(...TaskStateEnum.options)
const arbDebugState = fc.constantFrom(...DebugStateEnum.options)

const arbSnapshot: fc.Arbitrary<StateSnapshot> = fc.record({
  global_state: arbGlobalState,
  debug_state: arbDebugState,
  task_states: fc.dictionary(fc.string({ minLength: 1, maxLength: 8 }), arbTaskState),
  step_mode: fc.boolean(),
  breakpoints: fc.array(fc.string({ minLength: 1, maxLength: 8 })),
  debug_enabled: fc.boolean(),
  intercept_prompts: fc.boolean(),
})

describe('state consistency properties', () => {
  fcTest.prop([arbSnapshot])(
    'prop_snapshot_idempotent',
    (snap) => {
      const store = useDebugStore()
      store.applyStateSnapshot(snap)

      const stateAfterFirst = {
        gs: store.globalState.value,
        ds: store.debugState.value,
        ts: { ...store.taskStates.value },
        sm: store.stepMode.value,
        bp: [...store.breakpoints.value],
      }

      store.applyStateSnapshot(snap)

      expect(store.globalState.value).toBe(stateAfterFirst.gs)
      expect(store.debugState.value).toBe(stateAfterFirst.ds)
      expect(store.taskStates.value).toEqual(stateAfterFirst.ts)
      expect(store.stepMode.value).toBe(stateAfterFirst.sm)
      expect(store.breakpoints.value).toEqual(stateAfterFirst.bp)
    },
  )

  fcTest.prop([
    arbSnapshot,
    fc.dictionary(fc.string({ minLength: 1, maxLength: 8 }), arbTaskState),
  ])(
    'prop_ws_partial_update_preserves_untouched',
    (snap, newTaskStates) => {
      const store = useDebugStore()
      store.applyStateSnapshot(snap)

      const gsBefore = store.globalState.value
      const dsBefore = store.debugState.value
      const smBefore = store.stepMode.value

      // Apply a WS event that only has task_states (global_state stays same)
      const ev: WSEvent = {
        event: 'task_done',
        global_state: gsBefore,
        task_states: newTaskStates,
        timestamp: Date.now() / 1000,
      }
      store.applyWSEvent(ev)

      // Global state and debug state should not change
      expect(store.globalState.value).toBe(gsBefore)
      expect(store.debugState.value).toBe(dsBefore)
      expect(store.stepMode.value).toBe(smBefore)
    },
  )

  fcTest.prop([arbSnapshot])(
    'prop_state_enum_closed',
    (snap) => {
      const store = useDebugStore()
      store.applyStateSnapshot(snap)
      expect(GlobalStateEnum.safeParse(store.globalState.value).success).toBe(true)
    },
  )

  fcTest.prop([
    arbSnapshot,
  ])(
    'prop_task_state_enum_closed',
    (snap) => {
      const store = useDebugStore()
      store.applyStateSnapshot(snap)
      for (const state of Object.values(store.taskStates.value)) {
        expect(TaskStateEnum.safeParse(state).success).toBe(true)
      }
    },
  )
})
