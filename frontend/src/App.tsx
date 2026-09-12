import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getHealth } from './api/health'
import {
  getMarketData,
  intervals,
  MarketDataApiError,
  type MarketInterval,
  type TimeSeriesResponse,
} from './api/marketData'
import {
  searchSimilarity,
  type SimilarityMatch,
  type SimilaritySearchResponse,
} from './api/similaritySearch'

type ReferenceQuery = {
  symbol: string
  start: string
  end: string
  interval: MarketInterval
}
type ReferenceState =
  | { kind: 'idle' | 'loading' }
  | { kind: 'success'; data: TimeSeriesResponse; query: ReferenceQuery }
  | { kind: 'error'; message: string }
type SearchState =
  | { kind: 'idle' | 'loading' }
  | { kind: 'success'; data: SimilaritySearchResponse }
  | { kind: 'error'; message: string }
type PreviewState =
  | { kind: 'loading' }
  | { kind: 'success'; data: TimeSeriesResponse }
  | { kind: 'error' }

const errorMessages: Record<string, string> = {
  INVALID_SYMBOL: 'We could not find that ticker. Check it and try again.',
  NO_DATA: 'The required stock data is not available for that period.',
  PROVIDER_NOT_CONFIGURED:
    'Live market data is not configured, and the required stock data is not available locally.',
  PROVIDER_RATE_LIMITED:
    'The market-data limit was reached. Please try again later.',
  PROVIDER_UNAVAILABLE:
    'Market data is temporarily unavailable. Please try again.',
  PROVIDER_AUTHENTICATION_FAILED:
    'Live market data is temporarily unavailable.',
  MALFORMED_PROVIDER_RESPONSE:
    'Market data is temporarily unavailable. Please try again.',
  UNSUPPORTED_INTERVAL: 'That chart interval is not supported.',
  INVALID_SIMILARITY_SEARCH: 'Please check the search settings and try again.',
  VALIDATION_ERROR: 'Please check the ticker, dates, and search settings.',
  INVALID_RESPONSE: 'The server returned an unexpected response.',
}

const dateValue = (date: Date) => date.toISOString().slice(0, 10)
const intraday = (interval: MarketInterval) => interval !== '1day'
const boundary = (value: string, interval: MarketInterval, end = false) =>
  intraday(interval)
    ? new Date(value).toISOString()
    : `${value}T${end ? '23:59:59' : '00:00:00'}Z`
const searchBoundary = (value: string, end = false) =>
  `${value}T${end ? '23:59:59' : '00:00:00'}Z`
const displayDate = (value: string, interval: MarketInterval) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    ...(intraday(interval) ? { timeStyle: 'short' as const } : {}),
  }).format(
    new Date(intraday(interval) ? value : `${value.slice(0, 10)}T12:00:00`),
  )
const safeMessage = (error: unknown) => {
  const code =
    error instanceof MarketDataApiError ? error.code : 'UNKNOWN_ERROR'
  return errorMessages[code] ?? 'Something went wrong. Please try again.'
}
const previewKey = (match: SimilarityMatch) =>
  `${match.symbol}|${match.start}|${match.end}|${match.interval}`

function initialDates() {
  const today = new Date()
  const monthAgo = new Date(today)
  monthAgo.setUTCDate(monthAgo.getUTCDate() - 30)
  const fiveYearsAgo = new Date(today)
  fiveYearsAgo.setUTCFullYear(fiveYearsAgo.getUTCFullYear() - 5)
  return {
    referenceStart: dateValue(monthAgo),
    referenceEnd: dateValue(today),
    searchStart: dateValue(fiveYearsAgo),
    searchEnd: dateValue(today),
  }
}

