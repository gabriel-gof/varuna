const asCount = (value) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

const toPositiveSeconds = (value, fallbackSeconds = 300) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallbackSeconds
  return parsed
}

const parseTimestampMs = (value) => {
  if (!value) return null
  const ms = Date.parse(value)
  if (!Number.isFinite(ms)) return null
  return ms
}

export const getPollingIntervalSeconds = (olt) => toPositiveSeconds(olt?.polling_interval_seconds, 300)
export const getPowerIntervalSeconds = (olt) => toPositiveSeconds(olt?.power_interval_seconds, 300)

export const isStatusStale = (olt, nowMs = Date.now()) => {
  const lastPollMs = parseTimestampMs(olt?.last_poll_at)
  if (!lastPollMs) return true
  const staleAfterMs = getPollingIntervalSeconds(olt) * 1000
  return nowMs - lastPollMs > staleAfterMs
}

export const deriveOltHealthState = (olt, snmpState, nowMs = Date.now()) => {
  const snmpStatus = snmpState?.status
  if (snmpStatus === 'unreachable' || olt?.snmp_reachable === false) {
    return { state: 'gray', reason: 'snmp_unreachable' }
  }

  if (isStatusStale(olt, nowMs)) {
    return { state: 'gray', reason: 'status_stale' }
  }

  if (snmpStatus === 'pending' && olt?.snmp_reachable == null) {
    return { state: 'neutral', reason: 'checking' }
  }

  const online = asCount(olt?.online_count)
  const offline = asCount(olt?.offline_count)

  if (online <= 0 && offline <= 0) {
    return { state: 'neutral', reason: 'no_onus' }
  }
  if (online <= 0 && offline > 0) {
    return { state: 'red', reason: 'all_offline' }
  }
  if (online > 0 && offline > 0) {
    return { state: 'yellow', reason: 'partial_offline' }
  }

  return { state: 'green', reason: 'healthy' }
}
