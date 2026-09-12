import { MarketDataApiError } from './marketData'

export type UniverseKind = 'us_equities' | 'nasdaq' | 'nyse'
export type UniverseInfo = {
  id: UniverseKind
  name: string
  total_symbols: number
  refreshed_at: string | null
  stale: boolean
}

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function getUniverses(
  signal?: AbortSignal,
): Promise<UniverseInfo[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/universes`, { signal })
  const body: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const error = body as { error?: { code?: string; message?: string } } | null
    throw new MarketDataApiError(
      error?.error?.code ?? 'UNKNOWN_ERROR',
      error?.error?.message ?? 'Stock universes are unavailable.',
      response.status,
    )
  }
  if (
    !body ||
    typeof body !== 'object' ||
    !Array.isArray((body as { universes?: unknown }).universes)
  )
    throw new MarketDataApiError(
      'INVALID_RESPONSE',
      'The server returned an unexpected response.',
      response.status,
    )
  return (body as { universes: UniverseInfo[] }).universes
}
