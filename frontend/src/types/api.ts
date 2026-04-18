/**
 * API type definitions — all derived from Zod schemas via z.infer.
 * CONVENTION: No hand-written interfaces. Schema is the single source of truth.
 */

import { z } from 'zod'

// ── Enums ──

export const GlobalStateEnum = z.enum([
  'INIT', 'PLANNING', 'CONFIRMING', 'SCHEDULING',
  'EXECUTING', 'RECOVERING', 'WAITING_USER', 'COMPLETED', 'FAILED',
])
export type GlobalState = z.infer<typeof GlobalStateEnum>
export const ALL_GLOBAL_STATES = GlobalStateEnum.options

export const TaskStateEnum = z.enum([
  'PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED',
])
export type TaskState = z.infer<typeof TaskStateEnum>

export const DebugStateEnum = z.enum([
  'INACTIVE', 'OBSERVING', 'PAUSED', 'EDITING_PROMPT',
])
export type DebugState = z.infer<typeof DebugStateEnum>

export const LLMRecordStatusEnum = z.enum(['pending', 'done', 'error'])
export type LLMRecordStatus = z.infer<typeof LLMRecordStatusEnum>

export const GlobalEventEnum = z.enum([
  'receive_goal', 'plan_ready', 'user_confirm', 'user_revise',
  'task_dispatched', 'task_done', 'task_failed', 'recovered',
  'escalated', 'user_decision', 'user_abort', 'all_done', 'unrecoverable',
])
export type GlobalEvent = z.infer<typeof GlobalEventEnum>

export const TaskEventEnum = z.enum(['start', 'succeed', 'fail', 'skip'])
export type TaskEvent = z.infer<typeof TaskEventEnum>

export type GoalUiState = 'idle' | 'running' | 'done' | 'failed'

// ── Schemas ──

export const TaskNodeSchema = z.object({
  id: z.string(),
  task_type: z.string(),
  action: z.string().optional(),
  params: z.record(z.string(), z.unknown()).optional(),
  method: z.string().optional(),
  depends_on: z.array(z.string()).optional(),
  output_var: z.string().optional(),
  max_iterations: z.number().optional(),
})
export type TaskNode = z.infer<typeof TaskNodeSchema>

export const TaskGraphSchema = z.object({
  goal: z.string(),
  tasks: z.array(TaskNodeSchema),
  methods: z.record(z.string(), z.array(TaskNodeSchema)).optional(),
})
export type TaskGraph = z.infer<typeof TaskGraphSchema>

export const StateSnapshotSchema = z.object({
  global_state: GlobalStateEnum,
  debug_state: DebugStateEnum,
  task_states: z.record(z.string(), TaskStateEnum),
  step_mode: z.boolean(),
  breakpoints: z.array(z.string()),
  debug_enabled: z.boolean().optional(),
  intercept_prompts: z.boolean().optional(),
})
export type StateSnapshot = z.infer<typeof StateSnapshotSchema>

export const GraphResponseSchema = z.object({
  graph: TaskGraphSchema.nullable(),
  task_states: z.record(z.string(), TaskStateEnum),
})
export type GraphResponse = z.infer<typeof GraphResponseSchema>

export const GoalStatusSchema = z.object({
  active_goal: z.string().nullable(),
  busy: z.boolean(),
})
export type GoalStatus = z.infer<typeof GoalStatusSchema>

export const LLMRecordSummarySchema = z.object({
  seq: z.number(),
  role: z.string(),
  started_at: z.number(),
  duration_ms: z.number(),
  status: LLMRecordStatusEnum,
  msg_count: z.number(),
  response_len: z.number(),
  error_type: z.string(),
})
export type LLMRecordSummary = z.infer<typeof LLMRecordSummarySchema>

export const LLMHistorySchema = z.object({
  records: z.array(LLMRecordSummarySchema),
})
export type LLMHistory = z.infer<typeof LLMHistorySchema>

export const ChatMessageSchema = z.object({
  role: z.string(),
  content: z.string().optional(),
  has_images: z.boolean().optional(),
  images: z.array(z.string()).optional(),
})
export type ChatMessage = z.infer<typeof ChatMessageSchema>

export const LLMRecordDetailSchema = z.object({
  seq: z.number(),
  role: z.string(),
  started_at: z.number(),
  duration_ms: z.number(),
  status: LLMRecordStatusEnum,
  request_messages: z.array(ChatMessageSchema),
  response_text: z.string(),
  error_type: z.string(),
  error_msg: z.string(),
})
export type LLMRecordDetail = z.infer<typeof LLMRecordDetailSchema>

export const WSEventSchema = z.object({
  event: z.string(),
  global_state: GlobalStateEnum,
  task_states: z.record(z.string(), TaskStateEnum),
  timestamp: z.number(),
  task_id: z.string().optional(),
  detail: z.string().optional(),
})
export type WSEvent = z.infer<typeof WSEventSchema>

export const RollbackVersionsSchema = z.object({
  undo_depth: z.number(),
})
export type RollbackVersions = z.infer<typeof RollbackVersionsSchema>

export const GraphVersionSchema = z.object({
  version: z.number(),
  reason: z.string(),
  timestamp: z.number(),
  task_ids: z.array(z.string()),
})
export type GraphVersion = z.infer<typeof GraphVersionSchema>

export const GraphHistorySchema = z.object({
  versions: z.array(GraphVersionSchema),
  current_version: z.number(),
})
export type GraphHistory = z.infer<typeof GraphHistorySchema>

export const TaskFileSchema = z.object({
  file: z.string(),
  id: z.string(),
  goal: z.string(),
  tags: z.array(z.string()),
  timeout_s: z.number().optional(),
})
export type TaskFile = z.infer<typeof TaskFileSchema>

export const TaskListSchema = z.object({
  tasks: z.array(TaskFileSchema),
  task_dir: z.string(),
})
export type TaskList = z.infer<typeof TaskListSchema>
