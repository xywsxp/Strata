<script setup lang="ts">
import { computed } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'
import { badgeClass } from '../types/state-machine'
import { useApi } from '../composables/useApi'

const props = defineProps<{
  store: DebugStore
  wsConnected: boolean
}>()

const { post } = useApi()

const stateBadgeClass = computed(() => badgeClass(props.store.globalState.value))
const debugBadgeClass = computed(() => {
  switch (props.store.debugState.value) {
    case 'PAUSED': return 'badge-warn'
    case 'EDITING_PROMPT': return 'badge-blue'
    case 'OBSERVING': return 'badge-ok'
    default: return 'badge-idle'
  }
})

const interceptLabel = computed(() =>
  props.store.interceptEnabled.value ? 'Intercept ON' : 'Intercept OFF',
)
const interceptBadgeClass = computed(() =>
  props.store.interceptEnabled.value ? 'badge-warn' : 'badge-idle',
)

async function toggleIntercept(): Promise<void> {
  if (props.store.interceptEnabled.value) {
    await post('/api/prompt/skip')
  } else {
    await post('/api/prompt/enable')
  }
  props.store.interceptEnabled.value = !props.store.interceptEnabled.value
}
</script>

<template>
  <header>
    <div class="logo">STRATA <span>Debug</span></div>
    <span :class="['badge', stateBadgeClass]">{{ store.globalState.value }}</span>
    <span :class="['badge', debugBadgeClass]">{{ store.debugState.value }}</span>
    <span
      :class="['badge', interceptBadgeClass]"
      style="cursor:pointer"
      @click="toggleIntercept"
    >{{ interceptLabel }}</span>
    <span class="spacer"></span>
    <span :class="['badge', wsConnected ? 'badge-ok' : 'badge-err']">
      {{ wsConnected ? 'WS ✓' : 'WS off' }}
    </span>
  </header>
</template>
