/**
 * WebSocket composable — connection management with exponential backoff.
 *
 * CONVENTION: Only one WS connection at a time.
 * On message: Zod parse → store.applyWSEvent().
 * On open: full sync via GET /api/state.
 */

import { ref, type Ref } from 'vue'
import { WSEventSchema, StateSnapshotSchema } from '../types/api'
import type { DebugStore } from './useDebugStore'
import { useApi } from './useApi'

const BACKOFF_INITIAL = 2000
const BACKOFF_MAX = 30000

/**
 * Compute reconnection backoff for the nth attempt.
 * Exported for property testing.
 */
export function computeBackoff(attempt: number): number {
  return Math.min(BACKOFF_INITIAL * Math.pow(2, attempt), BACKOFF_MAX)
}

export function useWebSocket(store: DebugStore): {
  connected: Ref<boolean>
  connect: () => void
  disconnect: () => void
} {
  const connected = ref(false)
  let ws: WebSocket | null = null
  let reconnectAttempt = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  const { get } = useApi()

  function getWsUrl(): string {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const params = new URLSearchParams(window.location.search)
    const token = params.get('token') ?? ''
    return `${proto}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
  }

  async function fullSync(): Promise<void> {
    const snap = await get('/api/state', StateSnapshotSchema)
    if (snap) {
      store.applyStateSnapshot(snap)
    }
  }

  function connect(): void {
    if (ws && ws.readyState === WebSocket.OPEN) return

    try {
      ws = new WebSocket(getWsUrl())
    } catch (err) {
      console.warn('[useWebSocket] Failed to create WebSocket:', err)
      scheduleReconnect()
      return
    }

    ws.onopen = () => {
      connected.value = true
      reconnectAttempt = 0
      fullSync()
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(String(event.data))
        const parsed = WSEventSchema.safeParse(data)
        if (parsed.success) {
          store.applyWSEvent(parsed.data)
        } else {
          console.warn('[useWebSocket] Invalid WS message:', parsed.error.message)
        }
      } catch (err) {
        console.warn('[useWebSocket] Failed to parse WS message:', err)
      }
    }

    ws.onclose = () => {
      connected.value = false
      ws = null
      scheduleReconnect()
    }

    ws.onerror = () => {
      // onclose will fire after onerror
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer !== null) return
    const delay = computeBackoff(reconnectAttempt)
    reconnectAttempt++
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function disconnect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    connected.value = false
  }

  return { connected, connect, disconnect }
}
