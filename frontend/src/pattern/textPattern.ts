import {
  drawingProvenance,
  patternProblem,
  type Pattern,
  type Point,
} from './model'

const numeric = '[+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:e[+-]?\\d+)?'
const segment = new RegExp(`^(up|down|flat)\\s+(${numeric})$`, 'i')
const coordinate = new RegExp(`^(${numeric})\\s*:\\s*(${numeric})$`, 'i')
export function parseDescription(source: string): Pattern {
  const text = source.trim()
  if (!text)
    throw new Error(
      'Enter segments such as up 3, down 1, up 2, or an explicit points: list.',
    )
  if (text.length > 20000)
    throw new Error('Keep the description within 20,000 characters.')
  const explicit = /^points\s*:/i.exec(text)
  const fragments = (explicit ? text.slice(explicit[0].length) : text)
    .split(',')
    .map((s) => s.trim())
  if (fragments.some((s) => !s))
    throw new Error(
      'Every comma must separate a complete segment or point. Remove empty entries.',
    )
  if (fragments.length > (explicit ? 256 : 255))
    throw new Error(
      'Use at most 256 points, or 255 segments plus the starting point.',
    )
  let points: Point[]
  if (explicit) {
    points = fragments.map((fragment, i) => {
      const match = coordinate.exec(fragment)
      if (!match)
        throw new Error(
          `Point ${i + 1} must use x:y coordinates, for example 0.4:-1. Unrecognized entry: ${fragment.slice(0, 80)}`,
        )
      return { x: Number(match[1]), y: Number(match[2]) }
    })
  } else {
    const segments = fragments.map((fragment, i) => {
      const match = segment.exec(fragment)
      if (!match)
        throw new Error(
          `Segment ${i + 1} must be up, down, or flat followed by a positive duration. Unrecognized entry: ${fragment.slice(0, 80)}`,
        )
      const duration = Number(match[2])
      if (!Number.isFinite(duration) || duration <= 0)
        throw new Error(
          `Segment ${i + 1} needs a positive finite relative duration.`,
        )
      return { direction: match[1].toLowerCase(), duration }
    })
    const total = segments.reduce((sum, s) => sum + s.duration, 0)
    if (!Number.isFinite(total))
      throw new Error(
        'The combined duration must be finite. Use smaller relative durations.',
      )
    points = [{ x: 0, y: 0 }]
    let elapsed = 0,
      y = 0
    for (const [i, s] of segments.entries()) {
      elapsed += s.duration
      y += s.direction === 'up' ? 1 : s.direction === 'down' ? -1 : 0
      points.push({ x: i === segments.length - 1 ? 1 : elapsed / total, y })
    }
  }
  const problem = patternProblem(points)
  if (problem) throw new Error(problem)
  return {
    schema_version: 1,
    points,
    provenance: { ...drawingProvenance(), kind: 'text', grammar_version: 1 },
  }
}
