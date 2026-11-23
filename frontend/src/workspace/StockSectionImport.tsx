import { useEffect, useRef, useState } from 'react'
import {
  getMarketData,
  intervals,
  type MarketInterval,
  type TimeSeriesResponse,
} from '../api/marketData'
import { importSection, type Pattern } from '../pattern/model'

export function StockSectionImport({
  onImport,
  open = false,
}: {
  onImport: (pattern: Pattern) => void
  open?: boolean
}) {
  const [symbol, setSymbol] = useState('')
  const [interval, setInterval] = useState<MarketInterval>('1day')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [history, setHistory] = useState<TimeSeriesResponse | null>(null)
  const [first, setFirst] = useState(0),
    [last, setLast] = useState(1)
  const [selectingEnd, setSelectingEnd] = useState(false)
  const [loading, setLoading] = useState(false),
    [message, setMessage] = useState('')
  const active = useRef<{ id: number; controller: AbortController } | null>(
    null,
  )
  const sequence = useRef(0)
  useEffect(
    () => () => {
      active.current?.controller.abort()
      active.current = null
    },
    [],
  )
  function invalidate() {
    active.current?.controller.abort()
    active.current = null
    setLoading(false)
    setHistory(null)
    setMessage('')
  }
  async function load() {
    if (active.current) return
    const normalized = symbol.trim().toUpperCase()
    if (
      !/^[A-Z][A-Z0-9.-]{0,14}$/.test(normalized) ||
      !start ||
      !end ||
      start >= end
    ) {
      setMessage('Enter a supported symbol and ordered start and end dates.')
      return
    }
    const controller = new AbortController(),
      id = ++sequence.current
    active.current = { id, controller }
    setLoading(true)
    setMessage('')
    setHistory(null)
    try {
      const data = await getMarketData(
        normalized,
        `${start}T00:00:00Z`,
        `${end}T23:59:59.999Z`,
        interval,
        controller.signal,
      )
      if (active.current?.id !== id || controller.signal.aborted) return
      if (
        data.interval !== interval ||
        data.symbol !== normalized ||
        data.bars.length < 2
      )
        throw new Error(
          'The response must contain at least two observations for the requested symbol and timeframe.',
        )
      // Validate the history before plotting or allowing selection. A flat range
      // is allowed to load but remains ineligible for import until it has shape.
      if (
        data.bars.some(
          (bar, i) =>
            !Number.isFinite(Number(bar.close)) ||
            Number(bar.close) <= 0 ||
            !Number.isFinite(Date.parse(bar.timestamp)) ||
            (i > 0 &&
              Date.parse(bar.timestamp) <=
                Date.parse(data.bars[i - 1].timestamp)),
        )
      )
        throw new Error(
          'The history contains invalid prices or unordered timestamps.',
        )
      setHistory(data)
      setFirst(0)
      setLast(data.bars.length - 1)
      setSelectingEnd(false)
      setMessage(
        'Choose a start observation, then an end observation, or use the range controls.',
      )
    } catch (error) {
      if (active.current?.id === id && !controller.signal.aborted)
        setMessage((error as Error).message)
    } finally {
      if (active.current?.id === id) {
        active.current = null
        setLoading(false)
      }
    }
  }
  const bars = history?.bars ?? []
  const prices = bars.map((bar) => Number(bar.close)),
    low = Math.min(...prices),
    high = Math.max(...prices)
  const chartY = (i: number) =>
    180 - ((prices[i] - low) / (high - low || 1)) * 160
  const chartX = (i: number) => 20 + (i / (bars.length - 1)) * 960
  let preview: Pattern | null = null,
    problem = ''
  if (history) {
    try {
      preview = importSection(
        history.symbol,
        history.interval,
        bars,
        first,
        last,
      )
    } catch (error) {
      problem = (error as Error).message
    }
  }
  return (
    <section className="panel import-panel" aria-labelledby="import-title">
      <details open={open || undefined}>
        <summary id="import-title">Import a stock-chart section</summary>
        <p className="helper">
          Load history only when needed. Dates cover whole UTC days. Select
          observations on the chart, review the range, then import its
          closing-price shape.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void load()
          }}
        >
          <div className="history-fields">
            <label>
              Reference symbol
              <input
                value={symbol}
                placeholder="e.g. AAPL"
                maxLength={15}
                onChange={(e) => {
                  invalidate()
                  setSymbol(e.target.value)
                }}
              />
            </label>
            <label>
              History timeframe
              <select
                value={interval}
                onChange={(e) => {
                  invalidate()
                  setInterval(e.target.value as MarketInterval)
                }}
              >
                {intervals.map((i) => (
                  <option value={i.value} key={i.value}>
                    {i.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              History start
              <input
                type="date"
                value={start}
                onChange={(e) => {
                  invalidate()
                  setStart(e.target.value)
                }}
              />
            </label>
            <label>
              History end
              <input
                type="date"
                value={end}
                onChange={(e) => {
                  invalidate()
                  setEnd(e.target.value)
                }}
              />
            </label>
          </div>
          <button disabled={loading} type="submit">
            {loading ? 'Loading history…' : 'Load stock history'}
          </button>
          {loading && (
            <button type="button" onClick={invalidate}>
              Cancel history load
            </button>
          )}
        </form>
        {history && (
          <>
            <h3>{history.symbol} closing prices</h3>
            <p className="helper">
              Source timestamps are shown as supplied ({history.timezone}).
              Click a start, then an end. No data requests occur during
              selection.
            </p>
            <svg
              className="history-chart"
              viewBox="0 0 1000 200"
              role="img"
              aria-label={`${history.symbol} closing price selection chart`}
            >
              {last > first && (
                <rect
                  className="selection-range"
                  x={chartX(first)}
                  width={chartX(last) - chartX(first)}
                  y="0"
                  height="200"
                />
              )}
              <polyline
                className="pattern-line"
                points={bars
                  .map((_, i) => `${chartX(i)},${chartY(i)}`)
                  .join(' ')}
              />
              {bars.map((bar, i) => (
                <rect
                  key={bar.timestamp}
                  x={Math.max(0, chartX(i) - 480 / bars.length)}
                  y="0"
                  width={960 / bars.length + 1}
                  height="200"
                  fill="transparent"
                  onClick={() => {
                    if (selectingEnd) {
                      setLast(i)
                      setSelectingEnd(false)
                    } else {
                      setFirst(i)
                      setLast(i)
                      setSelectingEnd(true)
                    }
                  }}
                >
                  <title>
                    {bar.timestamp}, close {bar.close}
                  </title>
                </rect>
              ))}
            </svg>
            <div className="range-controls">
              <label>
                Start observation
                <select
                  value={first}
                  onChange={(e) => {
                    setFirst(Number(e.target.value))
                    setSelectingEnd(false)
                  }}
                >
                  {bars.map((b, i) => (
                    <option key={b.timestamp} value={i}>
                      {b.timestamp} / {b.close}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                End observation
                <select
                  value={last}
                  onChange={(e) => {
                    setLast(Number(e.target.value))
                    setSelectingEnd(false)
                  }}
                >
                  {bars.map((b, i) => (
                    <option key={b.timestamp} value={i}>
                      {b.timestamp} / {b.close}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {preview ? (
              <p>
                {last - first + 1} observations from {bars[first].timestamp} to{' '}
                {bars[last].timestamp}. {preview.points.length} editable points
                {last - first + 1 > 256
                  ? ' after shape-preserving simplification; review the editor geometry'
                  : ''}
                . Height = log(close / first close); x is equally spaced by
                observation.
              </p>
            ) : (
              <p role="alert">{problem}</p>
            )}
            <button
              type="button"
              disabled={!preview}
              onClick={() => {
                if (preview) {
                  onImport(preview)
                  setMessage(
                    'Section imported. Review and edit the shape above before scanning.',
                  )
                }
              }}
            >
              Import selected section
            </button>
          </>
        )}
        <p role="status">{message}</p>
      </details>
    </section>
  )
}
