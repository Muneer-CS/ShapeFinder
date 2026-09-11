import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => vi.restoreAllMocks())

describe('App', () => {
  it('shows a successful backend connection', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            status: 'ok',
            service: 'shape-finder-api',
            version: '0.1.0',
          }),
      }),
    )
    render(<App />)
    expect(await screen.findByText('API connected')).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: /find the shape/i }),
    ).toBeInTheDocument()
  })

  it('reports an unavailable backend', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(<App />)
    expect(await screen.findByText('API unavailable')).toBeInTheDocument()
  })
})
