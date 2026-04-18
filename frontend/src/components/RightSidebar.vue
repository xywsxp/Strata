<script setup lang="ts">
import { ref, computed } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'
import type { TaskNode } from '../types/api'
import { useApi } from '../composables/useApi'
import { GraphResponseSchema } from '../types/api'

const props = defineProps<{
  store: DebugStore
}>()

const { get } = useApi()
const selectedTaskId = ref<string | null>(null)
const selectedTaskDetail = ref<TaskNode | null>(null)

const taskEntries = computed(() => {
  return Object.entries(props.store.taskStates.value).sort(([a], [b]) => a.localeCompare(b))
})

function taskBadgeClass(state: string): string {
  switch (state) {
    case 'SUCCEEDED': return 'badge-ok'
    case 'FAILED': return 'badge-err'
    case 'RUNNING': return 'badge-running'
    case 'PENDING': return 'badge-idle'
    case 'SKIPPED': return 'badge-idle'
    default: return 'badge-idle'
  }
}

function selectTask(taskId: string): void {
  selectedTaskId.value = taskId
  // Find detail from graphData
  const graph = props.store.graphData.value
  if (graph) {
    const node = graph.tasks.find((t) => t.id === taskId)
    if (node) {
      selectedTaskDetail.value = node
      return
    }
  }
  selectedTaskDetail.value = null
}

function hideTaskDetail(): void {
  selectedTaskId.value = null
  selectedTaskDetail.value = null
}

async function refreshGraph(): Promise<void> {
  const data = await get('/api/graph', GraphResponseSchema)
  if (data) {
    props.store.setGraphData(data.graph)
    if (data.task_states) {
      props.store.taskStates.value = { ...props.store.taskStates.value, ...data.task_states }
    }
  }
}

function clearEvents(): void {
  props.store.events.value = []
}

function formatTimestamp(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString()
}

const emit = defineEmits<{
  showTaskEditModal: [taskId: string]
}>()
</script>

<template>
  <aside class="right" style="display:flex;flex-direction:column">
    <!-- Task states — v-for with :key for DOM stability (Bug #2 fix) -->
    <div class="section" style="flex:1;overflow-y:auto">
      <div class="section-hdr">
        <span>Running Tasks</span>
        <button class="btn btn-sm" @click="refreshGraph">↻ Graph</button>
      </div>
      <div class="task-state-list">
        <div
          v-for="[taskId, state] in taskEntries"
          :key="taskId"
          class="kv task-row"
          :class="{ selected: taskId === selectedTaskId }"
          @click="selectTask(taskId)"
        >
          <span class="k">{{ taskId }}</span>
          <span :class="['badge', taskBadgeClass(state)]">{{ state }}</span>
        </div>
        <div v-if="taskEntries.length === 0" style="color:var(--muted);font-size:11px">
          No tasks yet
        </div>
      </div>
    </div>

    <!-- Task detail panel -->
    <div
      v-if="selectedTaskId && selectedTaskDetail"
      class="section"
      style="max-height:240px;overflow-y:auto;flex-shrink:0;border-top:1px solid var(--border);padding-top:8px"
    >
      <div class="section-hdr">
        <span>Task Detail</span>
        <button class="btn btn-sm" @click="hideTaskDetail">✕</button>
      </div>
      <div style="font-size:12px">
        <div class="kv"><span class="k">ID</span><span class="v">{{ selectedTaskDetail.id }}</span></div>
        <div class="kv"><span class="k">Type</span><span class="v">{{ selectedTaskDetail.task_type }}</span></div>
        <div v-if="selectedTaskDetail.action" class="kv"><span class="k">Action</span><span class="v">{{ selectedTaskDetail.action }}</span></div>
        <div v-if="selectedTaskDetail.method" class="kv"><span class="k">Method</span><span class="v">{{ selectedTaskDetail.method }}</span></div>
        <div v-if="selectedTaskDetail.params" class="kv-block">
          <span class="k">Params</span>
          <pre>{{ JSON.stringify(selectedTaskDetail.params, null, 2) }}</pre>
        </div>
        <div v-if="selectedTaskDetail.depends_on?.length" class="kv">
          <span class="k">Depends</span>
          <span class="v">{{ selectedTaskDetail.depends_on.join(', ') }}</span>
        </div>
        <button
          v-if="store.taskStates.value[selectedTaskDetail.id] === 'PENDING'"
          class="btn btn-sm"
          style="margin-top:4px"
          @click="emit('showTaskEditModal', selectedTaskDetail.id)"
        >✏ Edit</button>
      </div>
    </div>

    <!-- Event log — v-for with :key (newest first) -->
    <div class="section" style="max-height:200px;overflow-y:auto;flex-shrink:0">
      <div class="section-hdr">
        <span>Event Log</span>
        <button class="btn btn-sm" @click="clearEvents">Clear</button>
      </div>
      <div class="event-log">
        <div
          v-for="(ev, i) in store.events.value"
          :key="`${ev.timestamp}-${i}`"
          class="event-entry"
        >
          <span class="ev-time">{{ formatTimestamp(ev.timestamp) }}</span>
          <span class="ev-name">{{ ev.event }}</span>
          <span v-if="ev.task_id" class="ev-task">{{ ev.task_id }}</span>
          <span :class="['badge', 'badge-sm', taskBadgeClass(ev.global_state)]">{{ ev.global_state }}</span>
        </div>
        <div v-if="store.events.value.length === 0" style="color:var(--muted);font-size:11px">
          No events yet
        </div>
      </div>
    </div>
  </aside>
</template>
