<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'

const props = defineProps<{
  store: DebugStore
}>()

const graphContainer = ref<HTMLDivElement | null>(null)
let network: unknown = null
let timer: ReturnType<typeof setInterval> | null = null
const elapsed = ref(0)

const stateTimerLabel = computed(() => {
  if (elapsed.value < 60) return `${elapsed.value}s`
  return `${Math.floor(elapsed.value / 60)}m ${elapsed.value % 60}s`
})

const wsAgeLabel = computed(() => {
  if (props.store.lastWsMessageTs.value === 0) return '—'
  const age = Math.floor((Date.now() - props.store.lastWsMessageTs.value) / 1000)
  return `${age}s ago`
})

function taskColor(state: string): string {
  switch (state) {
    case 'SUCCEEDED': return '#16a34a'
    case 'FAILED': return '#dc2626'
    case 'RUNNING': return '#4f6ef7'
    case 'PENDING': return '#94a3b8'
    case 'SKIPPED': return '#9ca3af'
    default: return '#94a3b8'
  }
}

function renderGraph(): void {
  const graph = props.store.graphData.value
  if (!graph || !graphContainer.value) return

  const vis = (window as Record<string, unknown>)['vis'] as {
    DataSet: new (data: unknown[]) => unknown
    Network: new (container: HTMLElement, data: unknown, options: unknown) => unknown
  } | undefined

  if (!vis) return

  const nodes = graph.tasks.map((t) => ({
    id: t.id,
    label: `${t.id}\n${t.action ?? t.task_type}`,
    color: taskColor(props.store.taskStates.value[t.id] ?? 'PENDING'),
    shape: 'box',
    font: { size: 11, face: 'Inter' },
  }))

  const edges: Array<{ from: string; to: string; arrows: string }> = []
  for (const task of graph.tasks) {
    if (task.depends_on) {
      for (const dep of task.depends_on) {
        edges.push({ from: dep, to: task.id, arrows: 'to' })
      }
    }
  }

  const data = {
    nodes: new vis.DataSet(nodes),
    edges: new vis.DataSet(edges),
  }

  if (network) {
    (network as { destroy: () => void }).destroy()
  }

  network = new vis.Network(graphContainer.value, data, {
    layout: { hierarchical: { direction: 'LR', sortMethod: 'directed' } },
    physics: false,
    interaction: { zoomView: true, dragView: true },
    edges: { color: { color: '#94a3b8' }, smooth: { type: 'cubicBezier' } },
  })
}

function updateTimer(): void {
  if (props.store.lastStateChangeTs.value > 0) {
    elapsed.value = Math.floor((Date.now() - props.store.lastStateChangeTs.value) / 1000)
  }
}

onMounted(() => {
  timer = setInterval(updateTimer, 1000)
  renderGraph()
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (network) {
    (network as { destroy: () => void }).destroy()
    network = null
  }
})

watch(() => props.store.graphData.value, () => renderGraph(), { deep: true })
watch(() => props.store.taskStates.value, () => renderGraph(), { deep: true })

const emit = defineEmits<{
  switchTab: [tab: 'graph' | 'screenshot' | 'prompt']
}>()
</script>

<template>
  <div class="center-panel">
    <div class="graph-status">
      <div class="gs-item">
        <span class="gs-label">State</span>
        <span class="gs-value">{{ store.globalState.value }}</span>
        <span class="gs-value" style="color:var(--muted);font-weight:400">{{ stateTimerLabel }}</span>
      </div>
      <div class="gs-item">
        <span class="gs-label">WS age</span>
        <span>{{ wsAgeLabel }}</span>
      </div>
      <div class="micro-timeline">
        <span
          v-for="(ev, i) in store.microEvents.value"
          :key="`micro-${i}`"
          class="micro-dot"
          :style="{ background: taskColor(ev.global_state) }"
          :title="ev.event"
        ></span>
      </div>
    </div>
    <div ref="graphContainer" class="graph-container"></div>
  </div>
</template>
