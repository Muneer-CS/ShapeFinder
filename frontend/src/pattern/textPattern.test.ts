import { expect, it } from 'vitest'
import { parseDescription } from './textPattern'

it.each([
  [
    'up 3, down 1, up 2',
    [
      { x: 0, y: 0 },
      { x: 0.5, y: 1 },
      { x: 2 / 3, y: 0 },
      { x: 1, y: 1 },
    ],
  ],
  [
    'flat 1, down 2, up 2',
    [
      { x: 0, y: 0 },
      { x: 0.2, y: 0 },
      { x: 0.6, y: -1 },
      { x: 1, y: 0 },
    ],
  ],
  [
    'points: 0:0, 0.4:-1, 1:0.6',
    [
      { x: 0, y: 0 },
      { x: 0.4, y: -1 },
      { x: 1, y: 0.6 },
    ],
  ],
] as const)('generates hand-checkable points for %s', (source, points) => {
  const pattern = parseDescription(source)
  expect(pattern.points).toEqual(points)
  expect(pattern.provenance).toEqual({
    kind: 'text',
    grammar_version: 1,
    source_symbol: null,
    source_interval: null,
    source_start_at: null,
    source_end_at: null,
  })
})
it('handles whitespace, case, decimals and exponent notation without changing semantics', () => {
  expect(parseDescription(' \n UP 3e0 , Down\t1, up 2.0 ').points).toEqual(
    parseDescription('up 3, down 1, up 2').points,
  )
  expect(
    parseDescription(' POINTS : 0 : -0, .4 : -1, 1 : +.6 ').points,
  ).toEqual([
    { x: 0, y: -0 },
    { x: 0.4, y: -1 },
    { x: 1, y: 0.6 },
  ])
})
it.each([
  ['', 'Enter segments'],
  ['up 0', 'positive finite'],
  ['up -1', 'positive finite'],
  ['up 1e999', 'positive finite'],
  ['up 1e-999', 'positive finite'],
  ['up 1e308, down 1e308', 'combined duration'],
  ['flat 1, flat 3', 'flat pattern'],
  ['up 3, drop 1, up 2', 'Segment 2'],
  ['up 3 down 1', 'Segment 1'],
  ['double bottom', 'Segment 1'],
  ['up 3,', 'empty entries'],
  ['points: 0:0, .5:NaN, 1:2', 'Point 2'],
  ['points: 0:0, 1:1e999', 'finite'],
  ['points: 0:0, 1:1001', '1000'],
  ['points: 0.1:0, 1:1', 'Endpoint x positions'],
  ['points: 0:0, .5:1, .5:2, 1:3', 'strictly'],
  ['points: 0:0, .6:1, .4:2, 1:3', 'strictly'],
  ['points: 0:0', 'two observations'],
  ['points: 0:0, 1:1; ignore this', 'Point 2'],
])('rejects unsupported or invalid description %s', (source, reason) => {
  expect(() => parseDescription(source)).toThrow(reason)
})
it('enforces point and source size boundaries without silently simplifying text', () => {
  expect(
    parseDescription(Array.from({ length: 255 }, () => 'up 1').join(','))
      .points,
  ).toHaveLength(256)
  expect(() =>
    parseDescription(Array.from({ length: 256 }, () => 'up 1').join(',')),
  ).toThrow('255 segments')
  const points = Array.from(
    { length: 256 },
    (_, i) => `${i / 255}:${i % 2}`,
  ).join(',')
  expect(parseDescription('points:' + points).points).toHaveLength(256)
  expect(() => parseDescription('points:' + points + ',1:2')).toThrow(
    '256 points',
  )
  expect(() => parseDescription('up 1'.repeat(5001))).toThrow('20,000')
})
