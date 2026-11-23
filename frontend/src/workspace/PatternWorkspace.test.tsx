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
import { pointField } from '../test/workspaceControls'
import type { Capabilities, ScanRequest, ScanResult } from '../api/patternScans'
import { patternHash } from '../pattern/model'

const capability = {
  interval: '1day',
  available: true,
  status: 'configured_but_unverified' as const,
  reason: 'configured_policy_accepted',
  timing_basis: 'operator_configured' as const,
  provider_timing_guarantee_verified: false,
  unfinished_available: false,
  unfinished_reason: 'snapshot_timing_unverified',
}
const capabilities: Capabilities = {
  intervals: [capability],
  calendar_version: 'fixture',
  hydration_configured: true,
  accept_configured_policies: true,
}
const prices = [100, 105, 95, 110]
const history = {
  symbol: 'AAPL',
  interval: '1day',
  timezone: 'America/New_York',
  bars: prices.map((price, i) => ({
    timestamp: `2025-01-0${i + 2}T14:30:00Z`,
    open: String(price),
    high: String(price),
    low: String(price),
    close: String(price),
    volume: '100',
  })),
}
const json = (body: unknown, status = 200) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }) as Response
async function resultFor(
  request: ScanRequest,
  overrides: Partial<ScanResult> = {},
): Promise<ScanResult> {
  return {
    scan_id: 'fixture-scan',
    revisions: request.revisions,
    pattern_content_hash: await patternHash(request.pattern),
    settings: request.settings,
    outcome: 'evaluated',
    cutoff: { cutoff_at: '2025-01-07T22:00:00Z' },
    matches: [],
    coverage: {
      universe_total: 2,
      scanned: 2,
      eligible: 2,
      stale: 0,
      skipped: 0,
      failed: 0,
      not_evaluated: 0,
      evaluation_incomplete: 0,
      matches_returned: 0,
    },
    execution: {
      partial: false,
      stop_reasons: [],
      provider_requests: 0,
      hypotheses_evaluated: 1,
    },
    provider_usage: {
      http_attempts: 0,
      documented_credit_cost: 0,
      unknown_cost_attempts: 0,
      actual_credits_consumed: null,
    },
    capability,
    universe_snapshot: null,
    reason_counts: {},
    ...overrides,
  }
}
function mockApi(
  post?: (request: ScanRequest, signal: AbortSignal) => Promise<Response>,
  caps = capabilities,
) {
  const mock = vi.fn(
    async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/pattern-scans/capabilities')) return json(caps)
      if (url.includes('/market-data/AAPL?')) return json(history)
      if (url.endsWith('/pattern-scans') && init?.method === 'POST') {
        const request = JSON.parse(String(init.body)) as ScanRequest
        return post
          ? post(request, init.signal as AbortSignal)
          : json(await resultFor(request))
      }
      throw new Error(`Unexpected mocked request: ${url}`)
    },
  )
  vi.stubGlobal('fetch', mock)
  return mock
}
async function importPattern() {
  fireEvent.click(screen.getByRole('button', { name: 'Stock chart' }))
  fireEvent.change(screen.getByLabelText('Reference symbol'), {
    target: { value: 'aapl' },
  })
  fireEvent.change(screen.getByLabelText('History start'), {
    target: { value: '2025-01-01' },
  })
  fireEvent.change(screen.getByLabelText('History end'), {
    target: { value: '2025-01-06' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Load stock history' }))
  await screen.findByRole('img', { name: 'AAPL closing price selection chart' })
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected section' }),
  )
  await waitFor(() =>
    expect(
      screen.getByRole('button', { name: 'Run current scan' }),
    ).toBeEnabled(),
  )
}
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  // JSDOM lacks PointerEvent. Preserve coordinates and pointer identity in tests.
  class TestPointerEvent extends MouseEvent {
    pointerId: number
    pointerType: string
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 1
      this.pointerType = init.pointerType ?? 'mouse'
    }
  }
  vi.stubGlobal('PointerEvent', TestPointerEvent)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('uses trading-session presets and blocks invalid ranges beside their fields', async () => {
  const fetch = mockApi(undefined, {
    ...capabilities,
    intervals: [{ ...capability, reason: 'massive_daily_cache' }],
  })
  render(<App />)
  const minimum = screen.getByLabelText('Minimum candles')
  const maximum = screen.getByLabelText('Maximum candles')
  expect(minimum).toHaveValue(20)
  expect(maximum).toHaveValue(20)
  expect(screen.getByLabelText('Stock universe')).toHaveValue('nasdaq')
  const presets = screen.getByRole('group', { name: 'Daily duration presets' })
  expect(
    within(presets).getByRole('button', { name: '20 trading sessions' }),
  ).toHaveAttribute('aria-pressed', 'true')
  await importPattern()
  const run = screen.getByRole('button', { name: 'Run current scan' })
  fireEvent.click(
    within(presets).getByRole('button', { name: '63 trading sessions' }),
  )
  expect(minimum).toHaveValue(63)
  expect(maximum).toHaveValue(63)
  fireEvent.change(maximum, { target: { value: '19' } })
  expect(maximum).toHaveAccessibleDescription(
    /Maximum duration must be at least the minimum/,
  )
  expect(minimum).toHaveAttribute('aria-invalid', 'true')
  expect(run).toBeDisabled()
  fireEvent.change(minimum, { target: { value: '' } })
  expect(minimum).toHaveAccessibleDescription(/Enter a whole number/)
  fireEvent.change(minimum, { target: { value: '17' } })
  expect(minimum).toHaveAttribute('aria-invalid', 'false')
  expect(run).toBeEnabled()
  fireEvent.click(run)
  await waitFor(() => {
    const sent = fetch.mock.calls.find(
      ([, options]) => options?.method === 'POST',
    )
    expect(JSON.parse(String(sent?.[1]?.body)).settings.duration).toEqual({
      minimum_candles: 17,
      maximum_candles: 19,
    })
  })
})

it('starts a shape from the keyboard and preserves undo, redo and clear', () => {
  mockApi()
  render(<App />)
  fireEvent.click(screen.getByText('Point coordinates and keyboard tools'))
  fireEvent.click(screen.getByRole('button', { name: 'Start with two points' }))
  const second = screen.getByRole('button', { name: /Point 2, x/ })
  fireEvent.keyDown(second, { key: 'ArrowUp', shiftKey: true })
  expect(second).toHaveAccessibleName(/y 0.1000/)
  fireEvent.keyDown(second, { key: 'z', ctrlKey: true })
  expect(second).toHaveAccessibleName(/y 0.000/)
  fireEvent.keyDown(second, { key: 'z', ctrlKey: true, shiftKey: true })
  expect(second).toHaveAccessibleName(/y 0.1000/)
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
  expect(
    screen.queryByRole('button', { name: /Point 2, x/ }),
  ).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
  expect(
    screen.getByRole('button', { name: /Point 2, x.*y 0.1000/ }),
  ).toBeInTheDocument()
})

it('fetches only capabilities at startup, keeps editing available, and never offers unsupported unfinished candles', async () => {
  const fetch = mockApi(undefined, {
    ...capabilities,
    intervals: [
      {
        ...capability,
        available: false,
        status: 'unavailable',
        reason: 'unknown_capability',
      },
    ],
  })
  render(<App />)
  expect(
    await screen.findByText(/Scanning unavailable: Candle timing/),
  ).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  expect(screen.getByLabelText('Include unfinished candle')).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Draw pattern' })).toBeEnabled()
  expect(screen.queryByText('Search range')).not.toBeInTheDocument()
})
it('keeps editing usable after a capability network failure', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockRejectedValue(new Error('offline fixture')),
  )
  render(<App />)
  expect(
    await screen.findByText(/availability could not be checked/),
  ).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Draw pattern' })).toBeEnabled()
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
})
it('draws upward, edits endpoints and interior points, undoes one gesture, and preserves coordinates on resize', async () => {
  mockApi()
  render(<App />)
  const svg = screen.getByRole('group', {
    name: 'Editable pattern drawing area',
  })
  let width = 1048
  vi.spyOn(svg, 'getBoundingClientRect').mockImplementation(() => ({
    left: 0,
    top: 0,
    width,
    height: 448,
    right: width,
    bottom: 448,
    x: 0,
    y: 0,
    toJSON() {},
  }))
  fireEvent.click(screen.getByRole('button', { name: 'Draw pattern' }))
  fireEvent.pointerDown(svg, {
    clientX: 24,
    clientY: 324,
    button: 0,
    pointerId: 7,
    pointerType: 'touch',
  })
  fireEvent.pointerMove(svg, {
    clientX: 524,
    clientY: 124,
    pointerId: 7,
    pointerType: 'touch',
  })
  fireEvent.pointerUp(svg, {
    clientX: 1024,
    clientY: 224,
    pointerId: 7,
    pointerType: 'touch',
  })
  expect(
    screen.getByRole('button', { name: /Point 1, x 0.000, y -0.5000/ }),
  ).toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: /Point 2, x 0.500, y 0.5000/ }),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
  expect(
    screen.queryByRole('button', { name: /Point 1, x/ }),
  ).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Redo' }))
  fireEvent.focus(screen.getByRole('button', { name: /Point 2, x/ }))
  fireEvent.change(pointField('X position'), {
    target: { value: '.6' },
  })
  fireEvent.change(pointField('Y shape units'), {
    target: { value: '.8' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Apply coordinates' }))
  const interior = screen.getByRole('button', {
    name: /Point 2, x 0.600, y 0.8000/,
  })
  fireEvent.keyDown(interior, { key: 'ArrowUp' })
  expect(pointField('Y shape units')).toHaveValue(0.81)
  fireEvent.keyDown(screen.getByRole('button', { name: /Point 1, x/ }), {
    key: 'ArrowLeft',
  })
  expect(pointField('X position')).toHaveValue(0.6)
  width = 524
  fireEvent(window, new Event('resize'))
  expect(
    screen.getByRole('button', { name: /Point 2, x 0.600, y 0.8100/ }),
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Remove point' }))
  expect(screen.getAllByRole('button', { name: /Point \d, x/ })).toHaveLength(2)
  fireEvent.click(screen.getByRole('button', { name: 'Add midpoint' }))
  expect(screen.getAllByRole('button', { name: /Point \d, x/ })).toHaveLength(3)
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
  fireEvent.click(screen.getByRole('button', { name: 'Reset pattern' }))
  expect(screen.getAllByRole('button', { name: /Point \d, x/ })).toHaveLength(3)
})
it('discards cancelled, backwards and zero-width strokes without changing the pattern', () => {
  mockApi()
  render(<App />)
  const svg = screen.getByRole('group', {
    name: 'Editable pattern drawing area',
  })
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    width: 1048,
    height: 448,
  } as DOMRect)
  fireEvent.click(screen.getByRole('button', { name: 'Draw pattern' }))
  fireEvent.pointerDown(svg, { clientX: 500, clientY: 200, button: 0 })
  fireEvent.pointerMove(svg, { clientX: 300, clientY: 100 })
  fireEvent.pointerUp(svg, { clientX: 900, clientY: 200 })
  expect(
    screen.getByText(/Stroke discarded: draw from left/),
  ).toBeInTheDocument()
  fireEvent.pointerDown(svg, { clientX: 500, clientY: 200, button: 0 })
  fireEvent.pointerUp(svg, { clientX: 500, clientY: 100 })
  expect(screen.getByText(/Stroke discarded: draw across/)).toBeInTheDocument()
  fireEvent.pointerDown(svg, { clientX: 100, clientY: 200, button: 0 })
  fireEvent.pointerCancel(svg)
  expect(screen.getByText(/Gesture cancelled/)).toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: /Point 1, x/ }),
  ).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
})
it('imports the selected actual closes and sends exact settings only on manual scan', async () => {
  const fetch = mockApi()
  render(<App />)
  await importPattern()
  const chart = screen.getByRole('img', {
    name: 'AAPL closing price selection chart',
  })
  const observations = chart.querySelectorAll('rect[fill="transparent"]')
  fireEvent.click(observations[1])
  fireEvent.click(observations[3])
  expect(screen.getByLabelText('Start observation')).toHaveValue('1')
  expect(screen.getByLabelText('End observation')).toHaveValue('3')
  fireEvent.change(screen.getByLabelText('Start observation'), {
    target: { value: '1' },
  })
  fireEvent.click(
    screen.getByRole('button', { name: 'Import selected section' }),
  )
  fireEvent.change(screen.getByLabelText('Stock universe'), {
    target: { value: 'custom' },
  })
  fireEvent.change(screen.getByLabelText('Custom symbols (up to 10)'), {
    target: { value: ' aapl, MSFT aapl ' },
  })
  fireEvent.change(screen.getByLabelText('Minimum similarity'), {
    target: { value: '0' },
  })
  fireEvent.change(screen.getByLabelText('Minimum completion (%)'), {
    target: { value: '60' },
  })
  fireEvent.change(screen.getByLabelText('Minimum candles'), {
    target: { value: '15' },
  })
  fireEvent.change(screen.getByLabelText('Maximum candles'), {
    target: { value: '60' },
  })
  fireEvent.change(screen.getByLabelText('Minimum observed movement (%)'), {
    target: { value: '0' },
  })
  fireEvent.change(screen.getByLabelText('Match type'), {
    target: { value: 'forming' },
  })
  expect(fetch).toHaveBeenCalledTimes(2)
  const historyUrl = new URL(String(fetch.mock.calls[1][0]))
  expect(historyUrl.searchParams.get('start')).toBe('2025-01-01T00:00:00Z')
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(await screen.findByText('Scan complete.')).toBeInTheDocument()
  const request = JSON.parse(
    String(fetch.mock.calls[2][1]?.body),
  ) as ScanRequest
  expect(request.pattern.points).toEqual([
    { x: 0, y: 0 },
    { x: 0.5, y: Math.log(95 / 105) },
    { x: 1, y: Math.log(110 / 105) },
  ])
  expect(request.pattern.provenance.source_start_at).toBe(
    history.bars[1].timestamp,
  )
  expect(request.universe).toEqual({
    kind: 'custom',
    symbols: ['AAPL', 'MSFT'],
  })
  expect(request.preview_acknowledgement).toBeNull()
  expect(request.settings).toEqual({
    interval: '1day',
    duration: { minimum_candles: 15, maximum_candles: 60 },
    minimum_completion: 0.6,
    minimum_movement_percent: 0,
    minimum_similarity: 0,
    match_type: 'forming',
    session: 'regular',
    include_unfinished: false,
    top_n: 20,
  })
  expect(request.revisions.pattern_revision).toBe(3)
  expect(request.revisions.settings_revision).toBe(9)
  expect(screen.getByText(/No matches met these settings/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Minimum similarity'), {
    target: { value: '80' },
  })
  expect(screen.getByText('Previous scan')).toBeInTheDocument()
})

