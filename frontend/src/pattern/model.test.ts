/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { afterEach, expect, it, vi } from 'vitest'
import {
  canonicalInstant,
  canonicalPatternJson,
  drawingProvenance,
  finishStroke,
  importSection,
  patternHash,
  patternProblem,
  screenPoint,
  simplify,
} from './model'
import { defaultDraft, parseSettings } from './settings'
import { parseDescription } from './textPattern'
import type { PriceBar } from '../api/marketData'
afterEach(() => vi.unstubAllGlobals())
it.each([
  ['text', 'd9c872f9a5a51040504c59faa82d3b994d2f0b2369590b141e5fb49f5de550ff'],
  [
    'screenshot_trace',
    'fc7fdd7e82e47a579f4f4affb31bc76734a8d71c2ad9493d5793caa9e0d1d4e7',
  ],
] as const)(
  'matches the actual Python model hash for %s provenance',
  async (kind, hash) => {
    vi.stubGlobal('crypto', webcrypto)
    const pattern = parseDescription('up 3, down 1, up 2')
    if (kind === 'screenshot_trace')
      pattern.provenance = { ...drawingProvenance(), kind }
    expect(await patternHash(pattern)).toBe(hash)
  },
)
it('matches the backend binary64 hash vector, including negative zero', async () => {
  vi.stubGlobal('crypto', webcrypto)
  const pattern = {
    schema_version: 1 as const,
    points: [
      { x: 0, y: 0 },
      { x: 0.5, y: -1 },
      { x: 1, y: 1 },
    ],
    provenance: drawingProvenance(),
  }
  expect(await patternHash(pattern)).toBe(
    '92f8edef3516bcb32ffd9f479d05efa98000a56323dee44d3a2b0abc87089311',
  )
  expect(
    await patternHash({
      ...pattern,
      points: [{ x: -0, y: -0 }, ...pattern.points.slice(1)],
    }),
  ).toBe(await patternHash(pattern))
  expect(
    canonicalPatternJson({
      ...pattern,
      provenance: { ...drawingProvenance(), source_symbol: 'é😀' },
    }),
  ).toContain('\\u00e9\\ud83d\\ude00')
  // Generated offline by the actual Python CanonicalPattern implementation.
  expect(
    await patternHash({
      ...pattern,
      provenance: {
        kind: 'stock_section',
        source_symbol: 'AAPL',
        source_interval: '1day',
        source_start_at: '2025-01-02T14:30:00Z',
        source_end_at: '2025-01-04T14:30:00.123000Z',
        grammar_version: null,
      },
    }),
  ).toBe('899645062cfec5248cc288f35a88cfd804111eb67979a4741f88da5a6feac7cc')
})
it('converts screen coordinates upward and rejects missing area', () => {
  expect(
    screenPoint(
      150,
      75,
      { left: 100, top: 50, width: 200, height: 100 },
      [-1, 1],
    ),
  ).toEqual({ x: 0.25, y: 0.5 })
  expect(
    screenPoint(-1, 999, { left: 0, top: 0, width: 100, height: 100 }, [-1, 1]),
  ).toEqual({ x: 0, y: -1 })
  expect(() =>
    screenPoint(0, 0, { left: 0, top: 0, width: 0, height: 0 }, [-1, 1]),
  ).toThrow('no drawing area')
})
it('normalizes stroke endpoints, resolves duplicates, and rejects backwards and zero width', () => {
  expect(
    finishStroke([
      { x: 0.2, y: 0 },
      { x: 0.2, y: 0.2 },
      { x: 0.5, y: 1 },
      { x: 0.8, y: -0.2 },
    ]),
  ).toEqual([
    { x: 0, y: 0.2 },
    { x: (0.5 - 0.2) / (0.8 - 0.2), y: 1 },
    { x: 1, y: -0.2 },
  ])
  expect(() =>
    finishStroke([
      { x: 0.2, y: 0 },
      { x: 0.1, y: 1 },
    ]),
  ).toThrow('backwards')
  expect(() =>
    finishStroke([
      { x: 0.2, y: 0 },
      { x: 0.2, y: 1 },
    ]),
  ).toThrow('horizontal range')
  expect(
    patternProblem([
      { x: 0, y: 1 },
      { x: 1, y: 1 },
    ]),
  ).toContain('flat')
  expect(
    patternProblem([
      { x: 0, y: 0 },
      { x: 0, y: 1 },
      { x: 1, y: 2 },
    ]),
  ).toContain('strictly')
  expect(
    patternProblem([
      { x: 0, y: NaN },
      { x: 1, y: 2 },
    ]),
  ).toContain('finite')
})
it('keeps endpoints and major turns in dense input', () => {
  const points = Array.from({ length: 1001 }, (_, i) => ({
    x: i / 1000,
    y: i <= 500 ? i / 500 : (1000 - i) / 500,
  }))
  const reduced = simplify(points)
  expect(reduced).toHaveLength(256)
  expect(reduced).toContainEqual(points[500])
  expect(reduced[0]).toEqual(points[0])
  expect(reduced.at(-1)).toEqual(points.at(-1))
  expect(patternProblem(reduced)).toBeNull()
})
it('imports actual closes with observation spacing, log relative height and exact provenance', () => {
  const bars = [100, 120, 90].map((p, i) => ({
    timestamp: `2025-01-0${i + 2}T09:30:00-05:00`,
    close: String(p),
  })) as PriceBar[]
  const pattern = importSection('AAPL', '1day', bars, 0, 2)
  expect(pattern.points).toEqual([
    { x: 0, y: 0 },
    { x: 0.5, y: Math.log(1.2) },
    { x: 1, y: Math.log(0.9) },
  ])
  expect(pattern.provenance).toEqual({
    kind: 'stock_section',
    source_symbol: 'AAPL',
    source_interval: '1day',
    source_start_at: '2025-01-02T14:30:00Z',
    source_end_at: '2025-01-04T14:30:00Z',
    grammar_version: null,
  })
  expect(canonicalInstant('2025-01-02T14:30:00.123Z')).toBe(
    '2025-01-02T14:30:00.123000Z',
  )
  expect(canonicalInstant('2025-01-02T09:30:00.123456-05:00')).toBe(
    '2025-01-02T14:30:00.123456Z',
  )
  expect(() => canonicalInstant('2025-01-02T14:30:00')).toThrow('UTC offset')
  expect(() => importSection('AAPL', '1day', bars, 2, 0)).toThrow('ordered')
  expect(() =>
    importSection('AAPL', '1day', [{ ...bars[0], close: '0' }, bars[1]], 0, 1),
  ).toThrow('invalid closing')
})
it('maps workspace defaults and explicit zero thresholds without fallback coercion', () => {
  expect(parseSettings(defaultDraft())).toEqual({
    problems: [],
    duration: { minError: null, maxError: null, rangeError: null },
    universe: { kind: 'nasdaq', symbols: [] },
    settings: {
      interval: '1day',
      duration: { minimum_candles: 20, maximum_candles: 20 },
      minimum_completion: 0.5,
      minimum_movement_percent: 5,
      minimum_similarity: 80,
      match_type: 'all',
      session: 'regular',
      include_unfinished: false,
      top_n: 20,
    },
  })
  const parsed = parseSettings({
    ...defaultDraft(),
    similarity: '0',
    movement: '0',
    universe: 'custom',
    symbols: 'aapl, msft AAPL',
  })
  expect(parsed.problems).toEqual([])
  expect(parsed.settings.minimum_similarity).toBe(0)
  expect(parsed.settings.minimum_movement_percent).toBe(0)
  expect(parsed.universe.symbols).toEqual(['AAPL', 'MSFT'])
  for (const change of [
    { completion: '0' },
    { minimum: '7' },
    { maximum: '241' },
    { minimum: '121' },
    { similarity: '' },
    { movement: 'Infinity' },
    { match: 'forming' as const, completion: '100' },
    { universe: 'custom' as const, symbols: 'BRK/B' },
    { universe: 'custom' as const, symbols: 'A B C D E F G H I J K' },
  ]) {
    const invalid = parseSettings({ ...defaultDraft(), ...change })
    expect(
      invalid.problems.length > 0 ||
        Object.values(invalid.duration).some(Boolean),
    ).toBe(true)
  }
})
