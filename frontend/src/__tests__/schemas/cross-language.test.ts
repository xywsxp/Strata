/**
 * Cross-language state machine consistency tests.
 * Verifies TypeScript transition tables match Python's (via transitions.json).
 */

import { describe, it, expect } from 'vitest'
import { VALID_GLOBAL_TRANSITIONS, VALID_TASK_TRANSITIONS } from '../../types/state-machine'
import transitionsJson from '../../types/transitions.json'

describe('cross-language state machine consistency', () => {
  it('prop_global_transitions_match_python', () => {
    const pythonGlobal = transitionsJson.global as Record<string, Record<string, string>>
    const tsGlobal = VALID_GLOBAL_TRANSITIONS

    // Same set of states
    const pyStates = Object.keys(pythonGlobal).sort()
    const tsStates = Object.keys(tsGlobal).sort()
    expect(tsStates).toEqual(pyStates)

    // For each state, same transitions
    for (const state of pyStates) {
      const pyTransitions = pythonGlobal[state]!
      const tsTransitions = tsGlobal[state as keyof typeof tsGlobal] ?? {}

      const pyEvents = Object.keys(pyTransitions).sort()
      const tsEvents = Object.keys(tsTransitions).sort()
      expect(tsEvents, `state=${state} events mismatch`).toEqual(pyEvents)

      for (const event of pyEvents) {
        expect(
          (tsTransitions as Record<string, string>)[event],
          `state=${state} event=${event} target mismatch`,
        ).toBe(pyTransitions[event])
      }
    }
  })

  it('prop_task_transitions_match_python', () => {
    const pythonTask = transitionsJson.task as Record<string, Record<string, string>>
    const tsTask = VALID_TASK_TRANSITIONS

    const pyStates = Object.keys(pythonTask).sort()
    const tsStates = Object.keys(tsTask).sort()
    expect(tsStates).toEqual(pyStates)

    for (const state of pyStates) {
      const pyTransitions = pythonTask[state]!
      const tsTransitions = tsTask[state as keyof typeof tsTask] ?? {}

      const pyEvents = Object.keys(pyTransitions).sort()
      const tsEvents = Object.keys(tsTransitions).sort()
      expect(tsEvents, `state=${state} events mismatch`).toEqual(pyEvents)

      for (const event of pyEvents) {
        expect(
          (tsTransitions as Record<string, string>)[event],
          `state=${state} event=${event} target mismatch`,
        ).toBe(pyTransitions[event])
      }
    }
  })

  it('prop_completed_reachable_only_via_all_done', () => {
    // Only SCHEDULING → all_done → COMPLETED reaches COMPLETED
    const pythonGlobal = transitionsJson.global as Record<string, Record<string, string>>
    const pathsToCompleted: Array<{ from: string; event: string }> = []
    for (const [state, transitions] of Object.entries(pythonGlobal)) {
      for (const [event, target] of Object.entries(transitions)) {
        if (target === 'COMPLETED') {
          pathsToCompleted.push({ from: state, event })
        }
      }
    }
    expect(pathsToCompleted).toEqual([{ from: 'SCHEDULING', event: 'all_done' }])
  })

  it('prop_no_transition_from_terminal', () => {
    expect(Object.keys(VALID_GLOBAL_TRANSITIONS.COMPLETED)).toHaveLength(0)
    expect(Object.keys(VALID_GLOBAL_TRANSITIONS.FAILED)).toHaveLength(0)
    expect(Object.keys(VALID_TASK_TRANSITIONS.SUCCEEDED)).toHaveLength(0)
    expect(Object.keys(VALID_TASK_TRANSITIONS.FAILED)).toHaveLength(0)
    expect(Object.keys(VALID_TASK_TRANSITIONS.SKIPPED)).toHaveLength(0)
  })
})
