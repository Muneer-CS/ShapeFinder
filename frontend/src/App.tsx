import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  CartesianGrid,
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

type RequestState =
  | { kind: 'idle' | 'loading' }
  | { kind: 'success'; data: TimeSeriesResponse; query: Query }
  | { kind: 'error'; message: string }
type Query = {
  symbol: string
  referenceStart: string
  referenceEnd: string
  interval: MarketInterval
  searchStart: string
  searchEnd: string
}

const errorMessages: Record<string, string> = {
  INVALID_SYMBOL: 'We could not find that ticker. Check it and try again.',
  NO_DATA: 'No price data was found for this reference period.',
  PROVIDER_NOT_CONFIGURED: 'Live market data is not configured yet.',
  PROVIDER_RATE_LIMITED:
    'The market-data limit was reached. Please try again later.',
  PROVIDER_UNAVAILABLE:
    'Market data is temporarily unavailable. Please try again.',
  PROVIDER_AUTHENTICATION_FAILED:
    'Live market data is temporarily unavailable.',
  MALFORMED_PROVIDER_RESPONSE:
    'Market data is temporarily unavailable. Please try again.',
  UNSUPPORTED_INTERVAL: 'That chart interval is not supported.',
  VALIDATION_ERROR: 'Please check the ticker, dates, and interval.',
}

const dateValue = (date: Date) => date.toISOString().slice(0, 10)
const intraday = (interval: MarketInterval) => interval !== '1day'
const boundary = (value: string, interval: MarketInterval, end = false) =>
  intraday(interval)
    ? new Date(value).toISOString()
    : `${value}T${end ? '23:59:59' : '00:00:00'}Z`
