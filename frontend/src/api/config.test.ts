import { describe, expect, it } from 'vitest'
import { resolveApiBaseUrl } from './config'

describe('API base configuration', () => {
  it('uses a local development default and same-origin production default', () => {
    expect(resolveApiBaseUrl(undefined, true)).toBe('http://localhost:8000')
    expect(resolveApiBaseUrl(undefined, false)).toBe('')
  })

  it('normalizes safe URLs and rejects malformed or credential-bearing values', () => {
    expect(resolveApiBaseUrl('https://api.example.test/')).toBe(
      'https://api.example.test',
    )
    expect(() => resolveApiBaseUrl('not a url')).toThrow('absolute HTTP')
    expect(() => resolveApiBaseUrl('ftp://api.example.test')).toThrow(
      'without credentials',
    )
    expect(() => resolveApiBaseUrl('https://key@api.example.test')).toThrow(
      'without credentials',
    )
  })
})
