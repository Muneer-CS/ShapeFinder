import type { Pattern, Point } from './model'
import type { Match } from '../api/patternScans'

export function sessionDate(timestamp: string, timezone: string): string {
  const date = new Date(timestamp)
  if (!Number.isFinite(date.getTime())) return ''
  try {
    return new Intl.DateTimeFormat('sv-SE', { timeZone: timezone }).format(date)
  } catch {
    return date.toISOString().slice(0, 10)
  }
}

export function matchDates(match: Match) {
  const { expected } = match.freshness
  return {
    start:
      match.matched_start_session ??
      sessionDate(match.start_at, expected.timezone),
    end:
      match.last_candle_session ??
      expected.session_date ??
      sessionDate(expected.open_at, expected.timezone),
  }
}

export function datedCandles(match: Match) {
  const observations = match.comparison?.observations ?? []
  const candles = observations.flatMap((bar) => {
    const { open, high, low, close, volume } = bar
    const values = [open, high, low, close, volume]
    if (values.some((value) => typeof value !== 'string' || !value.trim()))
      return []
    const [o, h, l, c, v] = values.map(Number)
    const day = sessionDate(bar.timestamp, match.freshness.expected.timezone)
    if (
      !day ||
      ![o, h, l, c, v].every(Number.isFinite) ||
      l <= 0 ||
      l > Math.min(o, c) ||
      h < Math.max(o, c) ||
      v < 0
    )
      return []
    return [{ bar, day, o, h, l, c }]
  })
  return candles.length > 0 && candles.length === observations.length
    ? candles
    : null
}

export type Alignment = {
  reference: Point[]
  observed: Point[]
  continuation: Point[]
  completion: number
  scale: number
}
function interpolate(points: Point[], x: number): number {
  if (x >= points.at(-1)!.x) return points.at(-1)!.y
  const at = points.findIndex((point) => point.x >= x)
  if (at <= 0) return points[0].y
  const a = points[at - 1],
    b = points[at]
  return a.y + ((b.y - a.y) * (x - a.x)) / (b.x - a.x)
}
function prepare(points: Point[], end: number) {
  const first = points[0].y,
    span =
      Math.max(...points.map((p) => p.y)) - Math.min(...points.map((p) => p.y))
  if (!(span > 0)) return null
  const relative = points.map((p) => ({ x: p.x, y: (p.y - first) / span }))
  const samples = Array.from({ length: 64 }, (_, i) =>
    interpolate(relative, (end * i) / 63),
  )
  const mean = samples.reduce((sum, value) => sum + value, 0) / 64
  const rms = Math.sqrt(
    samples.reduce((sum, value) => sum + (value - mean) ** 2, 0) / 64,
  )
  if (!Number.isFinite(rms) || rms <= 1e-12) return null
  return {
    samples: samples.map((y) => (y - mean) / rms),
    transform: (y: number) => ((y - first) / span - mean) / rms,
  }
}
export function comparisonAlignment(
  pattern: Pattern,
  match: Match,
): Alignment | null {
  const data = match.comparison
  if (!data) return null
  const completion =
    (match.observed_candles - 1) / (match.complete_duration_candles - 1)
  const prefix = pattern.points.filter((p) => p.x < completion)
  prefix.push({ x: completion, y: interpolate(pattern.points, completion) })
  const reference = prepare(prefix, completion)
  const stock = prepare(
    data.log_relative_closes.map((y, i) => ({
      x: i / (match.observed_candles - 1),
      y,
    })),
    1,
  )
  if (!reference || !stock) return null
  const denominator = stock.samples.reduce((sum, y) => sum + y * y, 0)
  const scale = Math.max(
    0,
    reference.samples.reduce((sum, y, i) => sum + y * stock.samples[i], 0) /
      denominator,
  )
  const continuation =
    completion < 1
      ? [
          { x: completion, y: reference.samples.at(-1)! },
          ...pattern.points
            .filter((p) => p.x > completion)
            .map((p) => ({ x: p.x, y: reference.transform(p.y) })),
        ]
      : []
  return {
    completion,
    scale,
    reference: reference.samples.map((y, i) => ({
      x: (completion * i) / 63,
      y,
    })),
    observed: stock.samples.map((y, i) => ({
      x: (completion * i) / 63,
      y: scale * y,
    })),
    continuation,
  }
}

// Linear quoted-price positions derived from the server's accurate log ratios.
// expm1 retains tiny ranges, and this form avoids overflowing exp(large prices).
export function observedPricePositions(match: Match): Point[] {
  const data = match.comparison
  if (!data) return []
  const low = Math.min(...data.log_relative_closes),
    high = Math.max(...data.log_relative_closes)
  const start = Date.parse(data.observations[0].timestamp),
    end = Date.parse(data.observations.at(-1)!.timestamp)
  const denominator = -Math.expm1(low - high)
  if (!(denominator > 0) || !(end > start)) return []
  return data.observations.map((bar, i) => ({
    x: (Date.parse(bar.timestamp) - start) / (end - start),
    y: Math.max(
      0,
      Math.min(
        1,
        (Math.exp(data.log_relative_closes[i] - high) *
          -Math.expm1(low - data.log_relative_closes[i])) /
          denominator,
      ),
    ),
  }))
}
