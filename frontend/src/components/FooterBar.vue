<script setup lang="ts">
import { computed } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'

const props = defineProps<{
  store: DebugStore
  wsConnected: boolean
}>()

const footerGoal = computed(() => {
  if (props.store.goalBusy.value) return '▶ Goal running…'
  if (props.store.goalState.value === 'done') return '✓ Goal completed'
  if (props.store.goalState.value === 'failed') return '✗ Goal failed'
  return ''
})

const wsLabel = computed(() =>
  props.wsConnected ? 'WS ✓' : 'WS ✗',
)

const runtimeLabel = computed(() => {
  if (props.store.lastStateChangeTs.value === 0) return '—'
  const elapsed = Math.floor((Date.now() - props.store.lastStateChangeTs.value) / 1000)
  if (elapsed < 60) return `${elapsed}s in state`
  return `${Math.floor(elapsed / 60)}m ${elapsed % 60}s in state`
})
</script>

<template>
  <footer>
    <span>Strata Debug</span>
    <span style="font-size:10px">{{ wsLabel }}</span>
    <span style="font-size:10px">{{ runtimeLabel }}</span>
    <span id="footer-goal">{{ footerGoal }}</span>
  </footer>
</template>
