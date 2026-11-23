/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'
import type { ScanRequest } from '../api/patternScans'
import { comparisonResult } from '../test/comparisonFixture'
const json = (body: unknown) =>
  ({ ok: true, status: 200, json: async () => body }) as Response
function fixtureApi(withCandles = false) {
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/capabilities'))
      return json({
        intervals: [
          {
            interval: '1day',
            available: true,
            status: 'configured_but_unverified',
            reason: 'fixture',
            timing_basis: 'operator_configured',
            provider_timing_guarantee_verified: false,
            unfinished_available: false,
            unfinished_reason: 'snapshot_timing_unverified',
          },
        ],
        calendar_version: 'fixture',
        hydration_configured: true,
        accept_configured_policies: true,
      })
    if (options?.method === 'POST') {
      const result = await comparisonResult(
        JSON.parse(String(options.body)) as ScanRequest,
      )
      if (withCandles) {
        result.reason_counts = { candidate_limit: 8 }
        for (const match of result.matches) {
          match.matched_start_session = '2025-01-01'
          match.last_candle_session = match.freshness.expected.open_at.slice(
            0,
            10,
          )
          for (const candle of match.comparison!.observations) {
            const close = Number(candle.close)
            Object.assign(candle, {
              open: (close + 1).toFixed(3),
              high: (close + 2).toFixed(3),
              low: (close - 2).toFixed(3),
              volume: '12345.50',
            })
          }
        }
      }
      return json(result)
    }
    throw new Error('Unexpected request')
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}
async function acceptedPattern() {
  fireEvent.click(screen.getByRole('button', { name: 'Describe' }))
  fireEvent.change(screen.getByLabelText('Pattern description'), {
    target: { value: 'points: 0:0, 0.5:1, 1:-2' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))
  fireEvent.click(screen.getByRole('button', { name: 'Accept preview' }))
  await screen.findByText(/Preview accepted\./)
}
async function go(hash: string) {
  await act(async () => {
    window.location.hash = hash
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}
beforeEach(() => {
  window.history.replaceState(null, '', '#scanner')
  vi.stubGlobal('crypto', webcrypto)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '#scanner')
  localStorage.clear()
})
it('preserves drafts, acceptance, results and selection through About and browser hash navigation', async () => {
  const fetch = fixtureApi()
  render(<App />)
  await acceptedPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await screen.findByText('Partial scan complete. Review coverage below.')
  fireEvent.click(screen.getByRole('button', { name: 'Compare MSFT' }))
  await go('#about')
  expect(
    screen.getByRole('heading', { name: 'Created by Muneer Mahmoud' }),
  ).toBeInTheDocument()
  for (const [name, url] of [
    ['Project repository', 'https://github.com/Muneer-CS/ShapeFinder'],
    ['Muneer Mahmoud on GitHub', 'https://github.com/Muneer-CS'],
    ['Muneer Mahmoud on LinkedIn', 'https://ca.linkedin.com/in/muneer-mahmoud'],
  ]) {
    const link = screen.getByRole('link', { name })
    expect(link).toHaveAttribute('href', url)
    expect(link.querySelector('svg')).not.toBeNull()
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  }
  await go('#scanner')
  expect(screen.getByLabelText('Pattern description')).toHaveValue(
    'points: 0:0, 0.5:1, 1:-2',
  )
  expect(screen.getByRole('button', { name: 'Compare MSFT' })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(screen.getByRole('button', { name: 'Run current scan' })).toBeEnabled()
  expect(fetch).toHaveBeenCalledTimes(2)
})
it('renders the result snapshot, never the subsequently edited pattern, and selects exact prices without fetching', async () => {
  const fetch = fixtureApi()
  render(<App />)
  await acceptedPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await screen.findByRole('img', {
    name: 'AAPL observed shape and reference comparison',
  })
  const selected = screen.getByRole('region', { name: 'AAPL comparison' }),
    chart = within(selected).getByRole('img'),
    observed = chart.querySelector('[data-series="observed"]')!
  const before = observed.getAttribute('d')
  const coordinates = (d: string) =>
    [...d.matchAll(/[ML]([^,]+),([^ ]+)/g)].map(([, x, y]) => [
      Number(x),
      Number(y),
    ])
  const bounds = chart.querySelector('clipPath rect')!
  const top = Number(bounds.getAttribute('y'))
  const bottom = top + Number(bounds.getAttribute('height'))
  const left = Number(bounds.getAttribute('x'))
  const width = Number(bounds.getAttribute('width'))
  const continuation = coordinates(
    chart.querySelector('.continuation-line')!.getAttribute('d')!,
  )
  expect(continuation).toHaveLength(2)
  expect(continuation.at(-1)![0]).toBe(left + width)
  for (const [, y] of continuation) {
    expect(y).toBeGreaterThan(top)
    expect(y).toBeLessThan(bottom)
  }
  expect(coordinates(before!).at(-1)![0]).toBe(left + width * 0.5)
  fireEvent.click(screen.getByRole('button', { name: 'Draw' }))
  expect(screen.getByText('Current scan')).toBeInTheDocument()
  expect(observed).toHaveAttribute('data-end-progress', '0.5')
  expect(
    within(selected).getByText(/Reference continuation, not a prediction/),
  ).toBeInTheDocument()
  fireEvent.keyDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    key: 'ArrowUp',
  })
  expect(screen.getByText('Previous scan')).toBeInTheDocument()
  expect(observed.getAttribute('d')).toBe(before)
  expect(
    within(selected).getByRole('img').querySelector('[data-series="observed"]'),
  ).toHaveAttribute('d', before)
  fireEvent.click(
    within(selected).getByRole('button', { name: 'Observed prices' }),
  )
  expect(
    await within(selected).findByRole('img', {
      name: 'AAPL observed closing prices',
    }),
  ).toBeInTheDocument()
  expect(
    within(selected).getByText(/Currency is not supplied/),
  ).toBeInTheDocument()
  expect(within(selected).getByText(/closing prices only/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Compare MSFT' }))
  const full = screen.getByRole('region', { name: 'MSFT comparison' })
  expect(
    within(full).getByRole('img').querySelector('[data-series="observed"]'),
  ).toHaveAttribute('data-end-progress', '1')
  expect(fetch).toHaveBeenCalledTimes(2)
})
it('shows exact dated candles and changes the matched viewport without fetching', async () => {
  const fetch = fixtureApi(true)
  render(<App />)
  await acceptedPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  const match = await screen.findByRole('region', { name: 'AAPL comparison' })
  expect(within(match).getByText(/Matched period/)).toHaveTextContent(
    '2025-01-01 to 2025-01-11',
  )
  expect(
    screen.getByRole('list', {
      name: 'Reasons stocks were excluded or unevaluated',
    }),
  ).toHaveTextContent('8 · Candidate limit reached')
  fireEvent.click(
    within(match).getByRole('button', { name: 'Observed prices' }),
  )
  const chart = within(match).getByRole('img', {
    name: /AAPL observed candlesticks/,
  })
  const readout = within(match).getByLabelText('Selected candle details')
  expect(readout).toHaveTextContent('2025-01-11')
  expect(readout).toHaveTextContent('12345.50')
  fireEvent.keyDown(chart, { key: 'ArrowLeft' })
  expect(readout).toHaveTextContent('2025-01-10')
  fireEvent.pointerOver(chart.querySelector('.candle-hit')!)
  expect(readout).toHaveTextContent('2025-01-01')
  expect(readout).toHaveTextContent('101.000')
  const control = (name: string) => within(match).getByRole('button', { name })
  fireEvent.click(control('Zoom in'))
  expect(within(match).getByText(/Showing 7 of 11/)).toHaveTextContent(
    '2025-01-03 to 2025-01-09',
  )
  fireEvent.click(control('Pan later'))
  expect(within(match).getByText(/Showing 7 of 11/)).toHaveTextContent(
    '2025-01-05 to 2025-01-11',
  )
  fireEvent.click(control('Pan earlier'))
  expect(within(match).getByText(/Showing 7 of 11/)).toHaveTextContent(
    '2025-01-02 to 2025-01-08',
  )
  fireEvent.click(control('Reset view'))
  expect(within(match).getByText(/Showing 11 of 11/)).toHaveTextContent(
    '2025-01-01 to 2025-01-11',
  )
  expect(fetch).toHaveBeenCalledTimes(2)
})
it('keeps the selected page when the skip link changes only the in-page anchor', async () => {
  fixtureApi()
  render(<App />)
  await go('#about')
  await go('#main-content')
  await waitFor(() =>
    expect(
      screen.getByRole('heading', { name: 'Created by Muneer Mahmoud' }),
    ).toBeInTheDocument(),
  )
})
