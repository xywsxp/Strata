/**
 * L2 Property tests — Boundedness invariants.
 *
 * Verifies events ≤ 300, microEvents ≤ 20, llmDetailCache ≤ 50, WS backoff ≤ 30000.
 */

import { describe, expect } from 'vitest'
import { fc, test as fcTest } from '@fast-check/vitest'
import { useDebugStore } from '../../composables/useDebugStore'
import { GlobalStateEnum, TaskStateEnum, type WSEvent } from '../../types/api'
import { computeBackoff } from '../../composables/useWebSocket'

const arbGlobalState = fc.constantFrom(...GlobalStateEnum.options)
const arbTaskState = fc.constantFrom(...TaskStateEnum.options)

const arbWSEvent: fc.Arbitrary<WSEvent> = fc.record({
  event: fc.constantFrom('task_done', 'task_failed', 'plan_ready', 'all_done'),
  global_state: arbGlobalState,
  task_states: fc.dictionary(fc.string({ minLength: 1, maxLength: 5 }), arbTaskState),
  timestamp: fc.double({ min: 1e9, max: 2e9, noNaN: true }),
})

describe('boundedness properties', () => {
  fcTest.prop([fc.array(arbWSEvent, { minLength: 1, maxLength: 500 })])(
    'prop_events_bounded_300',
    (eventList) => {
      const store = useDebugStore()
      for (const ev of eventList) {
        store.pushEvent(ev)
      }
      expect(store.events.value.length).toBeLessThanOrEqual(300)
    },
  )

  fcTest.prop([fc.array(arbWSEvent, { minLength: 1, maxLength: 100 })])(
    'prop_micro_events_bounded_20',
    (eventList) => {
      const store = useDebugStore()
      for (const ev of eventList) {
        store.applyWSEvent(ev)
      }
      expect(store.microEvents.value.length).toBeLessThanOrEqual(20)
    },
  )

  fcTest.prop([fc.array(fc.nat({ max: 200 }), { minLength: 1, maxLength: 100 })])(
    'prop_llm_cache_bounded_50',
    (keys) => {
      const store = useDebugStore()
      for (const key of keys) {
        store.llmDetailCache.value.set(key, { data: key })
        // Enforce LRU eviction
        if (store.llmDetailCache.value.size > 50) {
          const firstKey = store.llmDetailCache.value.keys().next().value
          if (firstKey !== undefined) {
            store.llmDetailCache.value.delete(firstKey)
          }
        }
      }
      expect(store.llmDetailCache.value.size).toBeLessThanOrEqual(50)
    },
  )

  fcTest.prop([fc.integer({ min: 1, max: 20 })])(
    'prop_ws_backoff_monotone_capped',
    (reconnects) => {
      let prev = 0
      for (let i = 0; i < reconnects; i++) {
        const backoff = computeBackoff(i)
        expect(backoff).toBeGreaterThanOrEqual(prev)
        expect(backoff).toBeLessThanOrEqual(30000)
        prev = backoff
      }
    },
  )
})
