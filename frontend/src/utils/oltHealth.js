const asCount = (value) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}
const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))
const isActiveEntity = (entity) => Boolean(entity) && entity.is_active !== false

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

const getPollingIntervalSeconds = (olt) => toPositiveSeconds(olt?.polling_interval_seconds, 300)

const isStatusStale = (olt, nowMs = Date.now()) => {
  const lastPollMs = parseTimestampMs(olt?.last_poll_at)
  const staleAfterMs = getPollingIntervalSeconds(olt) * 1000
  const graceMs = Math.max(90_000, Math.round(staleAfterMs * 0.5))
  const minimumToleranceMs = 10 * 60 * 1000
  const staleWindowMs = Math.max(staleAfterMs + graceMs, minimumToleranceMs)

  if (!lastPollMs) {
    const lastDiscoveryMs = parseTimestampMs(olt?.last_discovery_at)
    if (!lastDiscoveryMs) return false
    return nowMs - lastDiscoveryMs > staleWindowMs
  }

  return nowMs - lastPollMs > staleWindowMs
}

const getPonHealthState = (pon) => {
  const online = asCount(pon?.online_count)
  const offline = asCount(pon?.offline_count)
  const total = online + offline || asList(pon?.onus).length

  if (total <= 0) return 'green'
  if (online === 0 && offline > 0) return 'red'
  if (offline > 0) return 'yellow'
  return 'green'
}

const getSlotHealthState = (slot) => {
  const activePons = asList(slot?.pons).filter(isActiveEntity)
  if (!activePons.length) return 'green'

  const ponStates = activePons.map((pon) => getPonHealthState(pon))
  const redPons = ponStates.reduce((count, state) => (
    state === 'red' ? count + 1 : count
  ), 0)

  if (redPons === ponStates.length) return 'red'
  if (redPons > 0) return 'yellow'
  return 'green'
}

export const deriveOltHealthState = (olt, nowMs = Date.now()) => {
  // When SNMP has never been checked, show neutral until the scheduler reports.
  if (olt?.snmp_reachable == null) {
    return { state: 'neutral', reason: 'checking' }
  }

  const failureCount = Number(olt?.snmp_failure_count || 0)
  if (olt?.snmp_reachable === false && failureCount >= 2) {
    return { state: 'gray', reason: 'snmp_unreachable' }
  }

  if (isStatusStale(olt, nowMs)) {
    return { state: 'gray', reason: 'status_stale' }
  }

  const activeSlots = asList(olt?.slots).filter(isActiveEntity)
  if (activeSlots.length > 0) {
    const slotStates = activeSlots.map((slot) => getSlotHealthState(slot))
    const redSlots = slotStates.reduce((count, state) => (
      state === 'red' ? count + 1 : count
    ), 0)
    const totalSlots = slotStates.length
    if (redSlots === 0) {
      return { state: 'green', reason: 'no_slots_offline' }
    }
    if (redSlots === slotStates.length) {
      return { state: 'red', reason: 'all_slots_red' }
    }
    if (redSlots > 0 && redSlots < totalSlots) {
      return { state: 'yellow', reason: 'some_slots_red' }
    }
    return { state: 'green', reason: 'slots_healthy' }
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
