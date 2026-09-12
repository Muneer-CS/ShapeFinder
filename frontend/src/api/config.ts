export function resolveApiBaseUrl(
  value: string | undefined,
  development = import.meta.env.DEV,
): string {
  const candidate = value?.trim()
  if (!candidate) return development ? 'http://localhost:8000' : ''
  let parsed: URL
  try {
    parsed = new URL(candidate)
  } catch {
    throw new Error('VITE_API_BASE_URL must be an absolute HTTP(S) URL.')
  }
  if (
    !['http:', 'https:'].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  )
    throw new Error(
      'VITE_API_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment.',
    )
  return candidate.replace(/\/+$/, '')
}

export const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL)
