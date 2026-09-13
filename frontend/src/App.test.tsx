import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const bars = (symbol = 'NVDA') => ({
  symbol,
  interval: '1day',
  timezone: 'America/New_York',
  bars: [
    {
      timestamp: '2025-01-02T00:00:00-05:00',
      open: '100',
      high: '104',
      low: '99',
      close: '102.5',
      volume: '1000',
    },
    {
      timestamp: '2025-01-03T00:00:00-05:00',
      open: '102.5',
      high: '106',
      low: '101',
      close: '105',
      volume: '1200',
    },
  ],
})

const matches = [
  {
    symbol: 'AMD',
    start: '2024-02-05T00:00:00Z',
    end: '2024-03-15T23:59:59Z',
    interval: '1day',
    bar_count: 30,
    overall_score: 95.4,
    components: { shape: 97.1, direction: 92.4, error: 95, amplitude: 88.2 },
  },
  {
    symbol: 'MSFT',
    start: '2022-06-01T00:00:00Z',
    end: '2022-07-12T23:59:59Z',
    interval: '1day',
    bar_count: 30,
    overall_score: 88.7,
    components: { shape: 90, direction: 87, error: 89, amplitude: 84 },
  },
  {
    symbol: 'AAPL',
    start: '2021-01-04T00:00:00Z',
    end: '2021-02-12T23:59:59Z',
    interval: '1day',
    bar_count: 30,
    overall_score: 82.1,
    components: { shape: 84, direction: 80, error: 83, amplitude: 78 },
  },
]

const searchSuccess = (items = matches) => ({
  reference: {
    symbol: 'NVDA',
    start: '2025-01-01T00:00:00Z',
    end: '2025-01-04T23:59:59Z',
    interval: '1day',
    bar_count: 2,
  },
  search_start: '2020-01-01T00:00:00Z',
  search_end: '2025-01-04T23:59:59Z',
  matches: items,
  statistics: {
    symbols_requested: 3,
    symbols_scanned: 3,
    windows_evaluated: 2340,
    windows_passing_threshold: items.length,
    matches_returned: items.length,
    universe_id: 'custom',
    universe_symbols_total: 3,
    symbols_eligible: 3,
    symbols_skipped: 0,
    symbols_failed: 0,
    universe_stale: false,
    ready_before_hydration: 0,
    hydration_limit: 0,
    hydration_attempted: 0,
    hydration_succeeded: 0,
    hydration_failed: 0,
    hydration_fetched: 0,
    hydration_persisted: 0,
    hydration_became_ready: 0,
    hydration_suppressed: 0,
    candidates_considered: 0,
    candidates_skipped_historical_ineligible: 0,
    candidates_skipped_cooldown: 0,
    candidates_prioritized_partial_cache: 0,
    useful_success_rate: 0,
    hydration_failure_counts: {},
    ready_after_hydration: 3,
    provider_rate_limited: false,
    provider_daily_quota: false,
    hydration_provider_unavailable: false,
    hydration_timed_out: false,
  },
})

function response(body: unknown, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) }
}

function mockApi(
  options: {
    search?: ReturnType<typeof response>
    previewFails?: boolean
    universeFails?: boolean
  } = {},
) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    if (url.includes('/health'))
      return Promise.resolve(response({ status: 'ok' }))
    if (url.endsWith('/api/v1/universes'))
      if (options.universeFails)
        return Promise.resolve(
          response({ error: { code: 'PROVIDER_NOT_CONFIGURED' } }, false, 503),
        )
      else
        return Promise.resolve(
          response({
            universes: [
              {
                id: 'us_equities',
                name: 'U.S. stocks',
                total_symbols: 3921,
                refreshed_at: '2026-09-11T00:00:00Z',
                stale: false,
              },
              {
                id: 'nasdaq',
                name: 'NASDAQ common stocks',
                total_symbols: 1800,
                refreshed_at: '2026-09-11T00:00:00Z',
                stale: false,
              },
              {
                id: 'nyse',
                name: 'NYSE common stocks',
                total_symbols: 1400,
                refreshed_at: '2026-09-11T00:00:00Z',
                stale: false,
              },
            ],
          }),
        )
    if (url.includes('/similarity/search'))
      return Promise.resolve(options.search ?? response(searchSuccess()))
    const ticker = url.match(/market-data\/([^?]+)/)?.[1] ?? 'NVDA'
    if (options.previewFails && ticker === 'AMD')
      return Promise.resolve(
        response({ error: { code: 'NO_DATA' } }, false, 404),
      )
    return Promise.resolve(response(bars(ticker)))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function setDates() {
  fireEvent.change(screen.getByLabelText('Reference from'), {
    target: { value: '2025-01-01' },
  })
  fireEvent.change(screen.getByLabelText('Reference to'), {
    target: { value: '2025-01-04' },
  })
  fireEvent.change(screen.getByLabelText('Search from'), {
    target: { value: '2020-01-01' },
  })
  fireEvent.change(screen.getByLabelText('Search to'), {
    target: { value: '2025-01-04' },
  })
}

