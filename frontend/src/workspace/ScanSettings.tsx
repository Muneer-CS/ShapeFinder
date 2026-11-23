import { useId, type ReactNode } from 'react'
import { intervals } from '../api/marketData'
import { reasonText, type Capability } from '../api/patternScans'
import type { durationProblems, SettingsDraft } from '../pattern/settings'

const dailyPresets = [10, 20, 40, 63]

export function ScanSettings({
  draft,
  onChange,
  capability,
  dailyCache,
  duration,
  children,
}: {
  draft: SettingsDraft
  onChange: (draft: SettingsDraft) => void
  capability?: Capability
  dailyCache: boolean
  duration: ReturnType<typeof durationProblems>
  children: ReactNode
}) {
  const id = useId()
  const daily = draft.interval === '1day'
  const { minError, maxError, rangeError } = duration
  const maxMessage = maxError ?? rangeError
  const change = (key: keyof SettingsDraft, value: string | boolean) =>
    onChange({
      ...draft,
      [key]: value,
      ...(key === 'interval'
        ? {
            unfinished: false,
            ...(value === '1day' &&
            dailyCache &&
            draft.universe !== 'nasdaq' &&
            draft.universe !== 'nyse'
              ? { universe: 'nasdaq' }
              : {}),
          }
        : {}),
    })
  return (
    <section className="panel settings-panel" aria-labelledby="settings-title">
      <div className="section-heading">
        <h2 id="settings-title">Scan settings</h2>
      </div>
      <div className="settings-grid">
        <label>
          Candle timeframe
          <select
            value={draft.interval}
            onChange={(e) => change('interval', e.target.value)}
          >
            {intervals.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Stock universe
          <select
            value={draft.universe}
            onChange={(e) => change('universe', e.target.value)}
          >
            <option value="us_equities" disabled={daily && dailyCache}>
              US equities
            </option>
            <option value="nasdaq">NASDAQ</option>
            <option value="nyse">NYSE</option>
            <option value="custom" disabled={daily && dailyCache}>
              Custom symbols
            </option>
          </select>
        </label>
        {draft.universe === 'custom' && (
          <label className="wide-field">
            Custom symbols (up to 10)
            <input
              value={draft.symbols}
              onChange={(e) => change('symbols', e.target.value)}
              placeholder="AAPL, MSFT"
            />
          </label>
        )}
        <fieldset className="wide-field duration-field">
          <legend>
            {daily ? 'Duration in trading sessions' : 'Duration in candles'}
          </legend>
          {daily && (
            <div
              className="duration-presets"
              role="group"
              aria-label="Daily duration presets"
            >
              {dailyPresets.map((sessions) => (
                <button
                  key={sessions}
                  type="button"
                  aria-label={`${sessions} trading sessions`}
                  aria-pressed={
                    Number(draft.minimum) === sessions &&
                    Number(draft.maximum) === sessions
                  }
                  onClick={() =>
                    onChange({
                      ...draft,
                      minimum: String(sessions),
                      maximum: String(sessions),
                    })
                  }
                >
                  {sessions}
                </button>
              ))}
            </div>
          )}
          <p className="helper duration-help" id={`${id}-duration-help`}>
            {daily
              ? 'One candle per trading session. Enter a minimum and maximum for a range.'
              : 'Enter a minimum and maximum for the complete pattern.'}
          </p>
          <div className="two-fields">
            <div>
              <label>
                Minimum candles
                <input
                  type="number"
                  min="8"
                  max="240"
                  aria-invalid={!!(minError || rangeError)}
                  aria-describedby={`${id}-duration-help${minError ? ` ${id}-min-error` : rangeError ? ` ${id}-max-error` : ''}`}
                  value={draft.minimum}
                  onChange={(e) => change('minimum', e.target.value)}
                />
              </label>
              {minError && (
                <span id={`${id}-min-error`} className="field-error">
                  {minError}
                </span>
              )}
            </div>
            <div>
              <label>
                Maximum candles
                <input
                  type="number"
                  min="8"
                  max="240"
                  aria-invalid={!!maxMessage}
                  aria-describedby={`${id}-duration-help${maxMessage ? ` ${id}-max-error` : ''}`}
                  value={draft.maximum}
                  onChange={(e) => change('maximum', e.target.value)}
                />
              </label>
              {maxMessage && (
                <span id={`${id}-max-error`} className="field-error">
                  {maxMessage}
                </span>
              )}
            </div>
          </div>
        </fieldset>
        <label>
          Minimum similarity
          <input
            type="number"
            min="0"
            max="100"
            step="any"
            value={draft.similarity}
            onChange={(e) => change('similarity', e.target.value)}
          />
        </label>
        <label>
          Minimum completion (%)
          <input
            type="number"
            min="0"
            max="100"
            step="any"
            value={draft.completion}
            onChange={(e) => change('completion', e.target.value)}
          />
        </label>
        <label>
          Minimum observed movement (%)
          <input
            type="number"
            min="0"
            step="any"
            value={draft.movement}
            onChange={(e) => change('movement', e.target.value)}
          />
        </label>
        <label>
          Match type
          <select
            value={draft.match}
            onChange={(e) => change('match', e.target.value)}
          >
            <option value="all">All</option>
            <option value="full">Full</option>
            <option value="forming">Forming</option>
          </select>
        </label>
      </div>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={draft.unfinished}
          disabled={!capability?.available || !capability.unfinished_available}
          onChange={(e) => change('unfinished', e.target.checked)}
        />
        Include unfinished candle
      </label>
      {!capability?.unfinished_available && (
        <p className="helper unfinished-note">
          {reasonText(
            capability?.unfinished_reason ?? 'snapshot_timing_unverified',
          )}
        </p>
      )}
      <details className="settings-guide">
        <summary>How these settings work</summary>
        <p>
          Complete duration describes the whole pattern. A forming match follows
          its beginning with fewer observations, with at least 8 required.
        </p>
        <p>
          Movement is 100 × (highest observed close / lowest observed close −
          1). Height in the editor is relative shape, not a price or movement
          threshold. Completed regular-session candles are the default.
        </p>
      </details>
      {children}
    </section>
  )
}