it('rejects malformed replies and replies carrying the wrong request identity', async () => {
  let malformed = true
  mockApi(async (request) =>
    json(
      malformed
        ? { ...(await resultFor(request)), provider_usage: null }
        : {
            ...(await resultFor(request)),
            pattern_content_hash: '0'.repeat(64),
          },
    ),
  )
  render(<App />)
  await importPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'invalid scan result',
  )
  malformed = false
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'does not match the submitted pattern',
  )
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
})

it('shows an empty partial scan as partial coverage rather than a full-universe no-match', async () => {
  mockApi(async (request) =>
    json(
      await resultFor(request, {
        outcome: 'partial',
        coverage: {
          universe_total: 10,
          scanned: 2,
          eligible: 2,
          stale: 0,
          skipped: 0,
          failed: 0,
          not_evaluated: 8,
          evaluation_incomplete: 0,
          matches_returned: 0,
        },
        execution: {
          partial: true,
          stop_reasons: ['provider_budget'],
          provider_requests: 1,
          hypotheses_evaluated: 1,
        },
      }),
    ),
  )
  render(<App />)
  await importPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(
    await screen.findByText(
      'No matches among the evaluated stocks. The remaining universe was not fully evaluated.',
    ),
  ).toBeInTheDocument()
  expect(
    screen.getByText(/Stopped: Provider request budget reached/),
  ).toBeInTheDocument()
  expect(screen.getByText('0 matches shown.')).toBeInTheDocument()
  expect(screen.getByText(/8 unevaluated/)).toBeInTheDocument()
})

