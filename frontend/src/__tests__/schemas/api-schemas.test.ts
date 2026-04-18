/**
 * Zod schema validation tests — L1 contract layer.
 * Verifies schemas accept valid data and reject invalid data.
 */

import { describe, it, expect } from 'vitest'
import { fc, test as fcTest } from '@fast-check/vitest'
import {
  StateSnapshotSchema,
  WSEventSchema,
  TaskGraphSchema,
  GoalStatusSchema,
  LLMHistorySchema,
  GlobalStateEnum,
  TaskStateEnum,
  DebugStateEnum,
  ALL_GLOBAL_STATES,
} from '../../types/api'
import { badgeClass } from '../../types/state-machine'

describe('Zod schema L1 contracts', () => {
  const validSnapshot = {
    global_state: 'INIT',
    debug_state: 'INACTIVE',
    task_states: {},
    step_mode: false,
    breakpoints: [],
    debug_enabled: true,
    intercept_prompts: false,
  }

  it('test_state_schema_matches_server_format', () => {
    const result = StateSnapshotSchema.safeParse(validSnapshot)
    expect(result.success).toBe(true)
  })

  it('StateSnapshotSchema rejects missing global_state', () => {
    const { global_state: _, ...noGlobal } = validSnapshot
    const result = StateSnapshotSchema.safeParse(noGlobal)
    expect(result.success).toBe(false)
  })

  it('StateSnapshotSchema rejects invalid global_state', () => {
    const result = StateSnapshotSchema.safeParse({ ...validSnapshot, global_state: 'BOGUS' })
    expect(result.success).toBe(false)
  })

  it('test_ws_event_schema_parses_all_event_types', () => {
    const events = [
      'receive_goal', 'plan_ready', 'user_confirm', 'task_dispatched',
      'task_done', 'task_failed', 'recovered', 'all_done', 'unrecoverable',
      'llm_call', 'llm_done', 'prompt_pending',
    ]
    for (const event of events) {
      const result = WSEventSchema.safeParse({
        event,
        global_state: 'INIT',
        task_states: {},
        timestamp: 1234567890.0,
        task_id: '',
        detail: '',
      })
      expect(result.success, `event=${event} should parse`).toBe(true)
    }
  })

  it('WSEventSchema rejects invalid global_state', () => {
    const result = WSEventSchema.safeParse({
      event: 'task_done',
      global_state: 'INVALID',
      task_states: {},
      timestamp: 123,
    })
    expect(result.success).toBe(false)
  })

  it('TaskGraphResponseSchema accepts valid graph', () => {
    const graph = {
      goal: 'test goal',
      tasks: [
        { id: 't1', task_type: 'primitive', action: 'click', params: { x: 100, y: 200 } },
      ],
    }
    const result = TaskGraphSchema.safeParse(graph)
    expect(result.success).toBe(true)
  })

  it('TaskGraphResponseSchema rejects missing goal', () => {
    const result = TaskGraphSchema.safeParse({ tasks: [] })
    expect(result.success).toBe(false)
  })

  it('GoalStatusSchema accepts valid status', () => {
    expect(GoalStatusSchema.safeParse({ active_goal: null, busy: false }).success).toBe(true)
    expect(GoalStatusSchema.safeParse({ active_goal: 'test', busy: true }).success).toBe(true)
  })

  it('LLMHistorySchema accepts valid history', () => {
    const history = {
      records: [
        {
          seq: 1,
          role: 'planner',
          started_at: 1234567890.0,
          duration_ms: 1500.0,
          status: 'done',
          msg_count: 2,
          response_len: 500,
          error_type: '',
        },
      ],
    }
    expect(LLMHistorySchema.safeParse(history).success).toBe(true)
  })
})

// ── L2 Property tests ──

describe('schema property tests', () => {
  const arbGlobalState = fc.constantFrom(...GlobalStateEnum.options)
  const arbTaskState = fc.constantFrom(...TaskStateEnum.options)
  const arbDebugState = fc.constantFrom(...DebugStateEnum.options)

  const arbSnapshot = fc.record({
    global_state: arbGlobalState,
    debug_state: arbDebugState,
    task_states: fc.dictionary(
      fc.string({ minLength: 1, maxLength: 10 }),
      arbTaskState,
    ),
    step_mode: fc.boolean(),
    breakpoints: fc.array(fc.string({ minLength: 1, maxLength: 10 })),
    debug_enabled: fc.boolean(),
    intercept_prompts: fc.boolean(),
  })

  fcTest.prop([arbSnapshot])(
    'prop_schema_accepts_valid_state',
    (snap) => {
      const result = StateSnapshotSchema.safeParse(snap)
      expect(result.success).toBe(true)
    },
  )

  fcTest.prop([arbSnapshot, fc.constantFrom('global_state', 'debug_state', 'task_states', 'step_mode', 'breakpoints')])(
    'prop_schema_rejects_missing_field',
    (snap, fieldToRemove) => {
      const broken = { ...snap } as Record<string, unknown>
      delete broken[fieldToRemove]
      const result = StateSnapshotSchema.safeParse(broken)
      expect(result.success).toBe(false)
    },
  )

  fcTest.prop([arbGlobalState])(
    'prop_badge_class_total_function',
    (gs) => {
      const cls = badgeClass(gs)
      expect(cls).toBeDefined()
      expect(typeof cls).toBe('string')
      expect(cls.length).toBeGreaterThan(0)
    },
  )
})
