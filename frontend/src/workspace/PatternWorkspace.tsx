import { useEffect, useRef, useState } from 'react'
import {
  getCapabilities,
  reasonText,
  ScanApiError,
  scanPattern,
  type Capabilities,
  type ScanResult,
} from '../api/patternScans'
import { patternHash, patternProblem, type Pattern } from '../pattern/model'
import {
  defaultDraft,
  parseSettings,
  type SettingsDraft,
} from '../pattern/settings'
import { PatternEditor } from './PatternEditor'
import { StockSectionImport } from './StockSectionImport'
import { ScanSettings } from './ScanSettings'
import { ScanAvailability } from './ScanAvailability'
import { ScanResults } from './ScanResults'
import { usePatternInput, type InputMethod } from './usePatternInput'
import { DescribeInput } from './DescribeInput'
import { ScreenshotInput } from './ScreenshotInput'

export function PatternWorkspace() {
  const [draft, setDraft] = useState(defaultDraft)
  const [settingsRevision, setSettingsRevision] = useState(1)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [capabilityStatus, setCapabilityStatus] = useState(
    'Checking scan availability…',
  )
  const [result, setResult] = useState<ScanResult | null>(null)
  const [resultPattern, setResultPattern] = useState<Pattern | null>(null)
  const [previous, setPrevious] = useState(false)
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState('')
  const [failure, setFailure] = useState<{
    message: string
    details?: ScanResult | null
  } | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const active = useRef<{ id: number; controller: AbortController } | null>(
    null,
  )
  const sequence = useRef(0)
  useEffect(() => {
    const controller = new AbortController()
    void getCapabilities(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setCapabilities(value)
          setCapabilityStatus('No capability was supplied for this timeframe.')
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted)
          setCapabilityStatus(
            `Scan availability could not be checked: ${(error as Error).message}`,
          )
      })
    return () => {
      controller.abort()
      active.current?.controller.abort()
      active.current = null
    }
  }, [])
  function invalidate(markPrevious = true) {
    if (active.current) {
      active.current.controller.abort()
      active.current = null
      setBusy(false)
      setCancelled(true)
      setStatus('Scan cancelled because the workspace changed.')
    }
    if (markPrevious) setPrevious(true)
    setFailure(null)
  }
  const input = usePatternInput(invalidate)
  const revisions = {
    pattern_revision: input.revision,
    settings_revision: settingsRevision,
  }
  function changeSettings(next: SettingsDraft) {
    invalidate()
    setDraft(next)
    setSettingsRevision((current) => current + 1)
  }
  const parsed = parseSettings(draft)
  const capability = capabilities?.intervals.find(
    (c) => c.interval === draft.interval,
  )
  const dailyCache = !!capabilities?.intervals.some(
    (c) => c.interval === '1day' && c.reason === 'massive_daily_cache',
  )
  const needsExchange =
    dailyCache &&
    draft.interval === '1day' &&
    draft.universe !== 'nasdaq' &&
    draft.universe !== 'nyse'
  const problems = [
    patternProblem(input.visible.points),
    needsExchange ? 'Choose Nasdaq or NYSE for the saved daily scan.' : null,
    ...parsed.problems,
  ].filter((p): p is string => !!p)
  const invalid =
    problems.length > 0 || Object.values(parsed.duration).some(Boolean)
  const allowed =
    capability?.available &&
    (!draft.unfinished || capability.unfinished_available)
  async function scan() {
    // Ref guards before asynchronous hashing, including same-tick double clicks.
    if (active.current || !allowed || invalid || input.blocked) return
    const controller = new AbortController(),
      id = ++sequence.current
    active.current = { id, controller }
    setBusy(true)
    setCancelled(false)
    setPrevious(true)
    setFailure(null)
    setStatus('Scanning current patterns…')
    const request = {
      pattern: structuredClone(input.applied),
      revisions: { ...revisions },
      preview_acknowledgement: input.ack,
      settings: parsed.settings,
      universe: parsed.universe,
    }
    try {
      const hash = await patternHash(request.pattern)
      if (active.current?.id !== id || controller.signal.aborted) return
      if (
        request.preview_acknowledgement &&
        (request.preview_acknowledgement.pattern_revision !==
          request.revisions.pattern_revision ||
          request.preview_acknowledgement.content_hash !== hash)
      )
        throw new Error(
          'Preview acceptance does not match this pattern. Accept it again.',
        )
      const response = await scanPattern(request, controller.signal)
      if (active.current?.id !== id || controller.signal.aborted) return
      if (
        response.revisions.pattern_revision !==
          request.revisions.pattern_revision ||
        response.revisions.settings_revision !==
          request.revisions.settings_revision ||
        response.pattern_content_hash !== hash
      )
        throw new Error(
          'The response does not match the submitted pattern and settings revisions.',
        )
      setResult(response)
      setResultPattern(request.pattern)
      setPrevious(false)
      setStatus(
        response.outcome === 'partial'
          ? 'Partial scan complete. Review coverage below.'
          : 'Scan complete.',
      )
    } catch (error) {
      if (active.current?.id !== id || controller.signal.aborted) return
      if (error instanceof ScanApiError) {
        const category =
          error.status === 422
            ? 'Validation rejected'
            : error.status === 503
              ? 'Scan unavailable'
              : error.status === 504
                ? 'Scan deadline reached'
                : error.status === 429
                  ? 'Scan capacity reached'
                  : 'Scan failed'
        setFailure({
          message: `${category}: ${error.reason ? reasonText(error.reason) : error.message}`,
          details: error.details,
        })
      } else setFailure({ message: `Scan failed: ${(error as Error).message}` })
      setStatus('')
    } finally {
      if (active.current?.id === id) {
        active.current = null
        setBusy(false)
      }
    }
  }
  return (
    <div className="workspace">
      <div className="workspace-heading">
        <h1>Pattern scanner</h1>
        <p>Sketch a shape and find recent matches in stock prices.</p>
      </div>
      <div className="workspace-grid">
        <div className="pattern-column">
          <nav className="input-navigation" aria-label="Pattern input methods">
            {(
              [
                ['draw', 'Draw'],
                ['text', 'Describe'],
                ['screenshot_trace', 'Upload'],
                ['stock_chart', 'Stock chart'],
              ] as const
            ).map(([value, label]) => (
              <button
                type="button"
                key={value}
                aria-pressed={input.method === value}
                onClick={() => input.choose(value as InputMethod)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div hidden={input.method !== 'text'}>
            <DescribeInput
              value={input.description}
              onChange={input.descriptionChanged}
              onGenerate={input.generate}
            />
          </div>
          <div hidden={input.method !== 'screenshot_trace'}>
            <ScreenshotInput
              view={input.trace}
              onView={input.traceChanged}
              onDirty={input.sourceChanged}
              onLoading={input.imageLoading}
              onPreview={input.createTrace}
            />
          </div>
          <div hidden={input.method !== 'stock_chart'}>
            <StockSectionImport
              onImport={input.importStock}
              open={
                input.method === 'stock_chart' &&
                input.applied.provenance.kind !== 'stock_section'
              }
            />
          </div>
          {input.outdated && (
            <p role="alert" className="notice">
              Source changed. This interpretation is out of date. Generate a new
              text preview or create a new tracing preview before accepting or
              scanning it.
            </p>
          )}
          {input.sourceError && (
            <p role="alert" className="notice">
              {input.sourceError}
            </p>
          )}
          <PatternEditor
            key={input.editorKey}
            {...input.editor}
            sourceLabel={`${input.preview ? 'Unaccepted preview' : 'Applied pattern'} \u00b7 ${input.visible.provenance.kind.replaceAll('_', ' ')}`}
            trace={input.editorTrace}
            drawingEnabled={input.method === 'draw' || !!input.editorTrace}
          />
          {(input.needsAcceptance || input.ack) && (
            <section
              className="acceptance-strip"
              aria-label="Preview acceptance"
            >
              <p>
                {input.preview
                  ? 'Review this editable draft. Accept preview will replace the applied pattern.'
                  : input.needsAcceptance
                    ? 'Pattern changed. Review and accept this preview again.'
                    : 'Preview accepted.'}
              </p>
              {input.needsAcceptance && (
                <button
                  type="button"
                  disabled={
                    !input.canAccept ||
                    !input.needsAcceptance ||
                    input.accepting
                  }
                  onClick={() => void input.accept()}
                >
                  {input.accepting ? 'Accepting...' : 'Accept preview'}
                </button>
              )}
              {input.preview && (
                <button type="button" onClick={input.cancelPreview}>
                  Cancel preview
                </button>
              )}
            </section>
          )}
          {input.applied.provenance.kind === 'stock_section' && (
            <details className="provenance">
              <summary>
                Imported from {input.applied.provenance.source_symbol},{' '}
                {input.applied.provenance.source_interval}
              </summary>
              <p>
                {input.applied.provenance.source_start_at} to{' '}
                {input.applied.provenance.source_end_at}. Edits retain this
                source attribution.
              </p>
            </details>
          )}
        </div>
        <ScanSettings
          draft={draft}
          onChange={changeSettings}
          capability={capability}
          dailyCache={dailyCache}
          duration={parsed.duration}
        >
          <section className="scan-actions" aria-label="Scan controls">
            <ScanAvailability
              capability={capability}
              status={capabilityStatus}
            />
            {!!problems.length && (
              <ul className="validation-list" aria-label="Workspace validation">
                {problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
            <div className="workspace-run">
              <button
                className="primary scan-button"
                disabled={!allowed || invalid || busy || input.blocked}
                onClick={() => void scan()}
              >
                {busy ? 'Scanning…' : 'Run current scan'}
              </button>
              {busy && (
                <button
                  onClick={() => {
                    active.current?.controller.abort()
                    active.current = null
                    setBusy(false)
                    setCancelled(true)
                    setStatus(
                      'Scan cancelled. Any late response will be ignored.',
                    )
                  }}
                >
                  Cancel scan
                </button>
              )}
            </div>
            <p className="scan-status" role="status">
              {status}
            </p>
            {failure && (
              <p role="alert" className="notice">
                {failure.message}
              </p>
            )}
            <details className="diagnostics">
              <summary>Workspace revisions</summary>
              <p className="helper">
                Pattern revision {revisions.pattern_revision} · settings
                revision {revisions.settings_revision}. Up to 20 ranked stocks;
                coverage may be partial.
              </p>
            </details>
          </section>
        </ScanSettings>
        <div className="results-column">
          <ScanResults
            key={result?.scan_id ?? 'empty'}
            result={result}
            previous={previous}
            pattern={resultPattern}
            busy={busy}
            cancelled={cancelled}
            failure={failure}
          />
        </div>
      </div>
      <footer>
        Similarity and estimated completion measure observed alignment, not the
        probability of a future move.
      </footer>
    </div>
  )
}