const displayDate = (value: string, interval: MarketInterval) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    ...(intraday(interval) ? { timeStyle: 'short' as const } : {}),
  }).format(
    new Date(intraday(interval) ? value : `${value.slice(0, 10)}T12:00:00`),
  )

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
  const [searchStart, setSearchStart] = useState(defaults.searchStart)
  const [searchEnd, setSearchEnd] = useState(defaults.searchEnd)
  const [interval, setInterval] = useState<MarketInterval>('1day')
  const [formError, setFormError] = useState('')
  const [request, setRequest] = useState<RequestState>({ kind: 'idle' })
  const controllerRef = useRef<AbortController | null>(null)

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
  useEffect(() => () => controllerRef.current?.abort(), [])

  function changeInterval(next: MarketInterval) {
    if (intraday(next) && !intraday(interval)) {
      setReferenceStart(`${referenceStart.slice(0, 10)}T09:30`)
      setReferenceEnd(`${referenceEnd.slice(0, 10)}T16:00`)
    } else if (!intraday(next) && intraday(interval)) {
      setReferenceStart(referenceStart.slice(0, 10))
      setReferenceEnd(referenceEnd.slice(0, 10))
    }
    setInterval(next)
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const cleanSymbol = symbol.trim().toUpperCase()
    setSymbol(cleanSymbol)
    setFormError('')
    if (!cleanSymbol) return setFormError('Enter a stock ticker.')
    if (
      !referenceStart ||
      !referenceEnd ||
      new Date(referenceStart) >= new Date(referenceEnd)
    )
      return setFormError('Reference start must be earlier than reference end.')
    if (
      !searchStart ||
      !searchEnd ||
      new Date(searchStart) >= new Date(searchEnd)
    )
      return setFormError('Search start must be earlier than search end.')

    const query = {
      symbol: cleanSymbol,
      referenceStart,
      referenceEnd,
      interval,
      searchStart,
      searchEnd,
    }
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setRequest({ kind: 'loading' })
    try {
      const data = await getMarketData(
        cleanSymbol,
        boundary(referenceStart, interval),
        boundary(referenceEnd, interval, true),
        interval,
        controller.signal,
      )
      setRequest(
        data.bars.length
          ? { kind: 'success', data, query }
          : { kind: 'error', message: errorMessages.NO_DATA },
      )
    } catch (error: unknown) {
      if (error instanceof Error && error.name === 'AbortError') return
      const code =
        error instanceof MarketDataApiError ? error.code : 'UNKNOWN_ERROR'
      setRequest({
        kind: 'error',
        message:
          errorMessages[code] ?? 'Something went wrong. Please try again.',
      })
    }
  }

  const chartData =
    request.kind === 'success'
      ? request.data.bars.map((bar) => ({
          timestamp: bar.timestamp,
          close: Number(bar.close),
        }))
      : []

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
        <h1>Choose the chart shape to find later.</h1>
        <p>
          Load a real price chart now. The separate search period sets the
          boundary for matching in a future phase.
        </p>
      </section>

      <form className="configuration" onSubmit={submit} noValidate>
        <section
          className="config-card reference-config"
          aria-labelledby="reference-title"
        >
          <div className="section-number" aria-hidden="true">
            01
          </div>
          <div className="section-heading">
            <p className="eyebrow">Pattern to match</p>
            <h2 id="reference-title">Reference chart</h2>
          </div>
          <div className="field ticker-field">
            <label htmlFor="symbol">Stock ticker</label>
            <input
              id="symbol"
              value={symbol}
              onChange={(event) => setSymbol(event.target.value)}
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
              onChange={(event) => setReferenceStart(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="reference-end">Reference to</label>
            <input
              id="reference-end"
              type={intraday(interval) ? 'datetime-local' : 'date'}
              value={referenceEnd}
              onChange={(event) => setReferenceEnd(event.target.value)}
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
        </section>

        <section
          className="config-card search-config"
          aria-labelledby="search-title"
        >
          <div className="section-number" aria-hidden="true">
            02
          </div>
          <div className="section-heading">
            <p className="eyebrow">Future matching boundary</p>
            <h2 id="search-title">Search within</h2>
            <p>
              These dates will limit where similar patterns are searched. No
              search runs yet.
            </p>
          </div>
          <div className="field">
            <label htmlFor="search-start">Search from</label>
            <input
              id="search-start"
              type="date"
              value={searchStart}
              onChange={(event) => setSearchStart(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="search-end">Search to</label>
            <input
              id="search-end"
              type="date"
              value={searchEnd}
              onChange={(event) => setSearchEnd(event.target.value)}
            />
          </div>
          <div className="search-note">
            <span aria-hidden="true">↳</span> Saved with this reference in the
            current session
          </div>
        </section>

        <div className="form-action">
          {formError && (
            <p className="form-error" role="alert">
              {formError}
            </p>
          )}
          <button type="submit" disabled={request.kind === 'loading'}>
            {request.kind === 'loading'
              ? 'Loading reference…'
              : 'Load reference chart'}
          </button>
        </div>
      </form>

      <section
        className="result-card"
        aria-labelledby="chart-title"
        aria-busy={request.kind === 'loading'}
      >
        {request.kind === 'idle' && (
          <EmptyResult icon="⌁" title="Your reference chart will appear here">
            Choose a stock and reference period, then load its price history.
          </EmptyResult>
        )}
        {request.kind === 'loading' && (
          <EmptyResult loading title="Loading reference chart">
            Checking the local cache and market-data source…
          </EmptyResult>
        )}
        {request.kind === 'error' && (
          <EmptyResult error icon="!" title="Reference chart unavailable">
            {request.message}
          </EmptyResult>
        )}
        {request.kind === 'success' && (
          <>
            <div className="chart-header">
              <div>
                <p className="eyebrow">Reference chart</p>
                <h2 id="chart-title">{request.data.symbol}</h2>
              </div>
              <div className="chart-context">
                <strong>
                  {displayDate(
                    boundary(
                      request.query.referenceStart,
                      request.query.interval,
                    ),
                    request.query.interval,
                  )}{' '}
                  →{' '}
                  {displayDate(
                    boundary(
                      request.query.referenceEnd,
                      request.query.interval,
                      true,
                    ),
                    request.query.interval,
                  )}
                </strong>
                <span>
                  {
                    intervals.find(
                      (item) => item.value === request.query.interval,
                    )?.label
                  }{' '}
                  interval · {request.data.bars.length} bars
                </span>
              </div>
            </div>
            <div
              className="chart"
              role="img"
              aria-label={`${request.data.symbol} closing price chart`}
            >
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={chartData}
                  margin={{ top: 12, right: 18, bottom: 8, left: 2 }}
                >
                  <CartesianGrid stroke="#203a33" vertical={false} />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(value: string) =>
                      displayDate(value, request.query.interval)
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
                      value.toLocaleString(undefined, {
                        maximumFractionDigits: 2,
                      })
                    }
                  />
                  <Tooltip
                    labelFormatter={(value) =>
                      displayDate(String(value), request.query.interval)
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
                    dot={chartData.length < 3}
                    activeDot={{ r: 5 }}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="search-summary">
              <span>Future search period</span>
              <strong>
                {displayDate(`${request.query.searchStart}T00:00:00Z`, '1day')}{' '}
                → {displayDate(`${request.query.searchEnd}T00:00:00Z`, '1day')}
              </strong>
            </div>
          </>
        )}
      </section>
      <footer>
        <span>Phase 4</span>
        <span>Historical pattern similarity · No predictions</span>
      </footer>
    </main>
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
      <h2 id="chart-title">{title}</h2>
      <p>{children}</p>
    </div>
  )
}

export default App
