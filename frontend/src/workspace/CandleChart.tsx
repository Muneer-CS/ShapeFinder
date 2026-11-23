import { useEffect, useRef, useState } from 'react'
import type { datedCandles } from '../pattern/comparison'

type Candles = NonNullable<ReturnType<typeof datedCandles>>

export function CandleChart({
  candles,
  symbol,
}: {
  candles: Candles
  symbol: string
}) {
  const container = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(360)
  const [view, setView] = useState({ start: 0, count: candles.length })
  const [inspected, setInspected] = useState<number | null>(null)
  useEffect(() => {
    const element = container.current
    if (!element) return
    const measure = () =>
      setWidth(Math.max(250, Math.round(element.clientWidth || 360)))
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const total = candles.length
  const left = 62,
    right = width - 14,
    top = 18,
    bottom = 226
  const visible = candles.slice(view.start, view.start + view.count)
  const low = Math.min(...visible.map((bar) => bar.l))
  const high = Math.max(...visible.map((bar) => bar.h))
  const padding = Math.max(Number.MIN_VALUE, (high - low || low * 0.01) * 0.1)
  const end = view.start + view.count - 1
  const active =
    inspected !== null && inspected >= view.start && inspected <= end
      ? inspected
      : end
  const detail = candles[active]
  const slot = (right - left) / view.count
  const x = (i: number) => left + (i + 0.5) * slot
  const y = (price: number) =>
    bottom -
    ((price - low + padding) / (high - low + 2 * padding)) * (bottom - top)
  const ticks = width < 450 ? 3 : 5
  const tickPositions = Array.from({ length: ticks }, (_, i) =>
    Math.round((i * (view.count - 1)) / (ticks - 1)),
  )
  const priceFormat = new Intl.NumberFormat('en-US', {
    maximumSignificantDigits: 7,
  })
  function setWindow(start: number, count: number) {
    setView({ start: Math.max(0, Math.min(total - count, start)), count })
  }
  function zoom(factor: number) {
    const count = Math.min(total, Math.max(5, Math.round(view.count * factor)))
    setWindow(Math.round(view.start + (view.count - count) / 2), count)
  }
  function pan(direction: number) {
    setWindow(
      view.start + direction * Math.max(1, Math.floor(view.count / 2)),
      view.count,
    )
  }
  return (
    <figure className="candle-figure">
      <div
        className="candle-controls"
        aria-label="Price chart controls"
        role="group"
      >
        <button
          type="button"
          onClick={() => zoom(0.65)}
          disabled={view.count <= 5}
        >
          Zoom in
        </button>
        <button
          type="button"
          onClick={() => zoom(1.6)}
          disabled={view.count === total}
        >
          Zoom out
        </button>
        <button
          type="button"
          onClick={() => pan(-1)}
          disabled={view.start === 0}
        >
          Pan earlier
        </button>
        <button
          type="button"
          onClick={() => pan(1)}
          disabled={end === total - 1}
        >
          Pan later
        </button>
        <button
          type="button"
          onClick={() => {
            setWindow(0, total)
            setInspected(null)
          }}
          disabled={view.count === total}
        >
          Reset view
        </button>
      </div>
      <p className="price-window" role="status">
        Showing {view.count} of {total} matched candles · {visible[0].day} to{' '}
        {visible.at(-1)!.day}
      </p>
      <div className="candle-chart-frame" ref={container}>
        <svg
          className="candle-chart"
          viewBox={`0 0 ${width} 260`}
          role="img"
          aria-label={`${symbol} observed candlesticks. Use the left and right arrow keys to inspect candles.`}
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
            event.preventDefault()
            setInspected(
              Math.max(
                view.start,
                Math.min(end, active + (event.key === 'ArrowRight' ? 1 : -1)),
              ),
            )
          }}
        >
          <rect
            className="matched-price-band"
            x={left}
            y={top}
            width={right - left}
            height={bottom - top}
          />
          {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
            const py = top + tick * (bottom - top)
            return (
              <g key={tick}>
                <line
                  className="chart-grid"
                  x1={left}
                  x2={right}
                  y1={py}
                  y2={py}
                />
                <text
                  className="chart-tick"
                  x={left - 8}
                  y={py + 4}
                  textAnchor="end"
                >
                  {priceFormat.format(
                    high + padding - tick * (high - low + 2 * padding),
                  )}
                </text>
              </g>
            )
          })}
          {visible.map((candle, i) => {
            const px = x(i)
            const bodyWidth = Math.max(1, Math.min(15, slot * 0.58))
            const selected = view.start + i === active
            return (
              <g
                key={candle.bar.timestamp}
                className={candle.c >= candle.o ? 'candle-up' : 'candle-down'}
                onPointerEnter={() => setInspected(view.start + i)}
                onClick={() => setInspected(view.start + i)}
              >
                {selected && (
                  <rect
                    className="candle-highlight"
                    x={px - slot / 2}
                    y={top}
                    width={slot}
                    height={bottom - top}
                  />
                )}
                <line
                  className="candle-wick"
                  x1={px}
                  x2={px}
                  y1={y(candle.h)}
                  y2={y(candle.l)}
                />
                <rect
                  className="candle-body"
                  x={px - bodyWidth / 2}
                  y={Math.min(y(candle.o), y(candle.c))}
                  width={bodyWidth}
                  height={Math.max(1.5, Math.abs(y(candle.o) - y(candle.c)))}
                />
                <rect
                  className="candle-hit"
                  x={px - slot / 2}
                  y={top}
                  width={slot}
                  height={bottom - top}
                />
              </g>
            )
          })}
          {tickPositions.map((i, tick) => (
            <text
              key={i}
              className="chart-tick"
              x={x(i)}
              y={249}
              textAnchor={
                tick === 0 ? 'start' : tick === ticks - 1 ? 'end' : 'middle'
              }
            >
              {width < 450 ? visible[i].day.slice(5) : visible[i].day}
            </text>
          ))}
        </svg>
      </div>
      <figcaption>
        Shaded area: matched period. Prices are in supplied quote units.
      </figcaption>
      <div
        className="candle-details"
        aria-live="polite"
        aria-label="Selected candle details"
      >
        <time dateTime={detail.bar.timestamp}>{detail.day}</time>
        <dl>
          {(['open', 'high', 'low', 'close', 'volume'] as const).map((key) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{detail.bar[key]}</dd>
            </div>
          ))}
        </dl>
      </div>
    </figure>
  )
}
