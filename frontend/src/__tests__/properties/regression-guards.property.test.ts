/**
 * L2 Property tests — Bug regression guards.
 *
 * These properties prevent re-introduction of the "COMPLETED but actually failed" bug.
 */

import { describe, expect } from 'vitest'
import { fc, test as fcTest } from '@fast-check/vitest'
import { GlobalStateEnum, ALL_GLOBAL_STATES } from '../../types/api'
import { computeGoalState } from '../../types/state-machine'

const arbGlobalState = fc.constantFrom(...GlobalStateEnum.options)
const arbBusy = fc.boolean()

describe('regression guard properties', () => {
  fcTest.prop([arbBusy, arbGlobalState])(
    'prop_goal_state_never_done_unless_completed',
    (busy, gs) => {
      const result = computeGoalState(busy, gs)
      if (result === 'done') {
        expect(gs).toBe('COMPLETED')
      }
    },
  )

  fcTest.prop([arbBusy, arbGlobalState])(
    'prop_goal_state_failed_iff_global_failed',
    (busy, gs) => {
      const result = computeGoalState(busy, gs)
      if (result === 'failed') {
        expect(gs).toBe('FAILED')
        expect(busy).toBe(false)
      }
      if (gs === 'FAILED' && !busy) {
        expect(result).toBe('failed')
      }
    },
  )

  fcTest.prop([arbBusy, arbGlobalState])(
    'prop_goal_state_decision_exhaustive',
    (busy, gs) => {
      const result = computeGoalState(busy, gs)
      expect(['idle', 'running', 'done', 'failed']).toContain(result)
    },
  )

  // Exhaustive 18-combination decision table (not property-based, but comprehensive)
  describe('goal_state_decision_table', () => {
    const expectedTable: Array<{ busy: boolean; gs: typeof ALL_GLOBAL_STATES[number]; expected: string }> = [
      // busy=true → always 'running'
      { busy: true, gs: 'INIT', expected: 'running' },
      { busy: true, gs: 'PLANNING', expected: 'running' },
      { busy: true, gs: 'CONFIRMING', expected: 'running' },
      { busy: true, gs: 'SCHEDULING', expected: 'running' },
      { busy: true, gs: 'EXECUTING', expected: 'running' },
      { busy: true, gs: 'RECOVERING', expected: 'running' },
      { busy: true, gs: 'WAITING_USER', expected: 'running' },
      { busy: true, gs: 'COMPLETED', expected: 'running' },
      { busy: true, gs: 'FAILED', expected: 'running' },
      // busy=false
      { busy: false, gs: 'INIT', expected: 'idle' },
      { busy: false, gs: 'PLANNING', expected: 'idle' },
      { busy: false, gs: 'CONFIRMING', expected: 'idle' },
      { busy: false, gs: 'SCHEDULING', expected: 'idle' },
      { busy: false, gs: 'EXECUTING', expected: 'idle' },
      { busy: false, gs: 'RECOVERING', expected: 'idle' },
      { busy: false, gs: 'WAITING_USER', expected: 'idle' },
      { busy: false, gs: 'COMPLETED', expected: 'done' },
      { busy: false, gs: 'FAILED', expected: 'failed' },
    ]

    for (const { busy, gs, expected } of expectedTable) {
      fcTest.prop([fc.constant(null)])(
        `computeGoalState(busy=${busy}, gs=${gs}) === ${expected}`,
        () => {
          expect(computeGoalState(busy, gs)).toBe(expected)
        },
      )
    }
  })
})
