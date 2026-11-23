import { useEffect, useState } from 'react'
type Preference = 'system' | 'light' | 'dark'
const query = '(prefers-color-scheme: dark)'
function readPreference(): Preference {
  try {
    const value = localStorage.getItem('shapefinder.theme')
    if (value === 'dark' || value === 'light') return value
  } catch {
    /* Storage is optional. */
  }
  return 'system'
}
export function ThemeControl() {
  const [preference, setPreference] = useState(readPreference)
  const [deviceDark, setDeviceDark] = useState(
    () => window.matchMedia?.(query).matches ?? false,
  )
  const theme =
    preference === 'system' ? (deviceDark ? 'dark' : 'light') : preference
  useEffect(() => {
    const media = window.matchMedia?.(query)
    const changed = (event: MediaQueryListEvent) => setDeviceDark(event.matches)
    media?.addEventListener('change', changed)
    return () => media?.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    document.documentElement.style.colorScheme = theme
  }, [theme])
  function choose(value: Preference) {
    setPreference(value)
    try {
      if (value === 'system') localStorage.removeItem('shapefinder.theme')
      else localStorage.setItem('shapefinder.theme', value)
    } catch {
      /* Retain this session's in-memory selection. */
    }
  }
  return (
    <details
      className="theme-menu"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          event.currentTarget.open = false
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !event.currentTarget.open) return
        event.stopPropagation()
        event.currentTarget.open = false
        event.currentTarget.querySelector('summary')?.focus()
      }}
    >
      <summary
        aria-label={`Theme: ${theme}, ${preference === 'system' ? 'device preference' : 'selected preference'}`}
      >
        <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
          <path
            d={
              theme === 'dark'
                ? 'M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z'
                : 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10Zm0-5v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5'
            }
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
        </svg>
        <span>{theme === 'dark' ? 'Dark' : 'Light'}</span>
        <span aria-hidden="true">⌄</span>
      </summary>
      <fieldset>
        <legend>Theme preference</legend>
        {(['system', 'light', 'dark'] as const).map((value) => (
          <label key={value}>
            <input
              type="radio"
              name="theme"
              value={value}
              checked={preference === value}
              onChange={() => choose(value)}
            />
            {value === 'system'
              ? 'Use device theme'
              : value === 'light'
                ? 'Light theme'
                : 'Dark theme'}
          </label>
        ))}
      </fieldset>
    </details>
  )
}
