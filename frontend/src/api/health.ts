export type HealthResponse = {
  status: 'ok'
  service: string
  version: string
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/health`, { signal })
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }
  return (await response.json()) as HealthResponse
}
import { apiBaseUrl } from './config'
