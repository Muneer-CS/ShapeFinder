/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ThemeControl } from './ThemeControl'
function device(dark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>()
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({
      matches: dark,
      addEventListener: (_kind: string, fn: (e: MediaQueryListEvent) => void) =>
        listeners.add(fn),
      removeEventListener: (
        _kind: string,
        fn: (e: MediaQueryListEvent) => void,
      ) => listeners.delete(fn),
    })),
  )
  return (matches: boolean) =>
    act(() => listeners.forEach((fn) => fn({ matches } as MediaQueryListEvent)))
}
function open() {
  fireEvent.click(screen.getByLabelText(/^Theme:/))
}
afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  delete document.documentElement.dataset.theme
})
it('follows device changes until explicitly chosen, saves, reloads and resets to device preference', () => {
  localStorage.clear()
  const change = device(true),
    { unmount } = render(<ThemeControl />)
  expect(document.documentElement.dataset.theme).toBe('dark')
  change(false)
  expect(document.documentElement.dataset.theme).toBe('light')
  open()
  fireEvent.click(screen.getByLabelText('Dark theme'))
  expect(localStorage.getItem('shapefinder.theme')).toBe('dark')
  change(false)
  expect(document.documentElement.dataset.theme).toBe('dark')
  unmount()
  render(<ThemeControl />)
  expect(document.documentElement.dataset.theme).toBe('dark')
  open()
  fireEvent.click(screen.getByLabelText('Use device theme'))
  expect(localStorage.getItem('shapefinder.theme')).toBeNull()
})
it('keeps an explicit in-memory choice when storage is unavailable', () => {
  device(false)
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('disabled')
  })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('disabled')
  })
  render(<ThemeControl />)
  open()
  fireEvent.click(screen.getByLabelText('Dark theme'))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(screen.getByLabelText('Dark theme')).toBeChecked()
})
it('closes the chooser with Escape and returns keyboard focus to the summary', () => {
  device(false)
  render(<ThemeControl />)
  open()
  fireEvent.keyDown(screen.getByLabelText('Dark theme'), { key: 'Escape' })
  const summary = screen.getByLabelText(/^Theme:/)
  expect(summary).toHaveFocus()
  expect(summary.closest('details')).not.toHaveAttribute('open')
})
it('uses the same preference before React starts, via a blocking same-origin bootstrap', () => {
  device(false)
  localStorage.setItem('shapefinder.theme', 'dark')
  const source = readFileSync('public/theme-init.js', 'utf8')
  // Executes the shipped bootstrap against this browser-like document.
  new Function(source)()
  expect(document.documentElement.dataset.theme).toBe('dark')
  const html = readFileSync('index.html', 'utf8')
  expect(html.indexOf('src="/theme-init.js"')).toBeLessThan(
    html.indexOf('<body>'),
  )
  expect(html).not.toContain('defer src="/theme-init.js"')
})
