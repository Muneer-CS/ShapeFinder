/// <reference types="node" />
import { webcrypto } from 'node:crypto'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../App'
import { pointField, regionField } from '../test/workspaceControls'
import type { ScanRequest } from '../api/patternScans'
import { patternHash } from '../pattern/model'
import { imageFile, mockLocalImages } from '../test/localImageFixture'

const caps = {
  intervals: [
    {
      interval: '1day',
      available: true,
      status: 'configured_but_unverified',
      reason: 'configured_policy_accepted',
      timing_basis: 'operator_configured',
      provider_timing_guarantee_verified: false,
      unfinished_available: false,
      unfinished_reason: 'snapshot_timing_unverified',
    },
  ],
  calendar_version: 'mock',
  hydration_configured: true,
  accept_configured_policies: true,
}
const json = (value: unknown) =>
  ({ ok: true, status: 200, json: async () => value }) as Response
function api(
  post?: (request: ScanRequest, signal: AbortSignal) => Promise<Response>,
  available = true,
) {
  const requests: ScanRequest[] = []
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/capabilities'))
      return json({
        ...caps,
        intervals: caps.intervals.map((c) => ({ ...c, available })),
      })
    if (url.endsWith('/pattern-scans') && options?.method === 'POST') {
      const request = JSON.parse(String(options.body)) as ScanRequest
      requests.push(request)
      if (post) return post(request, options.signal as AbortSignal)
      return json({
        scan_id: 'mock',
        revisions: request.revisions,
        pattern_content_hash: await patternHash(request.pattern),
        settings: request.settings,
        cutoff: { cutoff_at: '2025-01-07T22:00:00Z' },
        outcome: 'evaluated',
        matches: [],
        coverage: {
          universe_total: 1,
          scanned: 1,
          eligible: 1,
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
        universe_snapshot: null,
        capability: caps.intervals[0],
        reason_counts: {},
      })
    }
    throw new Error('Unexpected request; interpretation must be local.')
  })
  vi.stubGlobal('fetch', fetch)
  return { fetch, requests }
}
function describe(source = 'up 3, down 1, up 2') {
  fireEvent.click(screen.getByRole('button', { name: 'Describe' }))
  fireEvent.change(screen.getByLabelText('Pattern description'), {
    target: { value: source },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))
}
async function accept() {
  fireEvent.click(screen.getByRole('button', { name: 'Accept preview' }))
  await screen.findByText(/Preview accepted\./)
}
const pointLabels = () =>
  screen
    .getAllByRole('button', { name: /^Point \d+, x/ })
    .map((p) => p.getAttribute('aria-label'))
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  class TestPointerEvent extends MouseEvent {
    pointerId: number
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init)
      this.pointerId = init.pointerId ?? 1
    }
  }
  vi.stubGlobal('PointerEvent', TestPointerEvent)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('generates a visible editable text preview, requires acceptance, and maps its exact acknowledgement', async () => {
  const { fetch, requests } = api()
  render(<App />)
  describe()
  expect(
    screen.getByRole('button', { name: /Point 2, x 0.500, y 1.000/ }),
  ).toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  expect(fetch).toHaveBeenCalledTimes(1)
  fireEvent.focus(screen.getByRole('button', { name: /Point 2, x/ }))
  fireEvent.change(pointField('Y shape units'), {
    target: { value: '1.25' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Apply coordinates' }))
  await accept()
  fireEvent.change(screen.getByLabelText('Minimum similarity'), {
    target: { value: '0' },
  })
  expect(screen.getByText(/Preview accepted\./)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await screen.findByText('Scan complete.')
  expect(requests).toHaveLength(1)
  expect(requests[0].pattern.provenance.kind).toBe('text')
  expect(requests[0].pattern.provenance.grammar_version).toBe(1)
  expect(requests[0].pattern.points[1].y).toBe(1.25)
  expect(requests[0].preview_acknowledgement).toEqual({
    pattern_revision: requests[0].revisions.pattern_revision,
    content_hash: await patternHash(requests[0].pattern),
  })
  expect(requests[0].revisions.settings_revision).toBe(2)
  expect(requests[0].settings.minimum_similarity).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: 'Draw' }))
  fireEvent.keyDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    key: 'ArrowUp',
  })
  expect(screen.getByText('Applied pattern · text')).toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  await accept()
  expect(screen.getByRole('button', { name: 'Run current scan' })).toBeEnabled()
  expect(fetch).toHaveBeenCalledTimes(2)
})
it('marks edited text out of date across navigation and never replaces applied geometry without acceptance', async () => {
  const { fetch } = api()
  render(<App />)
  describe()
  await accept()
  const original = pointLabels()
  fireEvent.change(screen.getByLabelText('Pattern description'), {
    target: { value: 'down 1, up 3' },
  })
  expect(
    screen.getByText(/Source changed. This interpretation is out of date/),
  ).toBeInTheDocument()
  expect(pointLabels()).toEqual(original)
  expect(screen.getByRole('button', { name: 'Accept preview' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Draw' }))
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Describe' }))
  fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))
  expect(pointLabels()).not.toEqual(original)
  fireEvent.click(screen.getByRole('button', { name: 'Cancel preview' }))
  expect(pointLabels()).toEqual(original)
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('retains separate drafts, rejects unsupported text, and leaves unavailable scans disabled after acceptance', async () => {
  const { fetch } = api(undefined, false)
  render(<App />)
  describe('up 3, surprise 1')
  expect(screen.getByText(/Segment 2 must be/)).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Pattern description'), {
    target: { value: 'flat 1, down 2, up 2' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Generate preview' }))
  const before = pointLabels()
  fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
  expect(screen.getByLabelText('Choose chart image')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Describe' }))
  expect(pointLabels()).toEqual(before)
  await accept()
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('traces a cropped local image into exact canonical points and keeps geometry aligned through zoom and resize', async () => {
  const { fetch, requests } = api(),
    images = mockLocalImages()
  const { unmount } = render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
  fireEvent.change(screen.getByLabelText('Choose chart image'), {
    target: { files: [imageFile()] },
  })
  await waitFor(() => expect(images.images).toHaveLength(1))
  act(() => images.images[0].onload!())
  await screen.findByText(/Local image: 1000/)
  for (const [label, value] of [
    ['Left', '10'],
    ['Right', '90'],
    ['Top', '20'],
    ['Bottom', '80'],
  ])
    fireEvent.change(regionField(`${label} bound (%)`), {
      target: { value },
    })
  fireEvent.click(screen.getByRole('button', { name: 'Apply chart region' }))
  fireEvent.click(
    screen.getByRole('button', { name: 'Create tracing preview' }),
  )
  const svg = screen.getByRole('group', {
    name: 'Editable pattern drawing area',
  })
  let width = 1048
  vi.spyOn(svg, 'getBoundingClientRect').mockImplementation(
    () => ({ left: 0, top: 0, width, height: 448 }) as DOMRect,
  )
  expect(screen.getByTestId('trace-image')).toHaveAttribute('x', '-125')
  expect(screen.getByTestId('trace-image')).toHaveAttribute(
    'href',
    'blob:local-fixture-1',
  )
  fireEvent.click(screen.getByRole('button', { name: 'Trace pattern' }))
  fireEvent.pointerDown(svg, { clientX: 24, clientY: 124, button: 0 })
  fireEvent.pointerMove(svg, { clientX: 274, clientY: 324 })
  fireEvent.pointerUp(svg, { clientX: 1024, clientY: 224 })
  expect(pointLabels()).toEqual([
    'Point 1, x 0.000, y 0.5000',
    'Point 2, x 0.250, y -0.5000',
    'Point 3, x 1.000, y 0.000',
  ])
  const labels = pointLabels(),
    imageX = screen.getByTestId('trace-image').getAttribute('x')
  fireEvent.change(screen.getByLabelText('Trace zoom'), {
    target: { value: '2' },
  })
  width = 524
  fireEvent(window, new Event('resize'))
  expect(pointLabels()).toEqual(labels)
  expect(screen.getByTestId('trace-image')).toHaveAttribute('x', imageX)
  expect(screen.getByRole('button', { name: 'Trace pattern' })).toBeDisabled()
  await accept()
  expect(fetch).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await screen.findByText('Scan complete.')
  expect(requests[0].pattern.points).toEqual([
    { x: 0, y: 0.5 },
    { x: 0.25, y: -0.5 },
    { x: 1, y: 0 },
  ])
  expect(requests[0].pattern.provenance.kind).toBe('screenshot_trace')
  expect(JSON.stringify(requests[0])).not.toMatch(
    /blob:|private-chart-name|image\/png|filename/,
  )
  fireEvent.change(screen.getByLabelText('Trace zoom'), {
    target: { value: '1' },
  })
  expect(screen.getByText(/Preview accepted\./)).toBeInTheDocument()
  const resizedSvg = screen.getByRole('group', {
    name: 'Editable pattern drawing area',
  })
  vi.spyOn(resizedSvg, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    width: 1048,
    height: 448,
  } as DOMRect)
  fireEvent.pointerDown(screen.getByRole('button', { name: /Point 2, x/ }), {
    clientX: 274,
    clientY: 324,
    button: 0,
  })
  fireEvent.pointerMove(resizedSvg, { clientX: 524, clientY: 124 })
  fireEvent(window, new Event('resize'))
  fireEvent.pointerUp(resizedSvg, { clientX: 524, clientY: 124 })
  expect(pointLabels()).toEqual(labels)
  expect(screen.getByText(/Preview accepted\./)).toBeInTheDocument()
  fireEvent.change(regionField('Left bound (%)'), {
    target: { value: '20' },
  })
  expect(pointLabels()).toEqual(labels)
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
  expect(screen.queryByTestId('trace-image')).not.toBeInTheDocument()
  unmount()
  expect(images.revoke).toHaveBeenCalledExactlyOnceWith('blob:local-fixture-1')
})
it('keeps the applied pattern through cancelled and failed image replacement and releases discarded URLs', async () => {
  api()
  const images = mockLocalImages()
  const { unmount } = render(<App />)
  describe()
  await accept()
  const original = pointLabels()
  fireEvent.click(screen.getByRole('button', { name: 'Upload' }))
  fireEvent.change(screen.getByLabelText('Choose chart image'), {
    target: { files: [imageFile()] },
  })
  await waitFor(() => expect(images.images).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: 'Cancel image import' }))
  expect(pointLabels()).toEqual(original)
  expect(screen.getByRole('button', { name: 'Run current scan' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Choose chart image'), {
    target: { files: [imageFile()] },
  })
  await waitFor(() => expect(images.images).toHaveLength(2))
  act(() => images.images[1].onerror!())
  await screen.findByText(/image could not be decoded/)
  expect(pointLabels()).toEqual(original)
  fireEvent.change(screen.getByLabelText('Choose chart image'), {
    target: { files: [imageFile()] },
  })
  await waitFor(() => expect(images.images).toHaveLength(3))
  act(() => images.images[2].onload!())
  await screen.findByRole('button', { name: 'Create tracing preview' })
  fireEvent.click(
    screen.getByRole('button', { name: 'Create tracing preview' }),
  )
  fireEvent.click(screen.getByRole('button', { name: 'Cancel preview' }))
  expect(pointLabels()).toEqual(original)
  expect(screen.getByRole('button', { name: 'Run current scan' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Choose chart image'), {
    target: { files: [imageFile()] },
  })
  await waitFor(() => expect(images.images).toHaveLength(4))
  act(() => images.images[3].onload!())
  await waitFor(() =>
    expect(images.revoke).toHaveBeenCalledWith('blob:local-fixture-3'),
  )
  unmount()
  expect(images.revoke).toHaveBeenCalledTimes(4)
})
it('cancels an old scan on source input changes and ignores its late reply without restoring acceptance', async () => {
  let resolve!: (response: Response) => void, signal: AbortSignal | undefined
  api(async (_request, requestSignal) => {
    signal = requestSignal
    return new Promise((done) => {
      resolve = done
    })
  })
  render(<App />)
  describe()
  await accept()
  fireEvent.click(screen.getByRole('button', { name: 'Run current scan' }))
  await waitFor(() => expect(signal).toBeDefined())
  fireEvent.change(screen.getByLabelText('Pattern description'), {
    target: { value: 'down 2, up 1' },
  })
  expect(signal?.aborted).toBe(true)
  await act(async () => resolve(json({})))
  expect(screen.queryByText('Scan complete.')).not.toBeInTheDocument()
  expect(screen.queryByText(/Preview accepted\./)).not.toBeInTheDocument()
  expect(
    screen.getByRole('button', { name: 'Run current scan' }),
  ).toBeDisabled()
})
