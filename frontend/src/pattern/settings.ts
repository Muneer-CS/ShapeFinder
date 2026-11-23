import type { MarketInterval } from '../api/marketData'
export type Settings = {
  interval: MarketInterval
  duration: { minimum_candles: number; maximum_candles: number }
  minimum_completion: number
  minimum_movement_percent: number
  minimum_similarity: number
  match_type: 'all' | 'full' | 'forming'
  session: 'regular'
  include_unfinished: boolean
  top_n: number
}
export type Universe = {
  kind: 'us_equities' | 'nasdaq' | 'nyse' | 'custom'
  symbols: string[]
}
export type SettingsDraft = {
  interval: MarketInterval
  minimum: string
  maximum: string
  completion: string
  movement: string
  similarity: string
  match: Settings['match_type']
  universe: Universe['kind']
  symbols: string
  unfinished: boolean
}
export const defaultDraft = (): SettingsDraft => ({
  interval: '1day',
  minimum: '20',
  maximum: '20',
  completion: '50',
  movement: '5',
  similarity: '80',
  match: 'all',
  universe: 'nasdaq',
  symbols: '',
  unfinished: false,
})

export function durationProblems(draft: SettingsDraft) {
  const minimum = Number(draft.minimum)
  const maximum = Number(draft.maximum)
  const valid = (value: number) =>
    Number.isInteger(value) && value >= 8 && value <= 240
  const unit = draft.interval === '1day' ? 'trading sessions' : 'candles'
  const minError = valid(minimum)
    ? null
    : `Enter a whole number from 8 to 240 ${unit}.`
  const maxError = valid(maximum)
    ? null
    : `Enter a whole number from 8 to 240 ${unit}.`
  const rangeError =
    !minError && !maxError && minimum > maximum
      ? 'Maximum duration must be at least the minimum.'
      : null
  return { minError, maxError, rangeError }
}

export function parseSettings(draft: SettingsDraft): {
  settings: Settings
  universe: Universe
  problems: string[]
  duration: ReturnType<typeof durationProblems>
} {
  const values = [
    draft.minimum,
    draft.maximum,
    draft.completion,
    draft.movement,
    draft.similarity,
  ]
  const [minimum, maximum, completion, movement, similarity] = values.map(
    (value) => (value.trim() === '' ? NaN : Number(value)),
  )
  const problems: string[] = []
  const duration = durationProblems(draft)
  if (!Number.isFinite(completion) || completion <= 0 || completion > 100)
    problems.push(
      'Minimum completion must be greater than 0% and at most 100%.',
    )
  if (!Number.isFinite(movement) || movement < 0)
    problems.push(
      'Minimum movement must be a finite percentage of at least 0%.',
    )
  if (!Number.isFinite(similarity) || similarity < 0 || similarity > 100)
    problems.push('Minimum similarity must be from 0 to 100.')
  if (
    draft.match === 'forming' &&
    (maximum < 9 || completion / 100 > (maximum - 2) / (maximum - 1))
  )
    problems.push(
      'These duration and completion settings cannot produce a forming match.',
    )
  const symbols =
    draft.universe === 'custom'
      ? [
          ...new Set(
            draft.symbols
              .toUpperCase()
              .split(/[\s,]+/)
              .filter(Boolean),
          ),
        ]
      : []
  if (
    draft.universe === 'custom' &&
    (!symbols.length ||
      symbols.length > 10 ||
      symbols.some((s) => !/^[A-Z][A-Z0-9.-]{0,14}$/.test(s)))
  )
    problems.push(
      'Enter 1 to 10 supported symbols, separated by spaces or commas.',
    )
  return {
    settings: {
      interval: draft.interval,
      duration: { minimum_candles: minimum, maximum_candles: maximum },
      minimum_completion: completion / 100,
      minimum_movement_percent: movement,
      minimum_similarity: similarity,
      match_type: draft.match,
      session: 'regular',
      include_unfinished: draft.unfinished,
      top_n: 20,
    },
    universe: { kind: draft.universe, symbols },
    problems,
    duration,
  }
}
