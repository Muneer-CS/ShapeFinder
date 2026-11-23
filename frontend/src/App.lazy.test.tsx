import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const chartImport = vi.hoisted(() => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  return { gate, release }
})

vi.mock('./ChartVisuals', async (importOriginal) => {
  await chartImport.gate
  return importOriginal()
})

import App from './LegacyApp'

afterEach(() => vi.unstubAllGlobals())

it('shows the loading fallback until the real lazy chart module resolves', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: string | URL | Request) => {
      const url = String(input)
      const body = url.includes('/health')
        ? { status: 'ok' }
        : url.endsWith('/universes')
          ? { universes: [] }
          : {
              symbol: 'NVDA',
              interval: '1day',
              timezone: 'America/New_York',
              bars: [100, 102].map((price, index) => ({
                timestamp: `2025-01-0${index + 2}T00:00:00-05:00`,
                open: String(price),
                high: String(price),
                low: String(price),
                close: String(price),
                volume: '1000',
              })),
            }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
    }),
  )
  render(<App />)
  fireEvent.change(screen.getByLabelText('Reference from'), {
    target: { value: '2025-01-01' },
  })
  fireEvent.change(screen.getByLabelText('Reference to'), {
    target: { value: '2025-01-04' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Load reference chart' }))
  expect(await screen.findByText('Preparing chart…')).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  await act(async () => {
    chartImport.release()
    // Flush the pending import explicitly, rather than imposing a wall-clock race.
    await import('./ChartVisuals')
  })
  expect(
    screen.getByRole('img', { name: 'NVDA reference closing price chart' }),
  ).toBeInTheDocument()
  expect(screen.queryByText('Preparing chart…')).not.toBeInTheDocument()
})
