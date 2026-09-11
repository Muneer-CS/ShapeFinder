import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const success = (symbol = 'NVDA') => ({
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

function response(body: unknown, ok = true, status = 200) {
  return { ok, status, json: () => Promise.resolve(body) }
}

function mockApi(
  marketResponse: ReturnType<typeof response> = response(success()),
) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    return Promise.resolve(
      url.includes('/health')
        ? response({
            status: 'ok',
            service: 'shape-finder-api',
            version: '0.1.0',
          })
        : marketResponse,
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function setValidDates() {
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

afterEach(() => vi.restoreAllMocks())

describe('reference chart workflow', () => {
  it('renders the initial reference and separate search controls', () => {
    mockApi()
    render(<App />)
    expect(
      screen.getByRole('heading', { name: 'Reference chart' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Search within' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Stock ticker')).toHaveValue('NVDA')
    expect(screen.getByLabelText('Reference from')).toHaveAttribute(
      'type',
      'date',
    )
    expect(screen.getByLabelText('Reference to')).toHaveAttribute(
      'type',
      'date',
    )
    expect(screen.getByLabelText('Search from')).toHaveAttribute('type', 'date')
    expect(screen.getByLabelText('Search to')).toHaveAttribute('type', 'date')
    expect(screen.getByLabelText('Chart interval')).toHaveValue('1day')
  })

  it('normalizes ticker input and switches intraday controls to date-time', () => {
    mockApi()
    render(<App />)
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: '  aapl  ' },
    })
    fireEvent.change(screen.getByLabelText('Chart interval'), {
      target: { value: '5min' },
    })
    expect(screen.getByLabelText('Reference from')).toHaveAttribute(
      'type',
      'datetime-local',
    )
    expect(
      (screen.getByLabelText('Reference from') as HTMLInputElement).value,
    ).toContain('T09:30')
  })

  it('validates empty ticker, reference range, and search range', () => {
    mockApi()
    render(<App />)
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: '   ' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a stock ticker')

    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: 'AAPL' },
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

    fireEvent.change(screen.getByLabelText('Reference from'), {
      target: { value: '2025-01-01' },
    })
    fireEvent.change(screen.getByLabelText('Reference to'), {
      target: { value: '2025-01-02' },
    })
    fireEvent.change(screen.getByLabelText('Search from'), {
      target: { value: '2025-02-02' },
    })
    fireEvent.change(screen.getByLabelText('Search to'), {
      target: { value: '2025-02-01' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Search start')
  })

  it('shows loading then renders exact response data and context', async () => {
    let resolveMarket!: (value: ReturnType<typeof response>) => void
    const marketPromise = new Promise<ReturnType<typeof response>>(
      (resolve) => {
        resolveMarket = resolve
      },
    )
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) =>
        String(input).includes('/health')
          ? Promise.resolve(response({ status: 'ok' }))
          : marketPromise,
      ),
    )
    render(<App />)
    setValidDates()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(screen.getByText('Loading reference chart')).toBeInTheDocument()
    resolveMarket(response(success()))
    expect(
      await screen.findByRole('img', { name: 'NVDA closing price chart' }),
    ).toBeInTheDocument()
    expect(screen.getByText('1 day interval · 2 bars')).toBeInTheDocument()
    expect(screen.getByText('Future search period')).toBeInTheDocument()
  })

  it.each([
    ['INVALID_SYMBOL', 'could not find that ticker'],
    ['NO_DATA', 'No price data was found'],
    ['PROVIDER_NOT_CONFIGURED', 'Live market data is not configured yet'],
    ['PROVIDER_RATE_LIMITED', 'market-data limit was reached'],
    ['PROVIDER_UNAVAILABLE', 'temporarily unavailable'],
  ])('maps %s to a safe message', async (code, message) => {
    mockApi(
      response({ error: { code, message: 'raw internal detail' } }, false, 503),
    )
    render(<App />)
    setValidDates()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByText('raw internal detail')).not.toBeInTheDocument()
  })

  it('keeps search dates out of the market-data request and preserves them in context', async () => {
    const fetchMock = mockApi()
    render(<App />)
    setValidDates()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    await screen.findByRole('img', { name: 'NVDA closing price chart' })
    const marketUrl = String(
      fetchMock.mock.calls.find(([url]) =>
        String(url).includes('/market-data/'),
      )?.[0],
    )
    expect(marketUrl).not.toContain('2020-01-01')
    expect(screen.getByText(/Jan 1, 2020/)).toBeInTheDocument()
  })

  it('replaces the previous reference after inputs change', async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input)
      if (url.includes('/health'))
        return Promise.resolve(response({ status: 'ok' }))
      return Promise.resolve(
        response(success(url.includes('/MSFT') ? 'MSFT' : 'NVDA')),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    setValidDates()
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    await screen.findByRole('img', { name: 'NVDA closing price chart' })
    fireEvent.change(screen.getByLabelText('Stock ticker'), {
      target: { value: 'msft' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'Load reference chart' }),
    )
    expect(
      await screen.findByRole('img', { name: 'MSFT closing price chart' }),
    ).toBeInTheDocument()
    await waitFor(() =>
      expect(
        screen.queryByRole('img', { name: 'NVDA closing price chart' }),
      ).not.toBeInTheDocument(),
    )
  })
})
