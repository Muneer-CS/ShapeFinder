import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Broken(): never {
  throw new Error('sensitive rendering detail')
}

afterEach(() => vi.restoreAllMocks())

describe('ErrorBoundary', () => {
  it('renders a safe, usable fallback for rendering exceptions', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(
      screen.queryByText('sensitive rendering detail'),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Reload application' }),
    ).toBeEnabled()
  })
})
