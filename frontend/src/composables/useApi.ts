/**
 * API composable — unified HTTP client with Bearer token injection and Zod validation.
 *
 * CONVENTION: All API responses are validated through Zod schemas.
 * Parse failures → console.warn + return null (never throw).
 */

import type { ZodSchema } from 'zod'

function extractToken(): string {
  if (typeof window === 'undefined') return ''
  const params = new URLSearchParams(window.location.search)
  return params.get('token') ?? ''
}

let _cachedToken: string | null = null

function getToken(): string {
  if (_cachedToken === null) {
    _cachedToken = extractToken()
  }
  return _cachedToken
}

/** Reset token cache — for testing only. */
export function _resetTokenCache(): void {
  _cachedToken = null
}

function baseUrl(): string {
  if (typeof window === 'undefined') return ''
  return `${window.location.protocol}//${window.location.host}`
}

export function useApi(): {
  get: <T>(path: string, schema: ZodSchema<T>) => Promise<T | null>
  post: <T>(path: string, body?: unknown, schema?: ZodSchema<T>) => Promise<T | null>
} {
  async function get<T>(path: string, schema: ZodSchema<T>): Promise<T | null> {
    try {
      const resp = await fetch(`${baseUrl()}${path}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      if (!resp.ok) {
        console.warn(`[useApi] GET ${path} → ${resp.status}`)
        return null
      }
      const json: unknown = await resp.json()
      const parsed = schema.safeParse(json)
      if (!parsed.success) {
        console.warn(`[useApi] GET ${path} schema validation failed:`, parsed.error.message)
        return null
      }
      return parsed.data
    } catch (err) {
      console.warn(`[useApi] GET ${path} error:`, err)
      return null
    }
  }

  async function post<T>(path: string, body?: unknown, schema?: ZodSchema<T>): Promise<T | null> {
    try {
      const resp = await fetch(`${baseUrl()}${path}`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${getToken()}`,
          'Content-Type': 'application/json',
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
      if (!resp.ok) {
        console.warn(`[useApi] POST ${path} → ${resp.status}`)
        return null
      }
      const json: unknown = await resp.json()
      if (schema) {
        const parsed = schema.safeParse(json)
        if (!parsed.success) {
          console.warn(`[useApi] POST ${path} schema validation failed:`, parsed.error.message)
          return null
        }
        return parsed.data
      }
      return json as T
    } catch (err) {
      console.warn(`[useApi] POST ${path} error:`, err)
      return null
    }
  }

  return { get, post }
}
