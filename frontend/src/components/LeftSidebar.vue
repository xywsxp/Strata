<script setup lang="ts">
import { ref, watch } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'
import { useApi } from '../composables/useApi'
import { TaskListSchema, RollbackVersionsSchema } from '../types/api'
import type { TaskFile } from '../types/api'

const props = defineProps<{
  store: DebugStore
  onSubmitGoal: (goal: string) => void
  onCancelGoal: () => void
}>()

const { get, post } = useApi()
const goalText = ref('')
const taskFiles = ref<TaskFile[]>([])
const taskDir = ref('')
const bpInput = ref('')
const undoDepth = ref(0)

function escapeHtml(s: string): string {
  const div = document.createElement('div')
  div.textContent = s
  return div.innerHTML
}

async function handleSubmitGoal(): Promise<void> {
  const text = goalText.value.trim()
  if (!text) return
  props.onSubmitGoal(text)
}

function handleKillGoal(): void {
  props.onCancelGoal()
}

async function handleContinue(): Promise<void> {
  await post('/api/continue')
}

async function handleStep(): Promise<void> {
  await post('/api/step', { action: 'once' })
}

async function applyStepMode(): Promise<void> {
  await post('/api/step', { action: props.store.stepMode.value ? 'disable' : 'enable' })
  props.store.stepMode.value = !props.store.stepMode.value
}

async function loadTasks(): Promise<void> {
  const data = await get('/api/tasks', TaskListSchema)
  if (!data) return
  taskDir.value = data.task_dir ?? ''
  taskFiles.value = data.tasks
}

function selectTask(task: TaskFile): void {
  goalText.value = task.goal
}

async function addBreakpoint(): Promise<void> {
  const id = bpInput.value.trim()
  if (!id) return
  await post('/api/breakpoint/add', { task_id: id })
  bpInput.value = ''
  props.store.breakpoints.value = [...props.store.breakpoints.value, id]
}

async function removeBreakpoint(id: string): Promise<void> {
  await post('/api/breakpoint/remove', { task_id: id })
  props.store.breakpoints.value = props.store.breakpoints.value.filter((b) => b !== id)
}

async function killAndRewind(): Promise<void> {
  await post('/api/goal/cancel')
  await post('/api/rollback/undo')
  props.store.setGoalBusy(false)
}

async function undoLastTask(): Promise<void> {
  await post('/api/rollback/undo')
  const data = await get('/api/rollback/versions', RollbackVersionsSchema)
  if (data) undoDepth.value = data.undo_depth
}

const emit = defineEmits<{
  showRollbackModal: []
  showGraphRollbackModal: []
  showReplanModal: []
}>()

// Load tasks on mount
loadTasks()

watch(() => props.store.goalState.value, (gs) => {
  if (gs === 'done' || gs === 'failed') {
    // Refresh state
  }
})
</script>

<template>
  <aside class="left">
    <!-- Goal submit -->
    <div class="section">
      <div class="section-hdr">Submit Goal</div>
      <div
        class="exec-bar"
        :class="{
          idle: store.goalState.value === 'idle',
          running: store.goalState.value === 'running',
          done: store.goalState.value === 'done',
          failed: store.goalState.value === 'failed',
        }"
      >
        <span class="exec-icon">
          {{ store.goalState.value === 'running' ? '⟳' : store.goalState.value === 'done' ? '✓' : store.goalState.value === 'failed' ? '✗' : '○' }}
        </span>
        <span class="exec-text">
          {{ store.goalState.value === 'running' ? 'Running…' : store.goalState.value === 'done' ? 'Completed' : store.goalState.value === 'failed' ? 'Failed' : 'Idle — no active goal' }}
        </span>
      </div>
      <div class="goal-form">
        <textarea
          v-model="goalText"
          placeholder="Type goal here…"
          rows="3"
        ></textarea>
        <div class="row">
          <button class="btn btn-primary" :disabled="store.goalBusy.value" @click="handleSubmitGoal">▶ Run Goal</button>
          <button v-if="store.goalBusy.value" class="btn btn-danger btn-sm" @click="handleKillGoal">Kill</button>
          <button class="btn" @click="handleContinue">Continue</button>
          <button class="btn" @click="handleStep">Step</button>
        </div>
        <label style="font-size:11px;color:var(--muted);display:flex;align-items:center;gap:4px;margin-top:4px">
          <input type="checkbox" :checked="store.stepMode.value" @change="applyStepMode">
          Pause before each task
        </label>
        <button
          v-if="store.goalBusy.value"
          class="btn btn-sm"
          style="margin-top:4px"
          @click="emit('showReplanModal')"
        >✏ Edit Goal &amp; Replan</button>
      </div>
    </div>

    <!-- Task files -->
    <div class="section">
      <div class="section-hdr">
        <span>Task Files</span>
        <button class="btn btn-sm" @click="loadTasks">↻</button>
      </div>
      <div v-if="taskDir" style="font-size:10px;color:var(--muted);margin-bottom:6px">📁 {{ taskDir }}</div>
      <div class="task-cards">
        <div
          v-for="task in taskFiles"
          :key="task.id"
          class="task-card"
          @click="selectTask(task)"
        >
          <div class="tc-id">{{ task.id }}</div>
          <div class="tc-goal">{{ task.goal.slice(0, 80).replace(/\n/g, ' ') }}</div>
          <div class="tc-tags">
            <span v-for="tag in task.tags" :key="tag" class="tag">{{ tag }}</span>
          </div>
        </div>
        <div v-if="!taskFiles.length" style="color:var(--muted);font-size:11px">No task files found</div>
      </div>
    </div>

    <!-- Breakpoints -->
    <div class="section">
      <div class="section-hdr">Breakpoints</div>
      <div style="display:flex;gap:5px;margin-bottom:6px">
        <input v-model="bpInput" type="text" placeholder="task_id">
        <button class="btn btn-sm" @click="addBreakpoint">Add</button>
      </div>
      <div class="bp-list">
        <div v-for="bp in store.breakpoints.value" :key="bp" class="bp-item">
          <span>{{ bp }}</span>
          <button class="btn btn-sm btn-danger" @click="removeBreakpoint(bp)">×</button>
        </div>
      </div>
    </div>

    <!-- Rollback -->
    <div class="section">
      <div class="section-hdr">Rollback</div>
      <div class="rollback-warn">Rewind only affects saved state. A running goal must be killed first.</div>
      <div class="btn-group" style="margin-bottom:6px">
        <button class="btn btn-sm btn-danger" @click="killAndRewind">Kill + Rewind</button>
        <button class="btn btn-sm btn-danger" @click="emit('showRollbackModal')">Checkpoint…</button>
      </div>
      <div class="btn-group">
        <button class="btn btn-sm" @click="undoLastTask">Undo Task <span v-if="undoDepth">({{ undoDepth }})</span></button>
        <button class="btn btn-sm" @click="emit('showGraphRollbackModal')">Graph…</button>
      </div>
    </div>
  </aside>
</template>
