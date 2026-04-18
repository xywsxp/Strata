/**
 * HeaderBar + FooterBar component tests — L3 regression.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref, computed } from 'vue'
import HeaderBar from '../../components/HeaderBar.vue'
import type { DebugStore } from '../../composables/useDebugStore'
import type { GlobalState, DebugState, GoalUiState } from '../../types/api'

function makeStore(overrides: Partial<{
  globalState: GlobalState
  debugState: DebugState
  interceptEnabled: boolean
  goalBusy: boolean
  goalState: GoalUiState
}>): DebugStore {
  const gs = ref<GlobalState>(overrides.globalState ?? 'INIT')
  const ds = ref<DebugState>(overrides.debugState ?? 'INACTIVE')
  const ie = ref(overrides.interceptEnabled ?? false)
  const gb = ref(overrides.goalBusy ?? false)
  return {
    globalState: gs,
    debugState: ds,
    taskStates: ref({}),
    breakpoints: ref([]),
    stepMode: ref(false),
    interceptEnabled: ie,
    activeTab: ref('graph'),
    graphData: ref(null),
    events: ref([]),
    microEvents: ref([]),
    llmRecords: ref([]),
    goalBusy: gb,
    goalState: computed(() => overrides.goalState ?? 'idle'),
    lastWsMessageTs: ref(0),
    lastStateChangeTs: ref(0),
    llmDetailCache: ref(new Map()),
    applyStateSnapshot: vi.fn(),
    applyWSEvent: vi.fn(),
    pushEvent: vi.fn(),
    setGoalBusy: vi.fn(),
    setGraphData: vi.fn(),
  } as DebugStore
}

beforeEach(() => {
  Object.defineProperty(globalThis, 'window', {
    value: {
      location: {
        search: '?token=test',
        protocol: 'http:',
        host: 'localhost:8080',
      },
    },
    writable: true,
    configurable: true,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('HeaderBar', () => {
  it('test_header_badge_class_mapping_failed', () => {
    const store = makeStore({ globalState: 'FAILED' })
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: false },
    })
    const badges = wrapper.findAll('.badge')
    const stateBadge = badges[0]!
    expect(stateBadge.classes()).toContain('badge-err')
    expect(stateBadge.text()).toBe('FAILED')
  })

  it('test_header_completed_badge_ok', () => {
    const store = makeStore({ globalState: 'COMPLETED' })
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: true },
    })
    const badges = wrapper.findAll('.badge')
    const stateBadge = badges[0]!
    expect(stateBadge.classes()).toContain('badge-ok')
  })

  it('test_header_ws_badge_connected', () => {
    const store = makeStore({})
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: true },
    })
    const wsBadge = wrapper.findAll('.badge').pop()!
    expect(wsBadge.classes()).toContain('badge-ok')
    expect(wsBadge.text()).toContain('WS ✓')
  })

  it('test_header_ws_badge_disconnected', () => {
    const store = makeStore({})
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: false },
    })
    const wsBadge = wrapper.findAll('.badge').pop()!
    expect(wsBadge.classes()).toContain('badge-err')
  })

  it('test_header_intercept_toggle_calls_api', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const store = makeStore({ interceptEnabled: false })
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: true },
    })

    // Click intercept badge (3rd badge: state, debug, intercept)
    const interceptBadge = wrapper.findAll('.badge')[2]!
    interceptBadge.element.click()
    await wrapper.vm.$nextTick()

    expect(mockFetch).toHaveBeenCalledOnce()
    const [url] = mockFetch.mock.calls[0]!
    expect(url).toContain('/api/prompt/enable')
  })

  it('test_header_intercept_skip_when_enabled', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const store = makeStore({ interceptEnabled: true })
    const wrapper = mount(HeaderBar, {
      props: { store, wsConnected: true },
    })

    const interceptBadge = wrapper.findAll('.badge')[2]!
    interceptBadge.element.click()
    await wrapper.vm.$nextTick()

    const [url] = mockFetch.mock.calls[0]!
    expect(url).toContain('/api/prompt/skip')
  })
})
