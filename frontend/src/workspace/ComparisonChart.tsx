import { useId, useState } from 'react'
import type { Match } from '../api/patternScans'
import type { Pattern, Point } from '../pattern/model'
import {
  comparisonAlignment,
  datedCandles,
  matchDates,
  observedPricePositions,
  type Alignment,
} from '../pattern/comparison'
import { CandleChart } from './CandleChart'

function range(alignment: Alignment): [number, number] {
  const values = [
    ...alignment.reference,
    ...alignment.observed,
    ...alignment.continuation,
  ]
    .map((p) => p.y)
    .filter(Number.isFinite)
  const lo = Math.min(...values),
    hi = Math.max(...values),
    padding = (hi - lo || 1) * 0.14
  return [lo - padding, hi + padding]
}
function path(
  points: Point[],
  x: (n: number) => number,
  y: (n: number) => number,
) {
  let previous = false
  return points
    .map((p) => {
      if (!Number.isFinite(p.y)) {
        previous = false
        return ''
      }
      const px = x(p.x),
        py = y(p.y)
      if (!Number.isFinite(px) || !Number.isFinite(py)) {
        previous = false
        return ''
      }
      const command = previous ? 'L' : 'M'
      previous = true
      return `${command}${px},${py}`
    })
    .join(' ')
}
function ShapePlot({
  alignment,
  mini,
  label,
}: {
  alignment: Alignment
  mini?: boolean
  label: string
}) {
  const id = useId(),
    [lo, hi] = range(alignment)
  const width = mini ? 120 : 640,
    height = mini ? 44 : 312
  const left = mini ? 2 : 42,
    right = mini ? 2 : 18,
    top = mini ? 3 : 20,
    bottom = mini ? 3 : 66
  const x = (value: number) => left + value * (width - left - right)
  const y = (value: number) =>
    top + ((hi - value) / (hi - lo)) * (height - top - bottom)
  return (
    <svg
      className={mini ? 'mini-comparison' : 'comparison-svg'}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      <defs>
        <clipPath id={id}>
          <rect
            x={left}
            y={top}
            width={width - left - right}
            height={height - top - bottom}
          />
        </clipPath>
      </defs>
      {!mini && (
        <>
          {[0, 0.25, 0.5, 0.75, 1].map((value) => (
            <g key={value}>
              <line
                className="chart-grid"
                x1={x(value)}
                x2={x(value)}
                y1={top}
                y2={height - bottom}
              />
              <text
                className="chart-tick"
                x={x(value)}
                y={height - bottom + 27}
                textAnchor="middle"
              >
                {value}
              </text>
            </g>
          ))}
          {[lo, 0, hi].map((value) => (
            <line
              key={value}
              className="chart-grid"
              x1={left}
              x2={width - right}
              y1={y(value)}
              y2={y(value)}
            />
          ))}
          <text
            className="chart-tick"
            x={width / 2}
            y={height - 5}
            textAnchor="middle"
          >
            Complete-pattern progress
          </text>
        </>
      )}
      <g clipPath={`url(#${id})`}>
        <path className="reference-line" d={path(alignment.reference, x, y)} />
        <path
          className="continuation-line"
          d={path(alignment.continuation, x, y)}
        />
        <path
          className="observed-line"
          data-series="observed"
          data-end-progress={alignment.completion}
          d={path(alignment.observed, x, y)}
        />
      </g>
      {!mini && (
        <>
          <line
            className="completion-marker"
            x1={x(alignment.completion)}
            x2={x(alignment.completion)}
            y1={top}
            y2={height - bottom}
          />
          <text
            className="chart-tick"
            x={x(alignment.completion) - 4}
            y={top + 12}
            textAnchor={alignment.completion < 0.1 ? 'start' : 'end'}
          >
            {(alignment.completion * 100).toFixed(1)}% observed
          </text>
          <circle
            className="observed-end"
            cx={x(alignment.completion)}
            cy={y(alignment.observed.at(-1)!.y)}
            r="3.5"
          />
        </>
      )}
    </svg>
  )
}
export function ComparisonPreview({
  pattern,
  match,
}: {
  pattern?: Pattern | null
  match: Match
}) {
  const alignment = pattern && comparisonAlignment(pattern, match)
  return alignment ? (
    <ShapePlot
      mini
      alignment={alignment}
      label={`${match.symbol} alignment preview`}
    />
  ) : (
    <span className="unavailable-preview">No samples</span>
  )
}
function PricePlot({ match }: { match: Match }) {
  const points = observedPricePositions(match),
    data = match.comparison!
  if (!points.length)
    return (
      <p>
        These prices cannot be drawn on a linear scale. Exact observations
        remain available below.
      </p>
    )
  const logs = data.log_relative_closes,
    min = logs.indexOf(Math.min(...logs)),
    max = logs.indexOf(Math.max(...logs))
  const x = (n: number) => 120 + n * 500,
    y = (n: number) => 230 - n * 200
  return (
    <figure>
      <svg
        className="comparison-svg"
        viewBox="0 0 640 285"
        role="img"
        aria-label={`${match.symbol} observed closing prices`}
      >
        {[0, 0.5, 1].map((n) => (
          <line
            key={n}
            className="chart-grid"
            x1="120"
            x2="620"
            y1={y(n)}
            y2={y(n)}
          />
        ))}
        <text className="chart-tick" x="112" y={y(1) + 4} textAnchor="end">
          {data.observations[max].close.length <= 9
            ? data.observations[max].close
            : Number.isFinite(Number(data.observations[max].close))
              ? Number(data.observations[max].close).toPrecision(5)
              : 'Maximum'}
        </text>
        <text className="chart-tick" x="112" y={y(0) + 4} textAnchor="end">
          {data.observations[min].close.length <= 9
            ? data.observations[min].close
            : Number.isFinite(Number(data.observations[min].close))
              ? Number(data.observations[min].close).toPrecision(5)
              : 'Minimum'}
        </text>
        <path className="observed-line" d={path(points, x, y)} />
        <text className="chart-tick" x="120" y="252">
          {new Date(data.observations[0].timestamp)
            .toISOString()
            .slice(0, 16)
            .replace('T', ' ')}
        </text>
        <text className="chart-tick" x="620" y="252" textAnchor="end">
          {new Date(data.observations.at(-1)!.timestamp)
            .toISOString()
            .slice(0, 16)
            .replace('T', ' ')}
        </text>
        <text className="chart-tick" x="355" y="278" textAnchor="middle">
          Observation time (UTC)
        </text>
      </svg>
      <figcaption>
        Linear quoted price. Currency is not supplied by this history contract.
        Exact values and candle-open timestamps are available below.
      </figcaption>
    </figure>
  )
}
export function ComparisonDetail({
  pattern,
  match,
}: {
  pattern?: Pattern | null
  match: Match
}) {
  const [view, setView] = useState<'shape' | 'price'>('shape')
  const alignment = pattern && comparisonAlignment(pattern, match)
  const dates = matchDates(match)
  const candles = view === 'price' ? datedCandles(match) : null
  return (
    <section
      className="comparison-detail"
      aria-label={`${match.symbol} comparison`}
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">Selected comparison</span>
          <h2>
            {match.symbol}{' '}
            <span className="badge">
              {match.match_type === 'full' ? 'Full' : 'Forming'}
            </span>
          </h2>
          <p className="matched-dates">
            Matched period <time dateTime={dates.start}>{dates.start}</time> to{' '}
            <time dateTime={dates.end}>{dates.end}</time>
          </p>
        </div>
        <div className="chart-tabs" aria-label="Comparison view">
          <button
            aria-pressed={view === 'shape'}
            onClick={() => setView('shape')}
          >
            Aligned shape
          </button>
          <button
            aria-pressed={view === 'price'}
            onClick={() => setView('price')}
          >
            Observed prices
          </button>
        </div>
      </div>
      <dl className="match-measures">
        <div>
          <dt>Similarity</dt>
          <dd>
            {match.similarity_score.toFixed(1)}
            <small> / 100</small>
          </dd>
        </div>
        <div>
          <dt>Estimated completion</dt>
          <dd>
            {(match.estimated_completion * 100).toFixed(1)}
            <small>%</small>
          </dd>
        </div>
        <div>
          <dt>Observed movement</dt>
          <dd>
            {match.movement_percent.toFixed(2)}
            <small>%</small>
          </dd>
        </div>
      </dl>
      {view === 'price' ? (
        candles ? (
          <CandleChart candles={candles} symbol={match.symbol} />
        ) : (
          <>
            <p className="notice">
              {match.comparison
                ? 'This response has closing prices only. Candlesticks require a dated open, high, low, close and volume for each session.'
                : 'This response has no saved price observations for the selected match.'}
            </p>
            {match.comparison && <PricePlot match={match} />}
          </>
        )
      ) : alignment ? (
        <figure>
          <ShapePlot
            alignment={alignment}
            label={`${match.symbol} observed shape and reference comparison`}
          />
          <ul className="chart-legend">
            <li>
              <i className="observed-swatch" />
              Observed stock
            </li>
            <li>
              <i className="reference-swatch" />
              Matched reference
            </li>
            {alignment.completion < 1 && (
              <li>
                <i className="continuation-swatch" />
                Remaining reference
              </li>
            )}
          </ul>
          {alignment.completion < 1 && (
            <figcaption>Reference continuation, not a prediction.</figcaption>
          )}
        </figure>
      ) : (
        <p className="empty-comparison">
          Comparison samples are unavailable for this response. No additional
          prices are fetched.
        </p>
      )}
      <p className="freshness-line">
        {match.observed_candles} observed / {match.complete_duration_candles}{' '}
        complete candles.{' '}
        {match.freshness.is_complete ? 'Completed' : 'Unfinished'} candle ending{' '}
        <time>{match.freshness.expected.close_at}</time>.
      </p>
      <details className="diagnostics">
        <summary>Observation and alignment details</summary>
        <p>
          From {match.start_at}. Last candle opens{' '}
          {match.freshness.expected.open_at}; retrieved{' '}
          {match.freshness.retrieved_at}.
          {match.freshness.snapshot_at &&
            ` Snapshot ${match.freshness.snapshot_at}.`}
        </p>
        {alignment && (
          <p>
            Unit-RMS shapes, with nonnegative fitted scale{' '}
            {alignment.scale.toPrecision(5)}. Alignment uses only the matched
            prefix. The vertical axis includes the complete reference.
          </p>
        )}
        {match.comparison && (
          <div className="table-scroll">
            <table>
              <caption>Exact observed closes in supplied quote units</caption>
              <thead>
                <tr>
                  <th>Candle opens (UTC offset shown)</th>
                  <th>Close</th>
                </tr>
              </thead>
              <tbody>
                {match.comparison.observations.map((bar) => (
                  <tr key={bar.timestamp}>
                    <td>{bar.timestamp}</td>
                    <td>{bar.close}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  )
}