it('aborts a history load after input changes and ignores its late response', async () => {
  let resolve!: (value: Response) => void
  let signal: AbortSignal | undefined
  const fetch = vi.fn(async (input: string, init: RequestInit) => {
    if (input.endsWith('/capabilities')) return json(capabilities)
    signal = init.signal as AbortSignal
    return new Promise<Response>((done) => {
      resolve = done
    })
  })
  vi.stubGlobal('fetch', fetch)
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Stock chart' }))
  for (const [label, value] of [
    ['Reference symbol', 'AAPL'],
    ['History start', '2025-01-01'],
    ['History end', '2025-01-06'],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.click(screen.getByRole('button', { name: 'Load stock history' }))
  fireEvent.change(screen.getByLabelText('Reference symbol'), {
    target: { value: 'MSFT' },
  })
  expect(signal?.aborted).toBe(true)
  await act(async () => resolve(json(history)))
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(
    screen.queryByRole('button', { name: 'Import selected section' }),
  ).not.toBeInTheDocument()
})

it('validates numeric edits without clamping them silently, and preserves point order during drag', async () => {
  mockApi()
  render(<App />)
  await importPattern()
  fireEvent.focus(screen.getByRole('button', { name: /Point 2, x/ }))
  fireEvent.change(pointField('X position'), {
    target: { value: '1' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Apply coordinates' }))
  expect(screen.getByText(/Keep y between -1000 and 1000/)).toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: /Point 2, x 0.333/ }),
  ).toBeInTheDocument()
  const svg = screen.getByRole('group', {
    name: 'Editable pattern drawing area',
  })
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    width: 1048,
    height: 448,
  } as DOMRect)
  const beforeSelection = screen.getByText(/Pattern revision \d/).textContent
  const beforePoint = screen
    .getByRole('button', { name: /Point 2, x/ })
    .getAttribute('aria-label')
  fireEvent.pointerDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    clientX: 360,
    clientY: 153,
    button: 0,
    pointerId: 4,
  })
  fireEvent.pointerUp(svg, { clientX: 360, clientY: 153, pointerId: 4 })
  expect(screen.getByRole('button', { name: /Point 2, x/ })).toHaveAttribute(
    'aria-label',
    beforePoint,
  )
  expect(screen.getByText(/Pattern revision \d/)).toHaveTextContent(
    beforeSelection!,
  )
  fireEvent.pointerDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    clientX: 357,
    clientY: 150,
    button: 0,
    pointerId: 4,
  })
  fireEvent.pointerMove(svg, { clientX: 1024, clientY: 50, pointerId: 4 })
  fireEvent.pointerUp(svg, { clientX: 1024, clientY: 50, pointerId: 4 })
  expect(
    Number((pointField('X position') as HTMLInputElement).value),
  ).toBeLessThan(2 / 3)
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
  expect(
    screen.getByRole('button', { name: /Point 2, x 0.333/ }),
  ).toBeInTheDocument()
  fireEvent.focus(screen.getByRole('button', { name: /Point 1, x/ }))
  expect(pointField('X position')).toBeDisabled()
})
it('prevents duplicates, cancels and ignores late responses after edits or a newer scan', async () => {
  const pending: {
    request: ScanRequest
    signal: AbortSignal
    resolve: (response: Response) => void
  }[] = []
  const fetch = mockApi(
    (request, signal) =>
      new Promise((resolve) => pending.push({ request, signal, resolve })),
  )
  render(<App />)
  await importPattern()
  const run = screen.getByRole('button', { name: 'Run current scan' })
  fireEvent.click(run)
  fireEvent.click(run)
  await waitFor(() => expect(pending).toHaveLength(1))
  expect(fetch).toHaveBeenCalledTimes(3)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel scan' }))
  expect(pending[0].signal.aborted).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await waitFor(() => expect(pending).toHaveLength(2))
  await act(async () => {
    pending[1].resolve(json(await resultFor(pending[1].request)))
    pending[0].resolve(
      json(
        await resultFor(pending[0].request, {
          outcome: 'partial',
          execution: {
            partial: true,
            stop_reasons: ['provider_budget'],
            provider_requests: 1,
            hypotheses_evaluated: 1,
          },
        }),
      ),
    )
  })
  expect(await screen.findByText('Scan complete.')).toBeInTheDocument()
  expect(screen.queryByText(/Partial scan complete/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await waitFor(() => expect(pending).toHaveLength(3))
  fireEvent.keyDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    key: 'ArrowUp',
  })
  expect(pending[2].signal.aborted).toBe(true)
  await act(async () => {
    pending[2].resolve(
      json(await resultFor(pending[2].request, { outcome: 'partial' })),
    )
  })
  expect(screen.getByText('Previous scan')).toBeInTheDocument()
  expect(screen.queryByText(/Partial scan complete/)).not.toBeInTheDocument()
})
it('distinguishes unavailable and validation errors from a successful no-match scan', async () => {
  let status = 503
  mockApi(async () =>
    json(
      {
        error: {
          code:
            status === 503 ? 'CURRENT_SCAN_UNAVAILABLE' : 'VALIDATION_ERROR',
          message: 'Check settings.',
          reason: status === 503 ? 'universe_catalog_unavailable' : undefined,
        },
      },
      status,
    ),
  )
  render(<App />)
  await importPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Scan unavailable: The server has no cached stock catalogue.',
  )
  expect(screen.queryByText(/No matches met/)).not.toBeInTheDocument()
  status = 422
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Validation rejected: Check settings.',
  )
})
it('shows partial coverage and one row for each returned stock with distinct measurements', async () => {
  mockApi(async (request) => {
    const matches = ['AAPL', 'MSFT'].map((symbol) => ({
      symbol,
      similarity_score: 91.234,
      estimated_completion: 0.6,
      match_type: 'forming' as const,
      movement_percent: 7.12,
      observed_candles: 13,
      complete_duration_candles: 21,
      start_at: '2024-12-12T14:30:00Z',
      freshness: {
        cutoff_at: '2025-01-07T22:00:00Z',
        expected: {
          open_at: '2025-01-07T14:30:00Z',
          close_at: '2025-01-07T21:00:00Z',
          timezone: 'America/New_York',
        },
        retrieved_at: '2025-01-07T21:10:00Z',
        snapshot_at: null,
        is_complete: true,
      },
    }))
    return json(
      await resultFor(request, {
        outcome: 'partial',
        matches,
        coverage: {
          universe_total: 10,
          scanned: 2,
          eligible: 2,
          stale: 1,
          skipped: 0,
          failed: 0,
          not_evaluated: 7,
          evaluation_incomplete: 0,
          matches_returned: 2,
        },
        execution: {
          partial: true,
          stop_reasons: ['candidate_limit'],
          provider_requests: 0,
          hypotheses_evaluated: 10,
        },
      }),
    )
  })
  render(<App />)
  await importPattern()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  expect(
    await screen.findByText('Partial scan complete. Review coverage below.'),
  ).toBeInTheDocument()
  expect(screen.getByText(/2 of 10 stocks evaluated/)).toBeInTheDocument()
  const table = screen.getByRole('table')
  expect(within(table).getAllByRole('row')).toHaveLength(3)
  expect(within(table).getAllByText('91.2 / 100')).toHaveLength(2)
  expect(within(table).getAllByText('60.0%')).toHaveLength(2)
  expect(within(table).getAllByText('7.12%')).toHaveLength(2)
  expect(within(table).getAllByText('2025-01-07')).toHaveLength(2)
  fireEvent.change(screen.getByLabelText('Minimum completion (%)'), {
    target: { value: '0' },
  })
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  expect(screen.getByText(/must be greater than 0%/)).toBeInTheDocument()
})