function App() {
  const defaults = useMemo(() => initialDates(), [])
  const [connection, setConnection] = useState<
    'checking' | 'connected' | 'offline'
  >('checking')
  const [symbol, setSymbol] = useState('NVDA')
  const [referenceStart, setReferenceStart] = useState(defaults.referenceStart)
  const [referenceEnd, setReferenceEnd] = useState(defaults.referenceEnd)
  const [interval, setInterval] = useState<MarketInterval>('1day')
  const [referenceError, setReferenceError] = useState('')
  const [reference, setReference] = useState<ReferenceState>({ kind: 'idle' })

  const [searchStart, setSearchStart] = useState(defaults.searchStart)
  const [searchEnd, setSearchEnd] = useState(defaults.searchEnd)
  const [candidateSymbols, setCandidateSymbols] = useState([
    'AMD',
    'AAPL',
    'MSFT',
  ])
  const [candidateInput, setCandidateInput] = useState('')
  const [candidateMessage, setCandidateMessage] = useState('')
  const [topN, setTopN] = useState(10)
  const [minimumSimilarity, setMinimumSimilarity] = useState('')
  const [searchError, setSearchError] = useState('')
  const [search, setSearch] = useState<SearchState>({ kind: 'idle' })
  const [selectedMatch, setSelectedMatch] = useState<SimilarityMatch | null>(
    null,
  )
  const [previews, setPreviews] = useState<Record<string, PreviewState>>({})

  const referenceController = useRef<AbortController | null>(null)
  const searchController = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then(() => setConnection('connected'))
      .catch((error: unknown) => {
        if (!(error instanceof Error && error.name === 'AbortError'))
          setConnection('offline')
      })
    return () => controller.abort()
  }, [])
  useEffect(
    () => () => {
      referenceController.current?.abort()
      searchController.current?.abort()
    },
    [],
  )

  function clearSearchResults() {
    searchController.current?.abort()
    setSearch({ kind: 'idle' })
    setSearchError('')
    setSelectedMatch(null)
    setPreviews({})
  }

  function invalidateReference() {
    referenceController.current?.abort()
    setReference({ kind: 'idle' })
    setReferenceError('')
    clearSearchResults()
  }

  function updateReference(field: 'symbol' | 'start' | 'end', value: string) {
    invalidateReference()
    if (field === 'symbol') setSymbol(value)
    if (field === 'start') setReferenceStart(value)
    if (field === 'end') setReferenceEnd(value)
  }

  function changeInterval(next: MarketInterval) {
    invalidateReference()
    if (intraday(next) && !intraday(interval)) {
      setReferenceStart(`${referenceStart.slice(0, 10)}T09:30`)
      setReferenceEnd(`${referenceEnd.slice(0, 10)}T16:00`)
    } else if (!intraday(next) && intraday(interval)) {
      setReferenceStart(referenceStart.slice(0, 10))
      setReferenceEnd(referenceEnd.slice(0, 10))
    }
    setInterval(next)
  }

  async function loadReference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cleanSymbol = symbol.trim().toUpperCase()
    setSymbol(cleanSymbol)
    setReferenceError('')
    if (!cleanSymbol) return setReferenceError('Enter a stock ticker.')
    if (
      !referenceStart ||
      !referenceEnd ||
      new Date(referenceStart) >= new Date(referenceEnd)
    )
      return setReferenceError(
        'Reference start must be earlier than reference end.',
      )

    clearSearchResults()
    referenceController.current?.abort()
    const controller = new AbortController()
    referenceController.current = controller
    setReference({ kind: 'loading' })
    try {
      const data = await getMarketData(
        cleanSymbol,
        boundary(referenceStart, interval),
        boundary(referenceEnd, interval, true),
        interval,
        controller.signal,
      )
      setReference(
        data.bars.length
          ? {
              kind: 'success',
              data,
              query: {
                symbol: cleanSymbol,
                start: referenceStart,
                end: referenceEnd,
                interval,
              },
            }
          : { kind: 'error', message: errorMessages.NO_DATA },
      )
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') return
      setReference({ kind: 'error', message: safeMessage(error) })
    }
  }

  function updateSearch(updater: () => void) {
    clearSearchResults()
    updater()
  }

  function addCandidates(raw = candidateInput) {
    const additions = raw
      .split(',')
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean)
    if (!additions.length) return
    const next = [...candidateSymbols]
    for (const ticker of additions) {
      if (next.includes(ticker)) continue
      if (next.length === 10) {
        setCandidateMessage('You can search up to 10 stocks at a time.')
        break
      }
      next.push(ticker)
    }
    clearSearchResults()
    setCandidateSymbols(next)
    setCandidateInput('')
    if (next.length < 10) setCandidateMessage('')
  }

  function handleCandidateKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      addCandidates()
    }
  }

  function removeCandidate(ticker: string) {
    clearSearchResults()
    setCandidateSymbols((current) => current.filter((item) => item !== ticker))
    setCandidateMessage('')
  }

  async function runSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSearchError('')
    if (reference.kind !== 'success')
      return setSearchError(
        'Load the current reference chart before searching.',
      )
    if (
      !searchStart ||
      !searchEnd ||
      new Date(searchStart) >= new Date(searchEnd)
    )
      return setSearchError('Search start must be earlier than search end.')
    if (!candidateSymbols.length)
      return setSearchError('Add at least one stock to search.')
    const minimum = minimumSimilarity === '' ? null : Number(minimumSimilarity)
    if (
      minimum !== null &&
      (!Number.isFinite(minimum) || minimum < 0 || minimum > 100)
    )
      return setSearchError('Minimum similarity must be between 0 and 100.')

    searchController.current?.abort()
    const controller = new AbortController()
    searchController.current = controller
    setSelectedMatch(null)
    setPreviews({})
    setSearch({ kind: 'loading' })
    try {
      const data = await searchSimilarity(
        {
          reference: {
            symbol: reference.query.symbol,
            start: boundary(reference.query.start, reference.query.interval),
            end: boundary(reference.query.end, reference.query.interval, true),
            interval: reference.query.interval,
          },
          search: {
            start: searchBoundary(searchStart),
            end: searchBoundary(searchEnd, true),
            symbols: candidateSymbols,
          },
          top_n: topN,
          minimum_similarity: minimum,
        },
        controller.signal,
      )
      setSearch({ kind: 'success', data })
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') return
      setSearch({ kind: 'error', message: safeMessage(error) })
    }
  }

  async function loadPreview(match: SimilarityMatch) {
    const key = previewKey(match)
    if (previews[key]?.kind === 'loading' || previews[key]?.kind === 'success')
      return
    setPreviews((current) => ({ ...current, [key]: { kind: 'loading' } }))
    try {
      const data = await getMarketData(
        match.symbol,
        match.start,
        match.end,
        match.interval,
      )
      setPreviews((current) => ({
        ...current,
        [key]: data.bars.length ? { kind: 'success', data } : { kind: 'error' },
      }))
    } catch {
      setPreviews((current) => ({ ...current, [key]: { kind: 'error' } }))
    }
  }

  function selectMatch(match: SimilarityMatch) {
    setSelectedMatch(match)
    void loadPreview(match)
  }

  return (
    <main>
      <header className="topbar">
        <a className="brand" href="/" aria-label="ShapeFinder home">
          <span className="brand-mark" aria-hidden="true">
            ⌁
          </span>
          ShapeFinder
        </a>
        <div className={`status status-${connection}`} role="status">
          <span className="status-dot" aria-hidden="true" />
          {connection === 'checking'
            ? 'Checking API'
            : connection === 'connected'
              ? 'API connected'
              : 'API unavailable'}
        </div>
      </header>

      <section className="page-heading">
        <p className="eyebrow">Historical pattern research</p>
        <h1>Find charts that moved alike.</h1>
        <p>
          Choose the shape you want to match, then search selected stocks for
          similar historical behavior—not similar prices.
        </p>
      </section>

      <form className="reference-form" onSubmit={loadReference} noValidate>
        <section
          className="config-card reference-config"
          aria-labelledby="reference-title"
        >
          <div className="section-number" aria-hidden="true">
            01
          </div>
          <div className="section-heading">
            <p className="eyebrow">What chart are you matching?</p>
            <h2 id="reference-title">Reference</h2>
          </div>
          <div className="field ticker-field">
            <label htmlFor="symbol">Stock ticker</label>
            <input
              id="symbol"
              value={symbol}
              onChange={(event) =>
                updateReference('symbol', event.target.value)
              }
              placeholder="NVDA"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="field">
            <label htmlFor="reference-start">Reference from</label>
            <input
              id="reference-start"
              type={intraday(interval) ? 'datetime-local' : 'date'}
              value={referenceStart}
              onChange={(event) => updateReference('start', event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="reference-end">Reference to</label>
            <input
              id="reference-end"
              type={intraday(interval) ? 'datetime-local' : 'date'}
              value={referenceEnd}
              onChange={(event) => updateReference('end', event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="interval">Chart interval</label>
            <select
              id="interval"
              value={interval}
              onChange={(event) =>
                changeInterval(event.target.value as MarketInterval)
              }
            >
              {intervals.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="card-action">
            {referenceError && (
              <p className="form-error" role="alert">
                {referenceError}
              </p>
            )}
            <button type="submit" disabled={reference.kind === 'loading'}>
              {reference.kind === 'loading'
                ? 'Loading reference…'
                : 'Load reference chart'}
            </button>
          </div>
        </section>
      </form>

      <ReferenceChart state={reference} />

      <form className="search-form" onSubmit={runSearch} noValidate>
        <section
          className="config-card search-config"
          aria-labelledby="search-title"
        >
          <div className="section-number" aria-hidden="true">
            02
          </div>
          <div className="section-heading">
            <p className="eyebrow">Where should ShapeFinder look?</p>
            <h2 id="search-title">Search</h2>
          </div>
          <div className="field">
            <label htmlFor="search-start">Search from</label>
            <input
              id="search-start"
              type="date"
              value={searchStart}
              onChange={(event) =>
                updateSearch(() => setSearchStart(event.target.value))
              }
            />
          </div>
          <div className="field">
            <label htmlFor="search-end">Search to</label>
            <input
              id="search-end"
              type="date"
              value={searchEnd}
              onChange={(event) =>
                updateSearch(() => setSearchEnd(event.target.value))
              }
            />
          </div>
          <div className="field results-field">
            <label htmlFor="top-n">Number of results</label>
            <select
              id="top-n"
              value={topN}
              onChange={(event) =>
                updateSearch(() => setTopN(Number(event.target.value)))
              }
            >
              <option value={5}>5</option>
              <option value={10}>10</option>
              <option value={20}>20</option>
            </select>
          </div>
          <div className="field threshold-field">
            <label htmlFor="minimum-similarity">
              Minimum similarity <span>optional</span>
            </label>
            <input
              id="minimum-similarity"
              type="number"
              min="0"
              max="100"
              step="0.1"
              value={minimumSimilarity}
              onChange={(event) =>
                updateSearch(() => setMinimumSimilarity(event.target.value))
              }
              placeholder="e.g. 80"
            />
            <small>
              Only show engineered similarity scores at or above this value.
            </small>
          </div>
          <div className="candidate-field">
            <label htmlFor="candidate-input">Stocks to search</label>
            <div className="ticker-entry">
              <div className="ticker-chips" aria-label="Candidate stocks">
                {candidateSymbols.map((ticker) => (
                  <span className="ticker-chip" key={ticker}>
                    {ticker}
                    <button
                      type="button"
                      onClick={() => removeCandidate(ticker)}
                      aria-label={`Remove ${ticker}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
                <input
                  id="candidate-input"
                  value={candidateInput}
                  onChange={(event) => setCandidateInput(event.target.value)}
                  onKeyDown={handleCandidateKey}
                  onBlur={() => addCandidates()}
                  disabled={candidateSymbols.length === 10}
                  placeholder={
                    candidateSymbols.length === 10
                      ? 'Limit reached'
                      : 'Add ticker'
                  }
                  autoComplete="off"
                  spellCheck={false}
                />
              </div>
              <button
                className="add-ticker"
                type="button"
                onClick={() => addCandidates()}
                disabled={candidateSymbols.length === 10}
              >
                Add
              </button>
            </div>
            <div className="candidate-meta">
              <span>{candidateSymbols.length} of 10 stocks</span>
              {candidateMessage && (
                <span role="status">{candidateMessage}</span>
              )}
            </div>
          </div>
          <div className="card-action search-action">
            {searchError && (
              <p className="form-error" role="alert">
                {searchError}
              </p>
            )}
            <button
              type="submit"
              disabled={
                search.kind === 'loading' || reference.kind !== 'success'
              }
            >
              {search.kind === 'loading'
                ? 'Searching for similar chart patterns…'
                : 'Find Similar Charts'}
            </button>
          </div>
        </section>
      </form>

      <SearchResults
        state={search}
        previews={previews}
        selected={selectedMatch}
        onPreview={(match) => void loadPreview(match)}
        onSelect={selectMatch}
      />

      {reference.kind === 'success' && selectedMatch && (
        <Comparison
          reference={reference}
          match={selectedMatch}
          preview={previews[previewKey(selectedMatch)]}
        />
      )}

      <footer>
        <span>Phase 7</span>
        <span>Engineered chart-shape similarity · No predictions</span>
      </footer>
    </main>
  )
}

function ReferenceChart({ state }: { state: ReferenceState }) {
  return (
    <section
      className="result-card reference-result"
      aria-labelledby="chart-title"
      aria-busy={state.kind === 'loading'}
    >
      {state.kind === 'idle' && (
        <EmptyResult icon="⌁" title="Your reference chart will appear here">
          Choose a stock and reference period, then load its price history.
        </EmptyResult>
      )}
      {state.kind === 'loading' && (
        <EmptyResult loading title="Loading reference chart">
          Checking the local cache and market-data source…
        </EmptyResult>
      )}
      {state.kind === 'error' && (
        <EmptyResult error icon="!" title="Reference chart unavailable">
          {state.message}
        </EmptyResult>
      )}
      {state.kind === 'success' && (
        <>
          <ChartHeading
            eyebrow="Reference · actual price"
            symbol={state.data.symbol}
            start={state.query.start}
            end={state.query.end}
            interval={state.query.interval}
            bars={state.data.bars.length}
          />
          <PriceChart
            series={state.data}
            label={`${state.data.symbol} reference closing price chart`}
          />
        </>
      )}
    </section>
  )
}

function SearchResults({
  state,
  previews,
  selected,
  onPreview,
  onSelect,
}: {
  state: SearchState
  previews: Record<string, PreviewState>
  selected: SimilarityMatch | null
  onPreview: (match: SimilarityMatch) => void
  onSelect: (match: SimilarityMatch) => void
}) {
  if (state.kind === 'idle') return null
  return (
    <section
      className="matches-section"
      aria-labelledby="matches-title"
      aria-busy={state.kind === 'loading'}
    >
      {state.kind === 'loading' && (
        <EmptyResult loading title="Searching for similar chart patterns…">
          Comparing historical windows across your selected stocks.
        </EmptyResult>
      )}
      {state.kind === 'error' && (
        <EmptyResult error icon="!" title="Search unavailable">
          {state.message}
        </EmptyResult>
      )}
      {state.kind === 'success' && (
        <>
          <div className="matches-heading">
            <div>
              <p className="eyebrow">Ranked historical periods</p>
              <h2 id="matches-title">Best matches</h2>
            </div>
            <p>
              {state.data.statistics.symbols_scanned} stocks scanned ·{' '}
              {state.data.statistics.windows_evaluated.toLocaleString()}{' '}
              historical windows compared
            </p>
          </div>
          {state.data.matches.length === 0 ? (
            <div className="zero-results" role="status">
              <h3>No matching periods found</h3>
              <p>No matches met your selected similarity threshold.</p>
            </div>
          ) : (
            <ol className="matches-list">
              {state.data.matches.map((match, index) => {
                const preview = previews[previewKey(match)]
                const isSelected = selected === match
                return (
                  <li
                    className={`match-card${isSelected ? ' selected' : ''}`}
                    key={previewKey(match)}
                  >
                    <div className="match-summary">
                      <span className="rank">#{index + 1}</span>
                      <div>
                        <h3>{match.symbol}</h3>
                        <p>
                          {displayDate(match.start, match.interval)} –{' '}
                          {displayDate(match.end, match.interval)}
                        </p>
                      </div>
                      <strong>
                        {match.overall_score.toFixed(1)}
                        <span>% similar</span>
                      </strong>
                    </div>
                    <div className="preview-shell">
                      {!preview && (
                        <button
                          type="button"
                          className="preview-button"
                          onClick={() => onPreview(match)}
                        >
                          Load chart preview
                        </button>
                      )}
                      {preview?.kind === 'loading' && (
                        <p role="status">Loading chart preview…</p>
                      )}
                      {preview?.kind === 'error' && (
                        <p role="status">
                          Chart preview unavailable. The match result is still
                          valid.
                        </p>
                      )}
                      {preview?.kind === 'success' && (
                        <PriceChart
                          series={preview.data}
                          compact
                          label={`${match.symbol} match chart preview`}
                        />
                      )}
                    </div>
                    <div className="match-actions">
                      <details>
                        <summary>Score details</summary>
                        <dl>
                          <Score label="Shape" value={match.components.shape} />
                          <Score
                            label="Direction"
                            value={match.components.direction}
                          />
                          <Score label="Path" value={match.components.error} />
                          <Score
                            label="Amplitude"
                            value={match.components.amplitude}
                          />
                        </dl>
                      </details>
                      <button
                        type="button"
                        onClick={() => onSelect(match)}
                        aria-pressed={isSelected}
                      >
                        {isSelected
                          ? 'Selected for comparison'
                          : `Compare ${match.symbol}`}
                      </button>
                    </div>
                  </li>
                )
              })}
            </ol>
          )}
        </>
      )}
    </section>
  )
}

function Comparison({
  reference,
  match,
  preview,
}: {
  reference: Extract<ReferenceState, { kind: 'success' }>
  match: SimilarityMatch
  preview?: PreviewState
}) {
  const overlay =
    preview?.kind === 'success' ? overlayData(reference.data, preview.data) : []
  return (
    <section className="comparison" aria-labelledby="comparison-title">
      <div className="comparison-heading">
        <div>
          <p className="eyebrow">Selected comparison</p>
          <h2 id="comparison-title">
            {reference.data.symbol} <span>versus</span> {match.symbol}
          </h2>
        </div>
        <strong>{match.overall_score.toFixed(1)}% similar</strong>
      </div>
      <div className="comparison-grid">
        <article>
          <h3>{reference.data.symbol} · Reference</h3>
          <p>
            Actual closing price ·{' '}
            {displayDate(reference.query.start, reference.query.interval)} –{' '}
            {displayDate(reference.query.end, reference.query.interval)}
          </p>
          <PriceChart
            series={reference.data}
            compact
            label={`${reference.data.symbol} comparison reference chart`}
          />
        </article>
        <article>
          <h3>{match.symbol} · Match</h3>
          <p>
            Actual closing price · {displayDate(match.start, match.interval)} –{' '}
            {displayDate(match.end, match.interval)}
          </p>
          {preview?.kind === 'loading' && (
            <div className="comparison-placeholder" role="status">
              Loading match chart…
            </div>
          )}
          {preview?.kind === 'error' && (
            <div className="comparison-placeholder">
              Match chart unavailable.
            </div>
          )}
          {preview?.kind === 'success' && (
            <PriceChart
              series={preview.data}
              compact
              label={`${match.symbol} selected match chart`}
            />
          )}
        </article>
      </div>
      {overlay.length > 0 && (
        <article className="overlay-card">
          <div>
            <h3>Shape overlay</h3>
            <p>
              Visual aid only · both series rebased to 100 · not actual prices
              or the scoring calculation
            </p>
          </div>
          <div
            className="overlay-chart"
            role="img"
            aria-label={`${reference.data.symbol} and ${match.symbol} rebased shape overlay`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={overlay}
                margin={{ top: 10, right: 18, bottom: 4, left: 0 }}
              >
                <CartesianGrid stroke="#203a33" vertical={false} />
                <XAxis dataKey="index" hide />
                <YAxis
                  tick={{ fill: '#82968f', fontSize: 12 }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip
                  contentStyle={{
                    background: '#10231e',
                    border: '1px solid #35564d',
                    borderRadius: 8,
                  }}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="reference"
                  name={reference.data.symbol}
                  stroke="#6ee7b7"
                  strokeWidth={2.5}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="match"
                  name={match.symbol}
                  stroke="#f4c95d"
                  strokeWidth={2.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </article>
      )}
    </section>
  )
}

function overlayData(reference: TimeSeriesResponse, match: TimeSeriesResponse) {
  const referenceFirst = Number(reference.bars[0]?.close)
  const matchFirst = Number(match.bars[0]?.close)
  if (!referenceFirst || !matchFirst) return []
  const length = Math.min(reference.bars.length, match.bars.length)
  return Array.from({ length }, (_, index) => ({
    index,
    reference: (Number(reference.bars[index].close) / referenceFirst) * 100,
    match: (Number(match.bars[index].close) / matchFirst) * 100,
  }))
}

function Score({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value.toFixed(1)}</dd>
    </div>
  )
}

function ChartHeading({
  eyebrow,
  symbol,
  start,
  end,
  interval,
  bars,
}: {
  eyebrow: string
  symbol: string
  start: string
  end: string
  interval: MarketInterval
  bars: number
}) {
  return (
    <div className="chart-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2 id="chart-title">{symbol}</h2>
      </div>
      <div className="chart-context">
        <strong>
          {displayDate(start, interval)} → {displayDate(end, interval)}
        </strong>
        <span>
          {intervals.find((item) => item.value === interval)?.label} interval ·{' '}
          {bars} bars
        </span>
      </div>
    </div>
  )
}

function PriceChart({
  series,
  label,
  compact = false,
}: {
  series: TimeSeriesResponse
  label: string
  compact?: boolean
}) {
  const data = series.bars.map((bar) => ({
    timestamp: bar.timestamp,
    close: Number(bar.close),
  }))
  return (
    <div
      className={`chart${compact ? ' chart-compact' : ''}`}
      role="img"
      aria-label={label}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 12, right: 18, bottom: 8, left: 2 }}
        >
          <CartesianGrid stroke="#203a33" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(value: string) =>
              displayDate(value, series.interval)
            }
            minTickGap={48}
            tick={{ fill: '#82968f', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fill: '#82968f', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={58}
            tickFormatter={(value: number) =>
              value.toLocaleString(undefined, { maximumFractionDigits: 2 })
            }
          />
          <Tooltip
            labelFormatter={(value) =>
              displayDate(String(value), series.interval)
            }
            formatter={(value) => [
              Number(value).toLocaleString(undefined, {
                maximumFractionDigits: 4,
              }),
              'Close',
            ]}
            contentStyle={{
              background: '#10231e',
              border: '1px solid #35564d',
              borderRadius: 8,
            }}
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke="#6ee7b7"
            strokeWidth={2.5}
            dot={data.length < 3}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function EmptyResult({
  icon,
  title,
  children,
  loading = false,
  error = false,
}: {
  icon?: string
  title: string
  children: string
  loading?: boolean
  error?: boolean
}) {
  return (
    <div
      className={`empty-state${error ? ' error-state' : ''}`}
      role={loading ? 'status' : error ? 'alert' : undefined}
    >
      {loading ? (
        <span className="loader" aria-hidden="true" />
      ) : (
        <span className="empty-icon" aria-hidden="true">
          {icon}
        </span>
      )}
      <h2>{title}</h2>
      <p>{children}</p>
    </div>
  )
}

export default App
