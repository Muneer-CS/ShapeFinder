import { MarketDataApiError, type MarketInterval } from './marketData'

export type SimilaritySearchRequest = {
  reference: {
    symbol: string
    start: string
    end: string
    interval: MarketInterval
  }
  search: {
    start: string
    end: string
    symbols: string[]
  }
  top_n: number
  minimum_similarity: number | null
}

export type SimilarityComponents = {
  shape: number
  direction: number
  error: number
  amplitude: number
}

export type SimilarityMatch = {
  symbol: string
  start: string
  end: string
  interval: MarketInterval
  bar_count: number
  overall_score: number
  components: SimilarityComponents
}

export type SimilaritySearchResponse = {
  reference: {
    symbol: string
    start: string
    end: string
    interval: MarketInterval
    bar_count: number
  }
  search_start: string
  search_end: string
  matches: SimilarityMatch[]
  statistics: {
    symbols_requested: number
    symbols_scanned: number
    windows_evaluated: number
    windows_passing_threshold: number
    matches_returned: number
  }
}

type ApiErrorBody = { error?: { code?: string; message?: string } }
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isMatch(value: unknown): value is SimilarityMatch {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<SimilarityMatch>
  const components = item.components as
    Partial<SimilarityComponents> | undefined
  return (
    typeof item.symbol === 'string' &&
    typeof item.start === 'string' &&
    typeof item.end === 'string' &&
    typeof item.interval === 'string' &&
    isNumber(item.bar_count) &&
    isNumber(item.overall_score) &&
    !!components &&
    isNumber(components.shape) &&
    isNumber(components.direction) &&
    isNumber(components.error) &&
    isNumber(components.amplitude)
  )
}

function isSimilaritySearchResponse(
  value: unknown,
): value is SimilaritySearchResponse {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<SimilaritySearchResponse>
  const reference = item.reference as
    Partial<SimilaritySearchResponse['reference']> | undefined
  const statistics = item.statistics as
    Partial<SimilaritySearchResponse['statistics']> | undefined
  return (
    !!reference &&
    typeof reference.symbol === 'string' &&
    typeof reference.start === 'string' &&
    typeof reference.end === 'string' &&
    typeof reference.interval === 'string' &&
    isNumber(reference.bar_count) &&
    typeof item.search_start === 'string' &&
    typeof item.search_end === 'string' &&
    Array.isArray(item.matches) &&
    item.matches.every(isMatch) &&
    !!statistics &&
    isNumber(statistics.symbols_requested) &&
    isNumber(statistics.symbols_scanned) &&
    isNumber(statistics.windows_evaluated) &&
    isNumber(statistics.windows_passing_threshold) &&
    isNumber(statistics.matches_returned)
  )
}

export async function searchSimilarity(
  request: SimilaritySearchRequest,
  signal?: AbortSignal,
): Promise<SimilaritySearchResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/similarity/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const errorBody = body as ApiErrorBody | null
    throw new MarketDataApiError(
      errorBody?.error?.code ?? 'UNKNOWN_ERROR',
      errorBody?.error?.message ?? 'The search could not be completed.',
      response.status,
    )
  }
  if (!isSimilaritySearchResponse(body))
    throw new MarketDataApiError(
      'INVALID_RESPONSE',
      'The server returned an unexpected response.',
      response.status,
    )
  return body
}
