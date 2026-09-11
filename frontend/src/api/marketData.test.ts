import { afterEach, describe, expect, it, vi } from 'vitest'
import { getMarketData, MarketDataApiError } from './marketData'

afterEach(() => vi.restoreAllMocks())

describe('market-data API client', () => {
  it('constructs the request and parses a typed response', async () => {
    const body = {
      symbol: 'AAPL',
      interval: '1day',
      timezone: 'UTC',
      bars: [
        {
          timestamp: '2025-01-01T00:00:00Z',
          open: '1',
          high: '2',
          low: '0.5',
          close: '1.5',
          volume: '10',
        },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(
      getMarketData(
        'AAPL',
        '2025-01-01T00:00:00Z',
        '2025-01-02T00:00:00Z',
        '1day',
      ),
    ).resolves.toEqual(body)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/market-data/AAPL?')
    expect(String(fetchMock.mock.calls[0][0])).toContain('interval=1day')
  })

  it('throws a stable typed error for API and malformed responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: () =>
          Promise.resolve({
            error: { code: 'INVALID_SYMBOL', message: 'safe' },
          }),
      }),
    )
    await expect(getMarketData('BAD', 'a', 'b', '1day')).rejects.toMatchObject<
      Partial<MarketDataApiError>
    >({ code: 'INVALID_SYMBOL', status: 404 })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ unexpected: true }),
      }),
    )
    await expect(getMarketData('AAPL', 'a', 'b', '1day')).rejects.toMatchObject<
      Partial<MarketDataApiError>
    >({ code: 'INVALID_RESPONSE' })
  })
})
