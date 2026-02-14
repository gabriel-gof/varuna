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

export const getPollingIntervalSeconds = (olt) => toPositiveSeconds(olt?.polling_interval_seconds, 300)
export const getPowerIntervalSeconds = (olt) => toPositiveSeconds(olt?.power_interval_seconds, 300)

export const isStatusStale = (olt, nowMs = Date.now()) => {
  const lastPollMs = parseTimestampMs(olt?.last_poll_at)
  if (!lastPollMs) return true
  const staleAfterMs = getPollingIntervalSeconds(olt) * 1000
  return nowMs - lastPollMs > staleAfterMs
}

const getPonHealthState = (pon) => {
  const online = asCount(pon?.online_count)
  const offline = asCount(pon?.offline_count)
  const total = online + offline || asList(pon?.onus).length

  if (total <= 0) return 'green'
  if (online === 0 && offline > 0) return 'red'
  return 'green'
}

const getSlotHealthState = (slot) => {
  const activePons = asList(slot?.pons).filter(isActiveEntity)
  if (!activePons.length) return 'green'

  const redPons = activePons.reduce((count, pon) => (
    getPonHealthState(pon) === 'red' ? count + 1 : count
  ), 0)

  if (redPons === activePons.length) return 'red'
  if (redPons > 0) return 'yellow'
  return 'green'
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

  const activeSlots = asList(olt?.slots).filter(isActiveEntity)
  if (activeSlots.length > 0) {
    const slotStates = activeSlots.map((slot) => getSlotHealthState(slot))
    const redSlots = slotStates.reduce((count, state) => (
      state === 'red' ? count + 1 : count
    ), 0)
    const degradedSlots = slotStates.reduce((count, state) => (
      state === 'red' || state === 'yellow' ? count + 1 : count
    ), 0)
    if (redSlots === slotStates.length) {
      return { state: 'red', reason: 'all_slots_red' }
    }
    if (degradedSlots > 0) {
      return { state: 'yellow', reason: 'slots_degraded' }
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
