import { apiBaseUrl } from './config'
import type { Pattern } from '../pattern/model'
import type { Settings, Universe } from '../pattern/settings'
export type Revisions = { pattern_revision: number; settings_revision: number }
export type Capability = {
  interval: string
  available: boolean
  status: 'usable' | 'configured_but_unverified' | 'unavailable'
  reason: string
  timing_basis: 'unknown' | 'operator_configured' | 'operator_attested'
  provider_timing_guarantee_verified: boolean
  unfinished_available: boolean
  unfinished_reason: string
}
export type Capabilities = {
  intervals: Capability[]
  calendar_version: string
  hydration_configured: boolean
  accept_configured_policies: boolean
}
export type ScanRequest = {
  pattern: Pattern
  revisions: Revisions
  preview_acknowledgement: {
    pattern_revision: number
    content_hash: string
  } | null
  settings: Settings
  universe: Universe
}
export type CandleObservation = {
  timestamp: string
  close: string
  open?: string | null
  high?: string | null
  low?: string | null
  volume?: string | null
}
export type Match = {
  symbol: string
  similarity_score: number
  estimated_completion: number
  match_type: 'full' | 'forming'
  movement_percent: number
  observed_candles: number
  complete_duration_candles: number
  start_at: string
  matched_start_session?: string | null
  last_candle_session?: string | null
  comparison?: {
    observations: CandleObservation[]
    log_relative_closes: number[]
  } | null
  freshness: {
    cutoff_at: string
    expected: {
      open_at: string
      close_at: string
      timezone: string
      session_date?: string
    }
    retrieved_at: string
    is_complete: boolean
    snapshot_at: string | null
  }
}
export type ScanResult = {
  scan_id: string
  revisions: Revisions
  pattern_content_hash: string
  cutoff: { cutoff_at: string }
  settings: Settings
  outcome: 'evaluated' | 'partial'
  matches: Match[]
  coverage: {
    universe_total: number
    scanned: number
    eligible: number
    stale: number
    skipped: number
    failed: number
    not_evaluated: number
    evaluation_incomplete: number
    matches_returned: number
  }
  execution: {
    partial: boolean
    stop_reasons: string[]
    provider_requests: number
    hypotheses_evaluated: number
  }
  provider_usage: {
    http_attempts: number
    documented_credit_cost: number
    unknown_cost_attempts: number
    actual_credits_consumed: null
  }
  universe_snapshot: {
    catalog_stale: boolean
    catalog_refreshed_at: string | null
  } | null
  capability: Capability | null
  reason_counts: Record<string, number>
  evaluated_session_dates?: Record<string, string>
}
export class ScanApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public reason?: string,
    public details?: ScanResult | null,
  ) {
    super(message)
    this.name = 'ScanApiError'
  }
}
function validCapability(value: Capability): boolean {
  return (
    !!value &&
    typeof value.interval === 'string' &&
    typeof value.available === 'boolean' &&
    ['usable', 'configured_but_unverified', 'unavailable'].includes(
      value.status,
    ) &&
    ['unknown', 'operator_configured', 'operator_attested'].includes(
      value.timing_basis,
    ) &&
    typeof value.provider_timing_guarantee_verified === 'boolean' &&
    typeof value.unfinished_available === 'boolean' &&
    typeof value.unfinished_reason === 'string' &&
    typeof value.reason === 'string'
  )
}
function validResult(body: ScanResult): boolean {
  const count = (value: number) => Number.isInteger(value) && value >= 0
  const instant = (value: string) =>
    typeof value === 'string' && Number.isFinite(Date.parse(value))
  const coverage = body?.coverage,
    usage = body?.provider_usage
  return (
    !!body &&
    ['evaluated', 'partial'].includes(body.outcome) &&
    Array.isArray(body.matches) &&
    !!body.revisions &&
    count(body.revisions.pattern_revision) &&
    count(body.revisions.settings_revision) &&
    typeof body.pattern_content_hash === 'string' &&
    /^[0-9a-f]{64}$/.test(body.pattern_content_hash) &&
    !!body.cutoff &&
    instant(body.cutoff.cutoff_at) &&
    !!body.settings &&
    typeof body.settings.interval === 'string' &&
    !!coverage &&
    [
      'universe_total',
      'scanned',
      'eligible',
      'stale',
      'skipped',
      'failed',
      'not_evaluated',
      'evaluation_incomplete',
      'matches_returned',
    ].every((key) => count(coverage[key as keyof typeof coverage])) &&
    !!body.execution &&
    typeof body.execution.partial === 'boolean' &&
    Array.isArray(body.execution.stop_reasons) &&
    body.execution.stop_reasons.every((reason) => typeof reason === 'string') &&
    !!usage &&
    [
      usage.http_attempts,
      usage.documented_credit_cost,
      usage.unknown_cost_attempts,
    ].every(count) &&
    (body.capability === null || validCapability(body.capability)) &&
    (body.universe_snapshot === null ||
      (typeof body.universe_snapshot?.catalog_stale === 'boolean' &&
        (body.universe_snapshot.catalog_refreshed_at === null ||
          instant(body.universe_snapshot.catalog_refreshed_at)))) &&
    new Set(body.matches.map((m) => m?.symbol)).size === body.matches.length &&
    body.matches.every(
      (m) =>
        !!m &&
        typeof m.symbol === 'string' &&
        !!m.symbol &&
        [m.similarity_score, m.estimated_completion, m.movement_percent].every(
          Number.isFinite,
        ) &&
        ['full', 'forming'].includes(m.match_type) &&
        count(m.observed_candles) &&
        count(m.complete_duration_candles) &&
        m.observed_candles >= 8 &&
        m.observed_candles <= m.complete_duration_candles &&
        m.complete_duration_candles <= 240 &&
        Math.abs(
          m.estimated_completion -
            (m.observed_candles - 1) / (m.complete_duration_candles - 1),
        ) <=
          Number.EPSILON * 4 &&
        (m.match_type === 'full') ===
          (m.observed_candles === m.complete_duration_candles) &&
        !!m.freshness &&
        !!m.freshness.expected &&
        instant(m.freshness.expected.open_at) &&
        instant(m.freshness.expected.close_at) &&
        instant(m.freshness.retrieved_at) &&
        typeof m.freshness.is_complete === 'boolean' &&
        (m.comparison == null ||
          (Array.isArray(m.comparison.observations) &&
            m.comparison.observations.length === m.observed_candles &&
            m.observed_candles >= 8 &&
            m.observed_candles <= 240 &&
            Array.isArray(m.comparison.log_relative_closes) &&
            m.comparison.log_relative_closes.length === m.observed_candles &&
            m.comparison.log_relative_closes.every(Number.isFinite) &&
            m.comparison.log_relative_closes[0] === 0 &&
            m.comparison.observations.every(
              (bar, i, all) =>
                instant(bar.timestamp) &&
                typeof bar.close === 'string' &&
                bar.close.length <= 128 &&
                /^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(bar.close) &&
                /[1-9]/.test(bar.close.split(/e/i)[0]) &&
                (i === 0 ||
                  Date.parse(bar.timestamp) > Date.parse(all[i - 1].timestamp)),
            ) &&
            Date.parse(m.comparison.observations[0].timestamp) ===
              Date.parse(m.start_at) &&
            Date.parse(m.comparison.observations.at(-1)!.timestamp) ===
              Date.parse(m.freshness.expected.open_at))) &&
        (m.freshness.snapshot_at === null || instant(m.freshness.snapshot_at)),
    )
  )
}
async function read(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const error = body?.error
    throw new ScanApiError(
      response.status,
      error?.code ?? 'UNKNOWN_ERROR',
      error?.message ?? 'The request could not be completed.',
      error?.reason,
      error?.details && validResult(error.details) ? error.details : null,
    )
  }
  return body
}
export async function getCapabilities(
  signal: AbortSignal,
): Promise<Capabilities> {
  const body = (await read(
    await fetch(`${apiBaseUrl}/api/v1/pattern-scans/capabilities`, { signal }),
  )) as Capabilities
  if (
    !body ||
    !Array.isArray(body.intervals) ||
    !body.intervals.every(validCapability) ||
    new Set(body.intervals.map((c) => c.interval)).size !==
      body.intervals.length
  )
    throw new Error('The server returned invalid scan capabilities.')
  return body
}
export async function scanPattern(
  request: ScanRequest,
  signal: AbortSignal,
): Promise<ScanResult> {
  const body = (await read(
    await fetch(`${apiBaseUrl}/api/v1/pattern-scans`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    }),
  )) as ScanResult
  if (!validResult(body))
    throw new Error('The server returned an invalid scan result.')
  return body
}
export function reasonText(reason: string): string {
  const reasons: Record<string, string> = {
    publication_finalization_unknown:
      'Publication and finalization timing have not been established.',
    unknown_capability:
      'Candle timing has not been established for this timeframe.',
    snapshot_timing_unverified:
      'Unfinished candle freshness has not been verified.',
    universe_catalog_unavailable:
      'The server has no cached stock catalogue. An operator must prepare it before scanning.',
    provider_budget: 'Provider request budget reached',
    computation_budget: 'Computation budget reached',
    deadline: 'Scan deadline reached',
    candidate_limit: 'Candidate limit reached',
    cancelled: 'Scan cancelled',
    provider_error: 'Some provider requests failed',
    missing_expected_candle: 'The expected completed candle was missing.',
    massive_daily_history_gap: 'The saved daily history has missing sessions.',
    massive_daily_stale_candle:
      'The last saved candle was outside the recency window.',
    massive_cache_read_error: 'Saved price history could not be read.',
    malformed_history: 'Saved price history failed validation.',
    hydration_symbol_limit: 'The history request limit was reached.',
  }
  return reasons[reason] ?? reason.replaceAll('_', ' ')
}