async function loadReference() {
  setDates()
  fireEvent.click(screen.getByRole('button', { name: 'Load reference chart' }))
  await screen.findByRole('img', { name: 'NVDA reference closing price chart' })
}

async function runSearch() {
  fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
  await screen.findByRole('heading', { name: 'Best matches' })
}

function removeDefaultCandidates() {
  for (const symbol of ['AMD', 'AAPL', 'MSFT'])
    fireEvent.click(screen.getByRole('button', { name: `Remove ${symbol}` }))
}

afterEach(() => vi.restoreAllMocks())

describe('reference workflow', () => {
  it('shows a clear API-unavailable state when the backend cannot be reached', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('offline'))),
    )
    render(<App />)
    expect(await screen.findByText('API unavailable')).toBeInTheDocument()
    expect(await screen.findByText('Backend unavailable.')).toBeInTheDocument()
    expect(
      await screen.findByText(/Universe metadata is unavailable/),
    ).toBeInTheDocument()
  })

  it('keeps reference and search periods visibly separate', () => {
    mockApi()
    render(<App />)
    expect(
      screen.getByRole('heading', { name: 'Reference' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Search' })).toBeInTheDocument()
    expect(screen.getByLabelText('How ShapeFinder works')).toHaveTextContent(
      'Choose a search period and stock universe.',
    )
    expect(screen.getByLabelText('Reference from')).toHaveAttribute(
      'type',
      'date',
    )
    expect(screen.getByLabelText('Search from')).toHaveAttribute('type', 'date')
  })

  it('loads the exact reference independently and supports intraday controls', async () => {
    const fetchMock = mockApi()
    render(<App />)
    setDates()
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: ' nvda ' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    await screen.findByRole('img', {
      name: 'NVDA reference closing price chart',
    })
    const marketUrl = String(
      fetchMock.mock.calls.find(([url]) =>
        String(url).includes('/market-data/'),
      )?.[0],
    )
    expect(marketUrl).not.toContain('2020-01-01')

    fireEvent.change(screen.getByLabelText('Chart interval'), {
      target: { value: '5min' },
    })
    expect(screen.getByLabelText('Reference from')).toHaveAttribute(
      'type',
      'datetime-local',
    )
    expect(
      screen.queryByRole('img', { name: 'NVDA reference closing price chart' }),
    ).not.toBeInTheDocument()
  })

  it('validates reference inputs and shows loading and safe errors', async () => {
    mockApi()
    render(<App />)
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: ' ' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a stock ticker')

    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: 'NVDA' },
    })
    fireEvent.change(screen.getByLabelText('Reference from'), {
      target: { value: '2025-02-02' },
    })
    fireEvent.change(screen.getByLabelText('Reference to'), {
      target: { value: '2025-02-01' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Reference start')
  })

  it.each([
    ['INVALID_SYMBOL', 'could not find that ticker'],
    ['PROVIDER_NOT_CONFIGURED', 'required stock data is not available locally'],
    ['PROVIDER_RATE_LIMITED', 'market-data limit was reached'],
    ['PROVIDER_UNAVAILABLE', 'temporarily unavailable'],
  ])('maps reference %s safely', async (code, expected) => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) =>
        Promise.resolve(
          String(input).includes('/health')
            ? response({ status: 'ok' })
            : response({ error: { code, message: 'raw detail' } }, false, 503),
        ),
      ),
    )
    render(<App />)
    setDates()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(expected)
    expect(screen.queryByText('raw detail')).not.toBeInTheDocument()
  })
})

