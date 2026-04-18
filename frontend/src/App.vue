<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useDebugStore } from './composables/useDebugStore'
import { useWebSocket } from './composables/useWebSocket'
import { useGoalRunner } from './composables/useGoalRunner'
import { useApi } from './composables/useApi'
import { StateSnapshotSchema, GraphResponseSchema } from './types/api'
import HeaderBar from './components/HeaderBar.vue'
import FooterBar from './components/FooterBar.vue'
import LeftSidebar from './components/LeftSidebar.vue'
import RightSidebar from './components/RightSidebar.vue'
import GraphPanel from './components/GraphPanel.vue'
import ScreenPanel from './components/ScreenPanel.vue'
import LLMPanel from './components/LLMPanel.vue'

const store = useDebugStore()
const { connected, connect, disconnect } = useWebSocket(store)
const { submitGoal, cancelGoal } = useGoalRunner(store, connected)
const { get, post } = useApi()

let refreshTimer: ReturnType<typeof setInterval> | null = null

function switchTab(tab: 'graph' | 'screenshot' | 'prompt'): void {
  store.activeTab.value = tab
}

async function initialSync(): Promise<void> {
  const snap = await get('/api/state', StateSnapshotSchema)
  if (snap) store.applyStateSnapshot(snap)

  const graph = await get('/api/graph', GraphResponseSchema)
  if (graph) {
    store.setGraphData(graph.graph)
    if (graph.task_states) {
      store.taskStates.value = { ...store.taskStates.value, ...graph.task_states }
    }
  }
}

onMounted(() => {
  initialSync()
  connect()
  // Fallback polling every 10s in case WS is down
  refreshTimer = setInterval(async () => {
    if (!connected.value) {
      const snap = await get('/api/state', StateSnapshotSchema)
      if (snap) store.applyStateSnapshot(snap)
    }
  }, 10000)
})

onBeforeUnmount(() => {
  disconnect()
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<template>
  <HeaderBar :store="store" :ws-connected="connected" />

  <LeftSidebar
    :store="store"
    :on-submit-goal="submitGoal"
    :on-cancel-goal="cancelGoal"
  />

  <main>
    <div class="center-tabs">
      <div
        :class="['center-tab', { active: store.activeTab.value === 'graph' }]"
        @click="switchTab('graph')"
      >Graph</div>
      <div
        :class="['center-tab', { active: store.activeTab.value === 'screenshot' }]"
        @click="switchTab('screenshot')"
      >Screen</div>
      <div
        :class="['center-tab', { active: store.activeTab.value === 'prompt' }]"
        @click="switchTab('prompt')"
      >⚡ LLM Calls</div>
    </div>

    <!-- Paused banner -->
    <div
      v-if="store.debugState.value === 'PAUSED'"
      class="paused-bar"
    >
      PAUSED — waiting for Continue or Step
      <button class="btn btn-sm btn-primary" @click="post('/api/continue')">Continue</button>
      <button class="btn btn-sm" @click="post('/api/step', { action: 'once' })">Step</button>
    </div>

    <GraphPanel
      v-show="store.activeTab.value === 'graph'"
      :store="store"
    />
    <ScreenPanel
      v-show="store.activeTab.value === 'screenshot'"
      :store="store"
      :active="store.activeTab.value === 'screenshot'"
    />
    <LLMPanel
      v-show="store.activeTab.value === 'prompt'"
      :store="store"
      :active="store.activeTab.value === 'prompt'"
    />
  </main>

  <RightSidebar :store="store" />
  <FooterBar :store="store" :ws-connected="connected" />
</template>

<style>
@import './style.css';
</style>
