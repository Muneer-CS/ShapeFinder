import { reasonText, type Capability } from '../api/patternScans'
export function ScanAvailability({
  capability,
  status,
}: {
  capability?: Capability
  status: string
}) {
  const message = !capability
    ? status
    : !capability.available
      ? `Scanning unavailable: ${reasonText(capability.reason)}`
      : capability.reason === 'massive_daily_cache'
        ? 'Daily scans use saved Nasdaq and NYSE candles.'
        : `Available. ${capability.timing_basis === 'operator_configured' ? 'Uses operator-configured timing; provider timing is unverified.' : capability.timing_basis === 'operator_attested' ? 'Uses operator-attested timing; this is not an independent provider guarantee.' : reasonText(capability.reason)}`
  return (
    <p
      className={`capability-note ${capability?.available ? '' : 'notice'}`}
      role="status"
    >
      {message}
    </p>
  )
}
