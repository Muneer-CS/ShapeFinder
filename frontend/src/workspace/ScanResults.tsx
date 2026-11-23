import { useState } from 'react'
import { reasonText, type ScanResult } from '../api/patternScans'
import type { Pattern } from '../pattern/model'
import { matchDates } from '../pattern/comparison'
import { ComparisonDetail, ComparisonPreview } from './ComparisonChart'

function ScanCoverage({ result }: { result: ScanResult }) {
  const { coverage, execution } = result
  const sessionDates = [
    ...new Set(Object.values(result.evaluated_session_dates ?? {})),
  ].sort()
  const exclusions = Object.entries(result.reason_counts ?? {}).filter(
    ([, count]) => count > 0,
  )
  return (
    <div className="coverage-card">
      <p className="coverage-summary">
        {result.outcome === 'partial' || execution.partial
          ? 'Partial scan. '
          : 'Scan evaluated. '}
        <strong>
          {coverage.scanned} of {coverage.universe_total}{' '}
          {coverage.universe_total === 1 ? 'stock' : 'stocks'} evaluated.
        </strong>{' '}
        <strong>
          {result.matches.length}{' '}
          {result.matches.length === 1 ? 'match' : 'matches'} shown.
        </strong>
      </p>
      <p className="coverage-counts">
        {coverage.stale} stale · {coverage.skipped} skipped · {coverage.failed}{' '}
        failed · {coverage.not_evaluated} unevaluated
        {!!coverage.evaluation_incomplete &&
          ` (${coverage.evaluation_incomplete} interrupted)`}
      </p>
      {!!execution.stop_reasons.length && (
        <p className="stop-summary">
          Stopped: {execution.stop_reasons.map(reasonText).join(', ')}.
        </p>
      )}
      {!!exclusions.length && (
        <ul
          className="coverage-reasons"
          aria-label="Reasons stocks were excluded or unevaluated"
        >
          {exclusions.map(([reason, count]) => (
            <li key={reason}>
              {count} · {reasonText(reason)}
            </li>
          ))}
        </ul>
      )}
      {!!sessionDates.length && (
        <p className="coverage-counts">
          Daily sessions evaluated: {sessionDates.join(', ')}.
        </p>
      )}
      {result.universe_snapshot?.catalog_stale && (
        <p className="notice">
          Stock membership uses a stale cached catalogue.
        </p>
      )}
    </div>
  )
}

export function ScanResults({
  result,
  previous,
  pattern,
  busy,
  cancelled,
  failure,
}: {
  result: ScanResult | null
  previous: boolean
  pattern?: Pattern | null
  busy: boolean
  cancelled: boolean
  failure: { message: string; details?: ScanResult | null } | null
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const match =
    result?.matches.find((m) => m.symbol === selected) ?? result?.matches[0]
  return (
    <section className="results-panel" aria-labelledby="results-title">
      <div className="section-heading">
        <h2 id="results-title">Ranked stocks</h2>
        {result && (
          <span className={`badge ${previous ? 'warning-badge' : ''}`}>
            {previous ? 'Previous scan' : 'Current scan'}
          </span>
        )}
      </div>
      {busy && (
        <p className="result-feedback">Scanning the selected universe…</p>
      )}
      {cancelled && !busy && <p className="result-feedback">Scan cancelled.</p>}
      {failure && (
        <div className="result-feedback">
          <p>{failure.message}</p>
          {failure.details && <ScanCoverage result={failure.details} />}
        </div>
      )}
      {!result && !busy && !cancelled && !failure && (
        <div className="empty-results">
          <h3>No scans yet</h3>
          <p>
            No scan results yet. Create a pattern, review the settings, then run
            a scan.
          </p>
        </div>
      )}
      {result && (
        <>
          {previous && (
            <p className="notice">
              These results belong to an earlier pattern, settings revision, or
              scan. Run again to update them.
            </p>
          )}
          <ScanCoverage result={result} />
          {result.matches.length === 0 ? (
            <div className="empty-results">
              <h3>No qualifying matches</h3>
              <p>
                {result.outcome === 'partial'
                  ? 'No matches among the evaluated stocks. The remaining universe was not fully evaluated.'
                  : 'No matches met these settings among the evaluated stocks.'}
              </p>
            </div>
          ) : (
            <div className="result-comparison-grid">
              <div className="ranked-list table-scroll">
                <table>
                  <caption>One result per stock, in scan rank order</caption>
                  <thead>
                    <tr>
                      <th>Stock</th>
                      <th>Shape</th>
                      <th>Similarity</th>
                      <th>Completion</th>
                      <th className="row-movement">Movement</th>
                      <th>Last candle</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.matches.map((item, i) => (
                      <tr
                        key={item.symbol}
                        data-selected={match?.symbol === item.symbol}
                      >
                        <th scope="row">
                          <button
                            className="stock-select"
                            aria-label={`Compare ${item.symbol}`}
                            aria-pressed={match?.symbol === item.symbol}
                            onClick={() => setSelected(item.symbol)}
                          >
                            <span className="rank-number">
                              {String(i + 1).padStart(2, '0')}
                            </span>
                            {item.symbol}
                          </button>
                          <span className={`match-type ${item.match_type}`}>
                            {item.match_type === 'full' ? 'Full' : 'Forming'}
                          </span>
                        </th>
                        <td>
                          <ComparisonPreview pattern={pattern} match={item} />
                        </td>
                        <td>{item.similarity_score.toFixed(1)} / 100</td>
                        <td>{(item.estimated_completion * 100).toFixed(1)}%</td>
                        <td className="row-movement">
                          {item.movement_percent.toFixed(2)}%
                        </td>
                        <td className="row-last-candle">
                          <time dateTime={matchDates(item).end}>
                            {matchDates(item).end}
                          </time>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {match && (
                <ComparisonDetail
                  key={match.symbol}
                  match={match}
                  pattern={pattern}
                />
              )}
            </div>
          )}
          <details className="diagnostics">
            <summary>Scan coverage and response details</summary>
            <p>
              {result.coverage.stale} stale · {result.coverage.skipped} skipped
              · {result.coverage.failed} failed ·{' '}
              {result.coverage.not_evaluated} not evaluated (
              {result.coverage.evaluation_incomplete} interrupted). Eligible:{' '}
              {result.coverage.eligible}.
            </p>
            <p>
              Market cutoff: {result.cutoff.cutoff_at}.{' '}
              {result.settings.interval}, regular session. Timing basis:{' '}
              {result.capability?.timing_basis.replaceAll('_', ' ') ??
                'not supplied'}
              .
            </p>
            <p>
              {result.provider_usage.http_attempts} provider attempts;{' '}
              {result.provider_usage.documented_credit_cost} documented credits
              of exposure; actual billed consumption is unknown.
            </p>
            <p>
              Pattern revision {result.revisions.pattern_revision}; settings
              revision {result.revisions.settings_revision}. Content hash{' '}
              {result.pattern_content_hash}.
            </p>
          </details>
        </>
      )}
    </section>
  )
}
