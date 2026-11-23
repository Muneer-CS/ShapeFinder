import { expect, it } from 'vitest'
import { comparisonAlignment, observedPricePositions } from './comparison'
import { comparisonMatch, comparisonPattern } from '../test/comparisonFixture'
it('uses complete-pattern progress and stops a partial observation at its exact endpoint', () => {
  const pattern = comparisonPattern(),
    match = comparisonMatch(),
    aligned = comparisonAlignment(pattern, match)!
  expect(aligned.completion).toBe(0.5)
  expect(aligned.reference).toHaveLength(64)
  expect(aligned.observed).toHaveLength(64)
  expect(aligned.observed[0].x).toBe(0)
  expect(aligned.observed.at(-1)!.x).toBe(0.5)
  expect(aligned.continuation[0].x).toBe(0.5)
  expect(aligned.continuation.at(-1)!.x).toBe(1)
  expect(aligned.scale).toBeCloseTo(1, 12)
  // Python's _prepare for a linear path sampled at 64 positions: unit-RMS endpoints.
  expect(aligned.reference[0].y).toBeCloseTo(-1.7051956801764152, 12)
  expect(aligned.observed.at(-1)!.y).toBeCloseTo(1.7051956801764152, 12)
})
it('does not use future extrema or mirror the observed shape to improve fit', () => {
  const pattern = comparisonPattern(),
    match = comparisonMatch()
  const original = comparisonAlignment(pattern, match)!
  const changed = comparisonAlignment(
    { ...pattern, points: [...pattern.points.slice(0, 2), { x: 1, y: 1000 }] },
    match,
  )!
  expect(changed.reference).toEqual(original.reference)
  expect(changed.observed).toEqual(original.observed)
  expect(changed.scale).toBe(original.scale)
  const reverse = {
    ...match,
    comparison: {
      ...match.comparison!,
      log_relative_closes: match.comparison!.log_relative_closes.map((y) => -y),
    },
  }
  expect(comparisonAlignment(pattern, reverse)!.scale).toBe(0)
})
it('agrees with Python preparation and nonnegative fit for a non-linear clipped prefix', () => {
  const pattern = {
    ...comparisonPattern(),
    points: [
      { x: 0, y: 2 },
      { x: 0.4, y: 6 },
      { x: 1, y: 0 },
    ],
  }
  const logs = [0, 0.03, 0.02, 0.05, 0.04, 0.08, 0.07, 0.09],
    match = comparisonMatch()
  match.observed_candles = 8
  match.complete_duration_candles = 15
  match.estimated_completion = 0.5
  match.comparison = {
    observations: match.comparison!.observations.slice(0, 8),
    log_relative_closes: logs,
  }
  const aligned = comparisonAlignment(pattern, match)!
  expect(aligned.scale).toBeCloseTo(0.9259370330899618, 12)
  for (const [i, value] of [
    [0, -1.8891565332467055],
    [31, 0.14298755803580548],
    [63, 0.5887481974139049],
  ])
    expect(aligned.reference[i].y).toBeCloseTo(value, 12)
  for (const [i, value] of [
    [0, -1.8069080180180441],
    [31, -0.08529340680549945],
    [63, 1.5943306041335685],
  ])
    expect(aligned.observed[i].y).toBeCloseTo(value, 12)
})
it('includes the interpolated prefix endpoint, handles full matches, and refuses missing samples', () => {
  const match = comparisonMatch()
  match.observed_candles = 13
  match.estimated_completion = 0.6
  match.comparison = {
    ...match.comparison!,
    log_relative_closes: Array.from({ length: 13 }, (_, i) => i / 100),
    observations: Array.from({ length: 13 }, (_, i) => ({
      timestamp: new Date(Date.UTC(2025, 0, i + 1)).toISOString(),
      close: '100',
    })),
  }
  const aligned = comparisonAlignment(comparisonPattern(), match)!
  expect(aligned.reference.at(-1)!.x).toBe(0.6)
  expect(aligned.continuation[0]).toEqual(aligned.reference.at(-1))
  expect(
    comparisonAlignment(comparisonPattern(), comparisonMatch('FULL', true))!
      .continuation,
  ).toEqual([])
  expect(
    comparisonAlignment(comparisonPattern(), { ...match, comparison: null }),
  ).toBeNull()
})
it('draws a linear price scale from accurate ratios, preserves elapsed timestamps and avoids overflow', () => {
  const match = comparisonMatch()
  const positions = observedPricePositions(match)
  expect(positions[0]).toEqual({ x: 0, y: 0 })
  expect(positions.at(-1)).toEqual({ x: 1, y: 1 })
  expect(positions[5].y).toBeCloseTo(
    (Math.exp(0.05) - 1) / (Math.exp(0.1) - 1),
    12,
  )
  const extreme = {
    ...match,
    comparison: {
      ...match.comparison!,
      log_relative_closes: Array.from({ length: 11 }, (_, i) => i * 1000),
    },
  }
  expect(
    observedPricePositions(extreme).every((p) => Number.isFinite(p.y)),
  ).toBe(true)
})
