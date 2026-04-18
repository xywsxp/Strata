<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'

const props = defineProps<{
  store: DebugStore
  active: boolean
}>()

const screenshotSrc = ref('')
const screenshotTs = ref('—')
const autoRefresh = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

function getHeaders(): Record<string, string> {
  const params = new URLSearchParams(window.location.search)
  return { Authorization: `Bearer ${params.get('token') ?? ''}` }
}

async function captureScreenshot(): Promise<void> {
  try {
    const resp = await fetch(`${window.location.protocol}//${window.location.host}/api/screenshot`, {
      headers: getHeaders(),
    })
    if (!resp.ok) return
    const blob = await resp.blob()
    screenshotSrc.value = URL.createObjectURL(blob)
    screenshotTs.value = new Date().toLocaleTimeString()
  } catch {
    // silent
  }
}

function startAutoRefresh(): void {
  if (timer) return
  timer = setInterval(() => {
    if (autoRefresh.value && props.active) {
      captureScreenshot()
    }
  }, 3000)
}

function stopAutoRefresh(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  if (props.active) {
    captureScreenshot()
    startAutoRefresh()
  }
})

onBeforeUnmount(() => {
  stopAutoRefresh()
})

watch(() => props.active, (active) => {
  if (active) {
    captureScreenshot()
    startAutoRefresh()
  } else {
    stopAutoRefresh()
  }
})
</script>

<template>
  <div class="center-panel">
    <div class="screenshot-controls">
      <button class="btn btn-sm" @click="captureScreenshot">📷 Capture</button>
      <label>
        <input v-model="autoRefresh" type="checkbox">
        Auto (3s)
      </label>
      <span style="font-size:10px;color:var(--muted)">{{ screenshotTs }}</span>
    </div>
    <img
      v-if="screenshotSrc"
      :src="screenshotSrc"
      alt="VM screenshot"
      class="screenshot-img"
    >
    <div v-else style="color:var(--muted);font-size:12px;padding:16px">No screenshot captured yet.</div>
  </div>
</template>
