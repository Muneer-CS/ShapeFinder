import { describe, expect, it, vi } from 'vitest'
import { MarketDataApiError } from './marketData'
import {
  searchSimilarity,
  type SimilaritySearchRequest,
} from './similaritySearch'

const request: SimilaritySearchRequest = {
  reference: {
    symbol: 'NVDA',
    start: '2025-01-01T00:00:00Z',
    end: '2025-01-31T23:59:59Z',
    interval: '1day',
  },
  search: {
    start: '2020-01-01T00:00:00Z',
    end: '2024-12-31T23:59:59Z',
    symbols: ['AMD'],
  },
  top_n: 5,
  minimum_similarity: 80,
}

const success = {
  reference: {
    symbol: 'NVDA',
    start: request.reference.start,
    end: request.reference.end,
    interval: '1day',
    bar_count: 20,
  },
  search_start: request.search.start,
  search_end: request.search.end,
  matches: [],
  statistics: {
    symbols_requested: 1,
    symbols_scanned: 1,
    windows_evaluated: 50,
    windows_passing_threshold: 0,
    matches_returned: 0,
    universe_id: 'custom',
    universe_symbols_total: 1,
    symbols_eligible: 1,
    symbols_skipped: 0,
    symbols_failed: 0,
    universe_stale: false,
    ready_before_hydration: 0,
    hydration_limit: 0,
    hydration_attempted: 0,
    hydration_succeeded: 0,
    hydration_failed: 0,
    ready_after_hydration: 1,
    provider_rate_limited: false,
    hydration_provider_unavailable: false,
    hydration_timed_out: false,
  },
}

describe('similarity search API client', () => {
  it('posts the typed backend contract', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(success),
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(searchSimilarity(request)).resolves.toEqual(success)
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/similarity/search',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(request),
      }),
    )
  })

  it('rejects API errors and malformed success bodies safely', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 429,
          json: () =>
            Promise.resolve({ error: { code: 'PROVIDER_RATE_LIMITED' } }),
        }),
      ),
    )
    await expect(searchSimilarity(request)).rejects.toMatchObject({
      code: 'PROVIDER_RATE_LIMITED',
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        }),
      ),
    )
    await expect(searchSimilarity(request)).rejects.toBeInstanceOf(
      MarketDataApiError,
    )
  })
})