describe('candidate symbol controls', () => {
  it('adds with Enter, normalizes whitespace/casing, prevents duplicates, and removes', () => {
    mockApi()
    render(<App />)
    const input = screen.getByLabelText('Stocks to search')
    fireEvent.change(input, { target: { value: '  avgo  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(
      screen.getByRole('button', { name: 'Remove AVGO' }),
    ).toBeInTheDocument()

    fireEvent.change(input, { target: { value: 'avgo' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getAllByText('AVGO')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Remove AVGO' }))
    expect(screen.queryByText('AVGO')).not.toBeInTheDocument()
  })

  it('accepts comma-separated paste and enforces the 10-symbol maximum', () => {
    mockApi()
    render(<App />)
    const input = screen.getByLabelText('Stocks to search')
    fireEvent.change(input, { target: { value: 'S1,S2,S3,S4,S5,S6,S7,S8' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByText('10 of 10 stocks')).toBeInTheDocument()
    expect(input).toBeDisabled()
    expect(
      screen.getByText('You can search up to 10 stocks at a time.'),
    ).toBeInTheDocument()
  })
})

describe('similarity search workflow', () => {
  it('renders provider-backed universe choices and keeps chips in Custom mode', async () => {
    mockApi()
    render(<App />)
    expect(
      await screen.findByRole('radio', { name: /U\.S\. stocks/ }),
    ).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /NASDAQ/ })).toBeInTheDocument()
    expect(screen.getByLabelText('Stocks to search')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /NASDAQ/ }))
    expect(screen.queryByLabelText('Stocks to search')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /Custom/ }))
    expect(screen.getByLabelText('Stocks to search')).toBeInTheDocument()
  })

  it('posts a named universe without custom symbols', async () => {
    const fetchMock = mockApi()
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('radio', { name: /NASDAQ/ }))
    await runSearch()
    const calls = fetchMock.mock.calls as unknown as Array<
      [RequestInfo | URL, RequestInit?]
    >
    const call = calls.find(([url]) =>
      String(url).includes('/similarity/search'),
    )
    const body = JSON.parse(String(call?.[1]?.body)) as {
      search: Record<string, unknown>
    }
    expect(body.search).toMatchObject({ universe: { kind: 'nasdaq' } })
    expect(body.search).not.toHaveProperty('symbols')
  })

  it('makes partial broad-scan coverage prominent and handles zero eligibility', async () => {
    const partial = searchSuccess([])
    partial.statistics = {
      ...partial.statistics,
      universe_id: 'us_equities',
      universe_symbols_total: 3921,
      symbols_requested: 3921,
      symbols_eligible: 812,
      symbols_scanned: 812,
      symbols_skipped: 3109,
      ready_before_hydration: 807,
      hydration_limit: 5,
      hydration_attempted: 5,
      hydration_succeeded: 5,
      hydration_fetched: 5,
      hydration_persisted: 5,
      hydration_became_ready: 5,
      ready_after_hydration: 812,
    }
    mockApi({ search: response(partial) })
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('radio', { name: /U\.S\. stocks/ }))
    await runSearch()
    expect(
      screen.getByText(/Scanned 812 of 3,921 known stocks/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Added 5 additional stocks to local coverage/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/3,109 still lacked complete cached history/),
    ).toBeInTheDocument()
  })

  it('explains partial hydration when the provider rate limit stops expansion', async () => {
    const partial = searchSuccess([])
    partial.statistics = {
      ...partial.statistics,
      universe_id: 'nasdaq',
      universe_symbols_total: 1800,
      symbols_requested: 1800,
      symbols_eligible: 4,
      symbols_scanned: 4,
      symbols_skipped: 1796,
      ready_before_hydration: 3,
      hydration_limit: 5,
      hydration_attempted: 2,
      hydration_succeeded: 1,
      hydration_failed: 1,
      hydration_fetched: 1,
      hydration_persisted: 1,
      hydration_became_ready: 1,
      ready_after_hydration: 4,
      provider_rate_limited: true,
    }
    mockApi({ search: response(partial) })
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('radio', { name: /NASDAQ/ }))
    await runSearch()
    expect(
      screen.getByText(/Added 1 additional stock to local coverage/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Additional market history could not be loaded right now/,
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText(/TWELVE_DATA_API_KEY/)).not.toBeInTheDocument()
  })

  it('distinguishes daily quota and insufficient-history messages', async () => {
    const partial = searchSuccess([])
    partial.statistics = {
      ...partial.statistics,
      universe_id: 'nasdaq',
      universe_symbols_total: 1800,
      symbols_requested: 1800,
      symbols_eligible: 1,
      symbols_scanned: 1,
      symbols_skipped: 1799,
      hydration_attempted: 1,
      hydration_failed: 1,
      hydration_failure_counts: { insufficient_coverage: 1 },
      provider_daily_quota: true,
    }
    mockApi({ search: response(partial) })
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('radio', { name: /NASDAQ/ }))
    await runSearch()
    expect(
      screen.getByText(/provider daily quota is exhausted/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/did not have enough usable history/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/response body|apikey/i)).not.toBeInTheDocument()
  })

  it('shows a clear state when a universe has no scan-ready stocks', async () => {
    const empty = searchSuccess([])
    empty.statistics = {
      ...empty.statistics,
      universe_id: 'nyse',
      universe_symbols_total: 1400,
      symbols_requested: 1400,
      symbols_eligible: 0,
      symbols_scanned: 0,
      symbols_skipped: 1400,
      hydration_limit: 5,
      hydration_attempted: 5,
      hydration_failed: 5,
    }
    mockApi({ search: response(empty) })
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('radio', { name: /NYSE/ }))
    await runSearch()
    expect(
      screen.getByRole('heading', { name: 'No scan-ready stocks' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/No stocks became scan-ready in this bounded attempt/),
    ).toBeInTheDocument()
  })

  it('explains universe metadata failure while preserving custom search', async () => {
    mockApi({ universeFails: true })
    render(<App />)
    expect(
      await screen.findByText(/Universe metadata is unavailable/),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Stocks to search')).toBeInTheDocument()
  })
  it('validates search range, empty candidates, and minimum score', async () => {
    mockApi()
    render(<App />)
    await loadReference()
    fireEvent.change(screen.getByLabelText('Search from'), {
      target: { value: '2025-02-01' },
    })
    fireEvent.change(screen.getByLabelText('Search to'), {
      target: { value: '2025-01-01' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Search start')

    setDates()
    removeDefaultCandidates()
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Add at least one stock',
    )

    const ticker = screen.getByLabelText('Stocks to search')
    fireEvent.change(ticker, { target: { value: 'AMD' } })
    fireEvent.keyDown(ticker, { key: 'Enter' })
    fireEvent.change(screen.getByLabelText(/Minimum similarity/), {
      target: { value: '101' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    expect(screen.getByRole('alert')).toHaveTextContent('between 0 and 100')
  })

  it('supports the top-N control and posts the exact backend contract', async () => {
    const fetchMock = mockApi()
    render(<App />)
    await loadReference()
    fireEvent.change(screen.getByLabelText('Number of results'), {
      target: { value: '5' },
    })
    fireEvent.change(screen.getByLabelText(/Minimum similarity/), {
      target: { value: '80' },
    })
    await runSearch()
    const calls = fetchMock.mock.calls as unknown as Array<
      [RequestInfo | URL, RequestInit?]
    >
    const call = calls.find(([url]) =>
      String(url).includes('/similarity/search'),
    )
    expect(call?.[1]?.method).toBe('POST')
    const body = JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({
      reference: {
        symbol: 'NVDA',
        start: '2025-01-01T00:00:00Z',
        end: '2025-01-04T23:59:59Z',
        interval: '1day',
      },
      search: {
        start: '2020-01-01T00:00:00Z',
        end: '2025-01-04T23:59:59Z',
        symbols: ['AMD', 'AAPL', 'MSFT'],
      },
      top_n: 5,
      minimum_similarity: 80,
    })
  })

  it('disables duplicate submission and exposes a search loading status', async () => {
    let resolveSearch!: (value: ReturnType<typeof response>) => void
    const pending = new Promise<ReturnType<typeof response>>((resolve) => {
      resolveSearch = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/health'))
          return Promise.resolve(response({ status: 'ok' }))
        if (url.includes('/similarity/search')) return pending
        return Promise.resolve(response(bars()))
      }),
    )
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    const button = screen.getByRole('button', {
      name: 'Searching for similar chart patterns…',
    })
    expect(button).toBeDisabled()
    expect(
      screen.getAllByText('Searching for similar chart patterns…'),
    ).toHaveLength(2)
    resolveSearch(response(searchSuccess()))
    await screen.findByRole('heading', { name: 'Best matches' })
  })

  it('renders ranked results, precise labels, components, and statistics', async () => {
    mockApi()
    render(<App />)
    await loadReference()
    await runSearch()
    const cards = screen.getAllByRole('listitem')
    expect(
      cards.map((card) => within(card).getByRole('heading').textContent),
    ).toEqual(['AMD', 'MSFT', 'AAPL'])
    expect(screen.getByText('95.4')).toBeInTheDocument()
    expect(
      screen.getByText(/3 stocks scanned · 2,340 historical windows compared/),
    ).toBeInTheDocument()
    fireEvent.click(within(cards[0]).getByText('Score details'))
    expect(within(cards[0]).getByText('97.1')).toBeInTheDocument()
    expect(within(cards[0]).getByText('92.4')).toBeInTheDocument()
    expect(within(cards[0]).getByText('overall contour')).toBeInTheDocument()
    expect(within(cards[0]).getByText('point-by-point fit')).toBeInTheDocument()
    expect(
      within(cards[0]).getByText(/not probability or confidence/),
    ).toBeInTheDocument()
  })

  it('shows a successful zero-match state rather than an error', async () => {
    mockApi({ search: response(searchSuccess([])) })
    render(<App />)
    await loadReference()
    await runSearch()
    expect(
      screen.getByText('No matches met your selected similarity threshold.'),
    ).toBeInTheDocument()
  })

  it.each([
    ['VALIDATION_ERROR', 'check the ticker, dates, and search settings'],
    ['PROVIDER_NOT_CONFIGURED', 'required stock data is not available locally'],
    ['PROVIDER_RATE_LIMITED', 'market-data limit was reached'],
    ['PROVIDER_UNAVAILABLE', 'temporarily unavailable'],
  ])(
    'maps search %s without exposing backend details',
    async (code, expected) => {
      mockApi({
        search: response(
          { error: { code, message: 'raw backend detail' } },
          false,
          503,
        ),
      })
      render(<App />)
      await loadReference()
      fireEvent.click(
        screen.getByRole('button', { name: 'Find Similar Charts' }),
      )
      expect(await screen.findByRole('alert')).toHaveTextContent(expected)
      expect(screen.queryByText('raw backend detail')).not.toBeInTheDocument()
    },
  )

  it('selects AMD, fetches its exact period, and shows actual charts plus overlay', async () => {
    const fetchMock = mockApi()
    render(<App />)
    await loadReference()
    await runSearch()
    fireEvent.click(screen.getByRole('button', { name: 'Compare AMD' }))
    expect(
      await screen.findByRole('heading', { name: /NVDA.*versus.*AMD/ }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('img', { name: 'AMD selected match chart' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'NVDA comparison reference chart' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'NVDA and AMD rebased shape overlay' }),
    ).toBeInTheDocument()
    expect(screen.getAllByText('95.4')).not.toHaveLength(0)
    const previewUrl = String(
      fetchMock.mock.calls.find(([url]) =>
        String(url).includes('/market-data/AMD'),
      )?.[0],
    )
    expect(previewUrl).toContain('start=2024-02-05T00%3A00%3A00Z')
    expect(previewUrl).toContain('end=2024-03-15T23%3A59%3A59Z')
    expect(previewUrl).toContain('interval=1day')
  })

  it('keeps results usable if a chart preview fails', async () => {
    mockApi({ previewFails: true })
    render(<App />)
    await loadReference()
    await runSearch()
    fireEvent.click(
      screen.getAllByRole('button', { name: 'Load chart preview' })[0],
    )
    expect(
      await screen.findByText(/Chart preview unavailable/),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })

  it('invalidates stale results when reference or search settings change', async () => {
    mockApi()
    render(<App />)
    await loadReference()
    await runSearch()
    fireEvent.change(screen.getByLabelText('Search from'), {
      target: { value: '2021-01-01' },
    })
    expect(
      screen.queryByRole('heading', { name: 'Best matches' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'NVDA reference closing price chart' }),
    ).toBeInTheDocument()

    await runSearch()
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: 'AAPL' },
    })
    expect(
      screen.queryByRole('heading', { name: 'Best matches' }),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole('img', { name: 'NVDA reference closing price chart' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Find Similar Charts' }),
    ).toBeDisabled()
  })

  it('prevents an older aborted search response from overwriting a newer result', async () => {
    let resolveFirst!: (value: ReturnType<typeof response>) => void
    let resolveSecond!: (value: ReturnType<typeof response>) => void
    const first = new Promise<ReturnType<typeof response>>(
      (resolve) => (resolveFirst = resolve),
    )
    const second = new Promise<ReturnType<typeof response>>(
      (resolve) => (resolveSecond = resolve),
    )
    let searches = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/health'))
          return Promise.resolve(response({ status: 'ok' }))
        if (url.endsWith('/api/v1/universes'))
          return Promise.resolve(response({ universes: [] }))
        if (url.includes('/similarity/search'))
          return searches++ === 0 ? first : second
        return Promise.resolve(response(bars()))
      }),
    )
    render(<App />)
    await loadReference()
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    fireEvent.change(screen.getByLabelText('Search from'), {
      target: { value: '2021-01-01' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Find Similar Charts' }))
    resolveSecond(response(searchSuccess([matches[2]])))
    expect(
      await screen.findByRole('heading', { name: 'AAPL' }),
    ).toBeInTheDocument()
    resolveFirst(response(searchSuccess([matches[0]])))
    await Promise.resolve()
    expect(
      screen.queryByRole('heading', { name: 'AMD' }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'AAPL' })).toBeInTheDocument()
  })
})
