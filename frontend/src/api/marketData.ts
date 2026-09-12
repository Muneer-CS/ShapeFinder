export const intervals = [
  { value: '1min', label: '1 minute' },
  { value: '5min', label: '5 minutes' },
  { value: '15min', label: '15 minutes' },
  { value: '30min', label: '30 minutes' },
  { value: '1h', label: '1 hour' },
  { value: '1day', label: '1 day' },
] as const

export type MarketInterval = (typeof intervals)[number]['value']
export type PriceBar = {
  timestamp: string
  open: string
  high: string
  low: string
  close: string
  volume: string
}
export type TimeSeriesResponse = {
  symbol: string
  interval: MarketInterval
  timezone: string
  bars: PriceBar[]
}
type ApiErrorBody = { error?: { code?: string; message?: string } }

export class MarketDataApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message)
    this.name = 'MarketDataApiError'
  }
}

function isTimeSeriesResponse(value: unknown): value is TimeSeriesResponse {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<TimeSeriesResponse>
  return (
    typeof candidate.symbol === 'string' &&
    typeof candidate.interval === 'string' &&
    typeof candidate.timezone === 'string' &&
    Array.isArray(candidate.bars) &&
    candidate.bars.every(
      (bar) =>
        typeof bar.timestamp === 'string' &&
        typeof bar.close === 'string' &&
        typeof bar.open === 'string' &&
        typeof bar.high === 'string' &&
        typeof bar.low === 'string' &&
        typeof bar.volume === 'string',
    )
  )
}

export async function getMarketData(
  symbol: string,
  start: string,
  end: string,
  interval: MarketInterval,
  signal?: AbortSignal,
): Promise<TimeSeriesResponse> {
  const query = new URLSearchParams({ start, end, interval })
  const response = await fetch(
    `${apiBaseUrl}/api/v1/market-data/${encodeURIComponent(symbol)}?${query}`,
    { signal },
  )
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const errorBody = body as ApiErrorBody | null
    throw new MarketDataApiError(
      errorBody?.error?.code ?? 'UNKNOWN_ERROR',
      errorBody?.error?.message ?? 'The request could not be completed.',
      response.status,
    )
  }
  if (!isTimeSeriesResponse(body))
    throw new MarketDataApiError(
      'INVALID_RESPONSE',
      'The server returned an unexpected response.',
      response.status,
    )
  return body
}
import { apiBaseUrl } from './config'
