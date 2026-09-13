import { MarketDataApiError, type MarketInterval } from './marketData'
import { apiBaseUrl } from './config'

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
  } & (
    | { symbols: string[]; universe?: never }
    | {
        symbols?: never
        universe: { kind: 'us_equities' | 'nasdaq' | 'nyse' }
      }
  )
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
    universe_id: string
    universe_symbols_total: number
    symbols_eligible: number
    symbols_skipped: number
    symbols_failed: number
    universe_stale: boolean
    ready_before_hydration: number
    hydration_limit: number
    hydration_attempted: number
    hydration_succeeded: number
    hydration_failed: number
    hydration_fetched: number
    hydration_persisted: number
    hydration_became_ready: number
    hydration_suppressed: number
    hydration_failure_counts: Record<string, number>
    ready_after_hydration: number
    provider_rate_limited: boolean
    provider_daily_quota: boolean
    hydration_provider_unavailable: boolean
    hydration_timed_out: boolean
  }
}

type ApiErrorBody = { error?: { code?: string; message?: string } }
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
    isNumber(statistics.matches_returned) &&
    typeof statistics.universe_id === 'string' &&
    isNumber(statistics.universe_symbols_total) &&
    isNumber(statistics.symbols_eligible) &&
    isNumber(statistics.symbols_skipped) &&
    isNumber(statistics.symbols_failed) &&
    typeof statistics.universe_stale === 'boolean' &&
    isNumber(statistics.ready_before_hydration) &&
    isNumber(statistics.hydration_limit) &&
    isNumber(statistics.hydration_attempted) &&
    isNumber(statistics.hydration_succeeded) &&
    isNumber(statistics.hydration_failed) &&
    isNumber(statistics.hydration_fetched) &&
    isNumber(statistics.hydration_persisted) &&
    isNumber(statistics.hydration_became_ready) &&
    isNumber(statistics.hydration_suppressed) &&
    !!statistics.hydration_failure_counts &&
    typeof statistics.hydration_failure_counts === 'object' &&
    isNumber(statistics.ready_after_hydration) &&
    typeof statistics.provider_rate_limited === 'boolean' &&
    typeof statistics.provider_daily_quota === 'boolean' &&
    typeof statistics.hydration_provider_unavailable === 'boolean' &&
    typeof statistics.hydration_timed_out === 'boolean'
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
