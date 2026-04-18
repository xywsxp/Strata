<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import type { DebugStore } from '../composables/useDebugStore'
import { useApi } from '../composables/useApi'
import {
  LLMHistorySchema,
  LLMRecordDetailSchema,
  type LLMRecordSummary,
  type LLMRecordDetail,
} from '../types/api'

const props = defineProps<{
  store: DebugStore
  active: boolean
}>()

const { get, post } = useApi()
const expandedSeqs = ref<Set<number>>(new Set())
const detailCache = ref<Map<number, LLMRecordDetail>>(new Map())
let timer: ReturnType<typeof setInterval> | null = null

const MAX_CACHE = 50

function statusColor(status: string): string {
  switch (status) {
    case 'done': return 'var(--green)'
    case 'error': return 'var(--red)'
    case 'pending': return 'var(--yellow)'
    default: return 'var(--muted)'
  }
}

async function refreshLLMLog(): Promise<void> {
  const data = await get('/api/llm/history', LLMHistorySchema)
  if (data) {
    props.store.llmRecords.value = data.records
  }
}

async function toggleRow(record: LLMRecordSummary): Promise<void> {
  if (expandedSeqs.value.has(record.seq)) {
    expandedSeqs.value = new Set([...expandedSeqs.value].filter((s) => s !== record.seq))
    return
  }

  // Load detail if not cached
  if (!detailCache.value.has(record.seq)) {
    const detail = await get(`/api/llm/record/${record.seq}`, LLMRecordDetailSchema)
    if (detail) {
      detailCache.value.set(record.seq, detail)
      // Enforce cache bound
      if (detailCache.value.size > MAX_CACHE) {
        const firstKey = detailCache.value.keys().next().value
        if (firstKey !== undefined) {
          detailCache.value.delete(firstKey)
        }
      }
    }
  }

  expandedSeqs.value = new Set([...expandedSeqs.value, record.seq])
}

function getDetail(seq: number): LLMRecordDetail | undefined {
  return detailCache.value.get(seq)
}

function escapeHtml(s: string): string {
  const div = document.createElement('div')
  div.textContent = s
  return div.innerHTML
}

// Intercept banner
async function approveFromPanel(): Promise<void> {
  await post('/api/prompt/approve')
}

async function skipFromPanel(): Promise<void> {
  await post('/api/prompt/skip')
}

const emit = defineEmits<{
  showPromptModal: []
}>()

function startRefreshTimer(): void {
  if (timer) return
  timer = setInterval(() => {
    if (props.active) refreshLLMLog()
  }, 5000)
}

function stopRefreshTimer(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  if (props.active) {
    refreshLLMLog()
    startRefreshTimer()
  }
})

onBeforeUnmount(() => {
  stopRefreshTimer()
})

watch(() => props.active, (active) => {
  if (active) {
    refreshLLMLog()
    startRefreshTimer()
  } else {
    stopRefreshTimer()
  }
})
</script>

<template>
  <div class="center-panel">
    <!-- Intercept banner -->
    <div
      v-if="store.debugState.value === 'EDITING_PROMPT'"
      class="intercept-banner"
    >
      <div class="intercept-banner-bar">
        <span>⏸ LLM call paused — awaiting approval</span>
        <button class="btn btn-primary btn-sm" @click="approveFromPanel">✓ Approve</button>
        <button class="btn btn-sm" @click="emit('showPromptModal')">✎ Edit</button>
        <span style="flex:1"></span>
        <button class="btn btn-sm btn-danger" @click="skipFromPanel">Skip All</button>
      </div>
    </div>

    <!-- LLM history log -->
    <div class="llm-log-hdr">
      <span>LLM Call History</span>
      <button class="btn btn-sm" @click="refreshLLMLog">↻ Refresh</button>
    </div>
    <div class="llm-log-body">
      <template v-if="store.llmRecords.value.length === 0">
        <div style="color:var(--muted);font-size:12px;padding:16px">No LLM calls recorded yet.</div>
      </template>
      <template v-else>
        <div
          v-for="record in store.llmRecords.value"
          :key="record.seq"
          class="llm-row"
        >
          <div class="llm-row-header" @click="toggleRow(record)">
            <span :style="{ color: statusColor(record.status) }">[{{ record.status }}]</span>
            <span class="llm-seq">#{{ record.seq }}</span>
            <span class="llm-role">{{ record.role }}</span>
            <span class="llm-duration">{{ record.duration_ms.toFixed(0) }}ms</span>
            <span class="llm-msgs">{{ record.msg_count }} msgs</span>
            <span v-if="record.error_type" class="llm-error">{{ record.error_type }}</span>
          </div>
          <div v-show="expandedSeqs.has(record.seq)" class="llm-row-detail">
            <template v-if="getDetail(record.seq)">
              <div
                v-for="(msg, mi) in getDetail(record.seq)!.request_messages"
                :key="`msg-${record.seq}-${mi}`"
                class="llm-msg"
              >
                <div class="llm-msg-role">{{ msg.role }}</div>
                <div class="llm-msg-content">{{ msg.content ?? '' }}</div>
                <div v-if="msg.images?.length" class="llm-images">
                  <template v-for="(img, ii) in msg.images" :key="`img-${record.seq}-${mi}-${ii}`">
                    <img
                      v-if="img !== 'image_too_large'"
                      :src="'data:image/png;base64,' + img"
                      class="llm-img"
                    >
                    <span v-else class="llm-img-placeholder">Image too large</span>
                  </template>
                </div>
              </div>
              <div v-if="getDetail(record.seq)!.response_text" class="llm-response">
                <div class="llm-msg-role">response</div>
                <pre class="llm-response-text">{{ getDetail(record.seq)!.response_text }}</pre>
              </div>
              <div v-if="getDetail(record.seq)!.error_msg" class="llm-error-msg">
                {{ getDetail(record.seq)!.error_msg }}
              </div>
            </template>
            <div v-else style="color:var(--muted);font-size:11px;padding:8px">Loading…</div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
