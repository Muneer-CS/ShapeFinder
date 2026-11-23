import type { Match, ScanRequest, ScanResult } from '../api/patternScans'
import { drawingProvenance, patternHash, type Pattern } from '../pattern/model'
export const comparisonPattern = (): Pattern => ({
  schema_version: 1,
  points: [
    { x: 0, y: 0 },
    { x: 0.5, y: 1 },
    { x: 1, y: -2 },
  ],
  provenance: drawingProvenance(),
})
export function comparisonMatch(symbol = 'AAPL', full = false): Match {
  const n = full ? 21 : 11,
    logs = Array.from({ length: n }, (_, i) =>
      full ? (i <= 10 ? i / 100 : (20 - i) / 100) : i / 100,
    )
  const observations = logs.map((value, i) => ({
    timestamp: new Date(Date.UTC(2025, 0, i + 1, 14, 30))
      .toISOString()
      .replace('.000Z', 'Z'),
    close: String(100 * Math.exp(value)),
  }))
  return {
    symbol,
    similarity_score: 92.4,
    estimated_completion: (n - 1) / 20,
    match_type: full ? 'full' : 'forming',
    movement_percent: 10.52,
    observed_candles: n,
    complete_duration_candles: 21,
    start_at: observations[0].timestamp,
    freshness: {
      cutoff_at: '2025-01-21T22:00:00Z',
      expected: {
        open_at: observations.at(-1)!.timestamp,
        close_at: new Date(
          Date.parse(observations.at(-1)!.timestamp) + 6 * 3600000,
        ).toISOString(),
        timezone: 'UTC',
      },
      retrieved_at: '2025-01-21T22:00:00Z',
      is_complete: true,
      snapshot_at: null,
    },
    comparison: { observations, log_relative_closes: logs },
  }
}
export async function comparisonResult(
  request: ScanRequest,
): Promise<ScanResult> {
  return {
    scan_id: 'comparison-fixture',
    revisions: request.revisions,
    pattern_content_hash: await patternHash(request.pattern),
    settings: request.settings,
    cutoff: { cutoff_at: '2025-01-21T22:00:00Z' },
    outcome: 'partial',
    matches: [comparisonMatch(), comparisonMatch('MSFT', true)],
    coverage: {
      universe_total: 10,
      scanned: 2,
      eligible: 2,
      stale: 0,
      skipped: 0,
      failed: 0,
      not_evaluated: 8,
      evaluation_incomplete: 0,
      matches_returned: 2,
    },
    execution: {
      partial: true,
      stop_reasons: ['candidate_limit'],
      provider_requests: 0,
      hypotheses_evaluated: 2,
    },
    provider_usage: {
      http_attempts: 0,
      documented_credit_cost: 0,
      unknown_cost_attempts: 0,
      actual_credits_consumed: null,
    },
    universe_snapshot: null,
    capability: null,
    reason_counts: {},
  }
}
