/**
 * useApi integration tests — L3 regression.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { z } from 'zod'
import { useApi, _resetTokenCache } from '../../composables/useApi'

// Mock window.location for token extraction
const originalWindow = globalThis.window

beforeEach(() => {
  _resetTokenCache()
  Object.defineProperty(globalThis, 'window', {
    value: {
      location: {
        search: '?token=test-secret-123',
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
  Object.defineProperty(globalThis, 'window', {
    value: originalWindow,
    writable: true,
    configurable: true,
  })
})

const TestSchema = z.object({ value: z.number() })

describe('useApi', () => {
  it('test_api_injects_bearer_token', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ value: 42 }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const { get } = useApi()
    await get('/api/test', TestSchema)

    expect(mockFetch).toHaveBeenCalledOnce()
    const [, opts] = mockFetch.mock.calls[0]!
    expect(opts.headers.Authorization).toBe('Bearer test-secret-123')
  })

  it('test_api_returns_null_on_error', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    })
    vi.stubGlobal('fetch', mockFetch)

    const { get } = useApi()
    const result = await get('/api/fail', TestSchema)

    expect(result).toBeNull()
  })

  it('test_api_returns_null_on_schema_failure', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ wrong_field: 'oops' }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const { get } = useApi()
    const result = await get('/api/bad-shape', TestSchema)

    expect(result).toBeNull()
  })

  it('test_api_returns_null_on_network_error', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new Error('network down'))
    vi.stubGlobal('fetch', mockFetch)

    const { get } = useApi()
    const result = await get('/api/offline', TestSchema)

    expect(result).toBeNull()
  })

  it('test_api_post_sends_body', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ value: 99 }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const { post } = useApi()
    await post('/api/submit', { goal: 'test' }, TestSchema)

    const [, opts] = mockFetch.mock.calls[0]!
    expect(opts.method).toBe('POST')
    expect(opts.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(opts.body)).toEqual({ goal: 'test' })
  })
})
