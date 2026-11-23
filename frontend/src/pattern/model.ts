import type { MarketInterval, PriceBar } from '../api/marketData'

export type Point = { x: number; y: number }
export type Provenance = {
  kind: 'drawing' | 'stock_section' | 'text' | 'screenshot_trace'
  source_symbol: string | null
  source_interval: MarketInterval | null
  source_start_at: string | null
  source_end_at: string | null
  grammar_version: 1 | null
}
export type Pattern = {
  schema_version: 1
  points: Point[]
  provenance: Provenance
}
export const drawingProvenance = (): Provenance => ({
  kind: 'drawing',
  source_symbol: null,
  source_interval: null,
  source_start_at: null,
  source_end_at: null,
  grammar_version: null,
})
export const emptyPattern = (): Pattern => ({
  schema_version: 1,
  points: [],
  provenance: drawingProvenance(),
})

export function patternProblem(points: Point[]): string | null {
  if (points.length < 2)
    return 'Draw a pattern or import at least two observations.'
  if (points.length > 256) return 'Use at most 256 control points.'
  if (
    points.some(
      (p) =>
        !Number.isFinite(p.x) ||
        !Number.isFinite(p.y) ||
        p.x < 0 ||
        p.x > 1 ||
        Math.abs(p.y) > 1000,
    )
  )
    return 'Coordinates must be finite, with x from 0 to 1 and y from -1000 to 1000.'
  if (points[0].x !== 0 || points.at(-1)!.x !== 1)
    return 'Endpoint x positions must be 0 and 1.'
  if (points.some((p, i) => i > 0 && p.x <= points[i - 1].x))
    return 'Point x positions must strictly increase.'
  if (points.every((p) => p.y === points[0].y))
    return 'A flat pattern has no measurable shape. Adjust its height.'
  return null
}

export function yDomain(points: Point[]): [number, number] {
  if (!points.length) return [-1, 1]
  const low = Math.min(...points.map((p) => p.y)),
    high = Math.max(...points.map((p) => p.y))
  const padding = (high - low || 1) * 0.15
  return [low - padding, high + padding]
}
export function screenPoint(
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
  domain: [number, number],
): Point {
  if (rect.width <= 0 || rect.height <= 0)
    throw new Error('The editor has no drawing area.')
  const x = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
  const height = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height))
  return {
    x,
    y: Math.max(
      -1000,
      Math.min(1000, domain[1] - height * (domain[1] - domain[0])),
    ),
  }
}

// Retain the largest vertical deviation from each line segment first. Endpoints
// and important turns survive; the visible result is the submitted geometry.
export function simplify(points: Point[], limit = 256): Point[] {
  if (points.length <= limit) return points
  const retained = new Set([0, points.length - 1])
  const segment = (start: number, end: number) => {
    let index = -1,
      error = -1
    for (let i = start + 1; i < end; i++) {
      const a = points[start],
        b = points[end],
        p = points[i]
      const deviation = Math.abs(
        p.y - (a.y + ((b.y - a.y) * (p.x - a.x)) / (b.x - a.x)),
      )
      if (deviation > error) {
        error = deviation
        index = i
      }
    }
    return { start, end, index, error }
  }
  const segments = [segment(0, points.length - 1)]
  while (retained.size < limit && segments.length) {
    segments.sort((a, b) => b.error - a.error || a.index - b.index)
    const next = segments.shift()!
    if (next.index < 0) break
    retained.add(next.index)
    segments.push(
      segment(next.start, next.index),
      segment(next.index, next.end),
    )
  }
  return [...retained].sort((a, b) => a - b).map((i) => points[i])
}
export function finishStroke(raw: Point[]): Point[] {
  if (raw.length < 2 || raw.at(-1)!.x === raw[0].x)
    throw new Error('Stroke discarded: draw across a horizontal range.')
  if (raw.some((p, i) => i > 0 && p.x < raw[i - 1].x))
    throw new Error(
      'Stroke discarded: draw from left to right without moving backwards.',
    )
  // At one x position, the most recent height replaces the preceding sample.
  const unique: Point[] = []
  for (const point of raw) {
    if (unique.at(-1)?.x === point.x) unique[unique.length - 1] = point
    else unique.push(point)
  }
  const first = unique[0].x,
    width = unique.at(-1)!.x - first
  if (!width)
    throw new Error('Stroke discarded: draw across a horizontal range.')
  return simplify(unique.map((p) => ({ x: (p.x - first) / width, y: p.y })))
}

// Pydantic UTC datetimes omit a zero fraction and use six fractional digits otherwise.
export function canonicalInstant(value: string): string {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/.exec(
      value,
    )
  if (!match) throw new Error('Source timestamps must include a UTC offset.')
  const whole = new Date(match[1] + match[3]).toISOString().replace('.000Z', '')
  // Date alone would truncate provider microseconds to milliseconds. Preserve
  // the six digits accepted and serialized by the backend datetime model.
  const fraction = (match[2] ?? '').slice(0, 6).padEnd(6, '0')
  return whole + (fraction === '000000' ? '' : '.' + fraction) + 'Z'
}
export function importSection(
  symbol: string,
  interval: MarketInterval,
  bars: PriceBar[],
  start: number,
  end: number,
): Pattern {
  const section = bars.slice(start, end + 1)
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end >= bars.length ||
    end <= start
  )
    throw new Error('Select at least two ordered observations.')
  const prices = section.map((bar) => Number(bar.close))
  if (prices.some((price) => !Number.isFinite(price) || price <= 0))
    throw new Error('This range contains invalid closing prices.')
  const times = section.map((bar) => Date.parse(bar.timestamp))
  if (
    times.some(
      (time, i) => !Number.isFinite(time) || (i > 0 && time <= times[i - 1]),
    )
  )
    throw new Error('Observation timestamps must be valid and increasing.')
  const points = simplify(
    prices.map((price, i) => ({
      x: i / (prices.length - 1),
      y: Math.log(price / prices[0]),
    })),
  )
  const problem = patternProblem(points)
  if (problem) throw new Error(problem)
  return {
    schema_version: 1,
    points,
    provenance: {
      kind: 'stock_section',
      source_symbol: symbol,
      source_interval: interval,
      source_start_at: canonicalInstant(section[0].timestamp),
      source_end_at: canonicalInstant(section.at(-1)!.timestamp),
      grammar_version: null,
    },
  }
}

function numberHex(value: number): string {
  const buffer = new ArrayBuffer(8)
  new DataView(buffer).setFloat64(0, value === 0 ? 0 : value, false)
  return [...new Uint8Array(buffer)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}
// Objects below are explicitly key-sorted, including every nullable provenance field.
export function canonicalPatternJson(pattern: Pattern): string {
  const p = pattern.provenance
  return JSON.stringify({
    points: pattern.points.map((point) => ({
      x: numberHex(point.x),
      y: numberHex(point.y),
    })),
    provenance: {
      grammar_version: p.grammar_version,
      kind: p.kind,
      source_end_at: p.source_end_at,
      source_interval: p.source_interval,
      source_start_at: p.source_start_at,
      source_symbol: p.source_symbol,
    },
    schema_version: pattern.schema_version,
  }).replace(
    /[\u007f-\uffff]/g,
    (char) => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0'),
  )
}
export async function patternHash(pattern: Pattern): Promise<string> {
  const hash = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(canonicalPatternJson(pattern)),
  )
  return [...new Uint8Array(hash)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
}
