import { classifyOnu } from './stats.js'

const asCount = (value) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}
const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))
const isActiveEntity = (entity) => Boolean(entity) && entity.is_active !== false

const getPonSignalCounts = (pon) => {
  const onus = asList(pon?.onus).filter(Boolean)
  if (onus.length > 0) {
    let online = 0
    let knownOffline = 0
    let unknown = 0
    for (const onu of onus) {
      const state = classifyOnu(onu).status
      if (state === 'online') {
        online += 1
      } else if (state === 'unknown') {
        unknown += 1
      } else {
        knownOffline += 1
      }
    }
    const total = onus.length
    return {
      total,
      online,
      knownOffline,
      unknown,
      offline: knownOffline + unknown,
    }
  }

  const online = asCount(pon?.online_count)
  const offline = asCount(pon?.offline_count)
  return {
    total: online + offline,
    online,
    knownOffline: offline,
    unknown: 0,
    offline,
  }
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

const getPollingIntervalSeconds = (olt) => toPositiveSeconds(olt?.polling_interval_seconds, 300)

const isStatusStale = (olt, nowMs = Date.now()) => {
  const lastPollMs = parseTimestampMs(olt?.last_poll_at)
  const pollingIntervalMs = getPollingIntervalSeconds(olt) * 1000
  const staleMarginMs = 90_000
  const staleWindowMs = Math.max(
    (pollingIntervalMs * 3) + staleMarginMs,
    staleMarginMs + 300_000
  )

  if (!lastPollMs) {
    const lastDiscoveryMs = parseTimestampMs(olt?.last_discovery_at)
    if (!lastDiscoveryMs) return false
    return nowMs - lastDiscoveryMs > staleWindowMs
  }

  return nowMs - lastPollMs > staleWindowMs
}

const getPonHealthState = (pon) => {
  const { total, knownOffline } = getPonSignalCounts(pon)

  if (total <= 0) return 'green'
  if (knownOffline >= total) return 'red'
  if (knownOffline > 0) return 'yellow'
  return 'green'
}

const getSlotHealthState = (slot) => {
  const activePons = asList(slot?.pons).filter(isActiveEntity)
  if (!activePons.length) return 'green'

  const ponStates = activePons.map((pon) => getPonHealthState(pon))
  const hasRed = ponStates.some((state) => state === 'red')
  const allRed = ponStates.every((state) => state === 'red')

  if (allRed) return 'red'
  if (hasRed) return 'yellow'
  return 'green'
}

export const deriveOltHealthState = (olt, nowMs = Date.now()) => {
  // When SNMP has never been checked, show neutral until the scheduler reports.
  if (olt?.snmp_reachable == null) {
    return { state: 'neutral', reason: 'checking' }
  }

  if (olt?.snmp_reachable === false) {
    return { state: 'gray', reason: 'snmp_unreachable' }
  }

  if (isStatusStale(olt, nowMs)) {
    return { state: 'gray', reason: 'status_stale' }
  }

  const hasTopologyTree = Object.prototype.hasOwnProperty.call(olt || {}, 'slots')
  const activeSlots = asList(olt?.slots).filter(isActiveEntity)
  if (activeSlots.length > 0) {
    const slotStates = activeSlots.map((slot) => getSlotHealthState(slot))
    const hasRed = slotStates.some((state) => state === 'red')
    const allRed = slotStates.every((state) => state === 'red')

    if (allRed) return { state: 'red', reason: 'all_slots_red' }
    if (hasRed) return { state: 'yellow', reason: 'at_least_one_slot_red' }
    return { state: 'green', reason: 'no_slot_red' }
  }

  // Count-only payloads (/api/olts/ without include_topology) can be briefly stale on reload.
  // Avoid transient warning colors until the topology tree is loaded.
  if (!hasTopologyTree) {
    if (olt?.snmp_reachable === true) {
      return { state: 'green', reason: 'reachable_without_topology' }
    }
    return { state: 'neutral', reason: 'topology_not_loaded' }
  }

  const online = asCount(olt?.online_count)
  const offline = asCount(olt?.offline_count)

  if (online <= 0 && offline <= 0) {
    return { state: 'neutral', reason: 'no_onus' }
  }
  if (online <= 0 && offline > 0) {
    return { state: 'red', reason: 'all_offline' }
  }
  if (online > 0 && offline > 0) return { state: 'green', reason: 'mixed_but_online' }

  return { state: 'green', reason: 'healthy' }
}
