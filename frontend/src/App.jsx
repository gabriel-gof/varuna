import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Network,
  Settings as SettingsIcon,
  LogOut,
  User,
  Palette,
  Languages,
  ChevronDown,
  ChevronRight,
  X,
  RotateCw,
  Check,
  ArrowDownUp
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { NetworkTopology } from './components/NetworkTopology'
import { SettingsPanel } from './components/SettingsPanel'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import api, { updatePonDescription } from './services/api'
import { InlineEditableText } from './components/InlineEditableText'
import { classifyOnu } from './utils/stats'
import { deriveOltHealthState } from './utils/oltHealth'
import { getPowerColor, powerColorClass } from './utils/powerThresholds'

const normalizeList = (data) => {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.results)) return data.results
  return []
}
const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))
const parseTimestampMs = (value) => {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}
const toPositiveInt = (value, fallback) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.round(parsed)
}

const getApiErrorMessage = (error, fallback) => {
  const data = error?.response?.data
  if (typeof data === 'string' && data.trim()) return data.trim()
  if (data?.detail) return String(data.detail)
  if (data && typeof data === 'object') {
    const parts = Object.entries(data)
      .map(([key, value]) => {
        if (Array.isArray(value)) return `${key}: ${value.join(', ')}`
        if (value && typeof value === 'object') return `${key}: ${JSON.stringify(value)}`
        return `${key}: ${value}`
      })
      .filter(Boolean)
    if (parts.length) return parts.join(' | ')
  }
  return error?.message || fallback
}

const clampPonPanelWidth = (value) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 42
  return Math.min(68, Math.max(32, numeric))
}

const normalizeMatchValue = (value) => String(value || '').trim().toLowerCase()
const ALARM_REASON_PRIORITY = ['linkLoss', 'dyingGasp', 'unknown']
const ALARM_REASON_TO_STATUS_KEY = {
  linkLoss: 'link_loss',
  dyingGasp: 'dying_gasp',
  unknown: 'unknown'
}
const SELECTED_PON_STORAGE_KEY = 'varuna.selectedPonId'
const SEARCH_MATCH_STORAGE_KEY = 'varuna.searchMatch'

const formatPowerValue = (value) => {
  if (value === null || value === undefined || value === '') return '—'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  return `${numeric.toFixed(2)} dBm`
}

const asNumericPower = (value) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

const getOnuPowerSnapshot = (onu) => {
  const onuRx = onu?.onu_rx_power ?? onu?.onu_rx ?? onu?.rx_onu ?? null
  const oltRx = onu?.olt_rx_power ?? onu?.olt_rx ?? onu?.rx_olt ?? null
  const readAt = onu?.power_read_at ?? onu?.read_at ?? onu?.power_timestamp ?? null
  return { onuRx, oltRx, readAt }
}

const hasPowerSnapshot = (snapshot) => {
  if (!snapshot) return false
  return snapshot.onuRx !== null || snapshot.oltRx !== null || Boolean(snapshot.readAt)
}

const shouldCarryForwardPower = (previousOlt, nextOlt) => {
  const previousPowerMs = parseTimestampMs(previousOlt?.last_power_at)
  if (!previousPowerMs) return false

  const nextPowerMs = parseTimestampMs(nextOlt?.last_power_at)
  if (!nextPowerMs) return true
  return nextPowerMs <= previousPowerMs
}

const buildOltPowerSnapshotMap = (olt) => {
  const byOnuId = new Map()

  asList(olt?.slots).forEach((slot) => {
    asList(slot?.pons).forEach((pon) => {
      asList(pon?.onus).forEach((onu) => {
        if (!onu?.id) return
        const snapshot = getOnuPowerSnapshot(onu)
        if (!hasPowerSnapshot(snapshot)) return
        byOnuId.set(String(onu.id), snapshot)
      })
    })
  })

  return byOnuId
}

const mergeOltPowerSnapshots = (previousOlt, nextOlt) => {
  if (!previousOlt || !shouldCarryForwardPower(previousOlt, nextOlt)) return nextOlt

  const previousSnapshotByOnu = buildOltPowerSnapshotMap(previousOlt)
  if (!previousSnapshotByOnu.size) return nextOlt

  const slots = asList(nextOlt?.slots).map((slot) => ({
    ...slot,
    pons: asList(slot?.pons).map((pon) => ({
      ...pon,
      onus: asList(pon?.onus).map((onu) => {
        const currentSnapshot = getOnuPowerSnapshot(onu)
        if (hasPowerSnapshot(currentSnapshot)) return onu

        const previousSnapshot = previousSnapshotByOnu.get(String(onu?.id))
        if (!previousSnapshot) return onu

        return {
          ...onu,
          onu_rx_power: previousSnapshot.onuRx,
          olt_rx_power: previousSnapshot.oltRx,
          power_read_at: previousSnapshot.readAt
        }
      })
    }))
  }))

  return {
    ...nextOlt,
    slots
  }
}

const mergeTopologyPowerSnapshots = (previousOlts, nextOlts) => {
  const previousByOltId = new Map(asList(previousOlts).map((olt) => [String(olt?.id), olt]))
  return asList(nextOlts).map((nextOlt) => mergeOltPowerSnapshots(previousByOltId.get(String(nextOlt?.id)), nextOlt))
}

const LONG_RUNNING_ACTION_TIMEOUT_MS = 180_000
const BACKGROUND_ACTION_TIMEOUT_MS = 20_000
const MAINTENANCE_PENDING_WINDOW_MS = {
  polling: 15 * 60 * 1000,
  power: 45 * 60 * 1000,
  discovery: 30 * 60 * 1000
}
const SEARCH_ROW_HIGHLIGHT_STYLE = {
  boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.65)'
}

const toIntOrNull = (value) => {
  const numeric = Number(value)
  if (!Number.isInteger(numeric)) return null
  return numeric
}

const patchPonPowerRows = (olts, target, rows) => {
  const rowMap = new Map(
    asList(rows)
      .filter((row) => row?.onu_id != null)
      .map((row) => [String(row.onu_id), row])
  )
  if (!rowMap.size) return olts

  let changed = false

  const nextOlts = asList(olts).map((olt) => {
    if (String(olt?.id) !== String(target.oltId)) return olt

    const nextSlots = asList(olt?.slots).map((slot) => {
      const slotNumber = slot?.slot_number ?? slot?.slot_id
      if (String(slotNumber) !== String(target.slotNumber)) return slot

      const nextPons = asList(slot?.pons).map((pon) => {
        const ponNumber = pon?.pon_number ?? pon?.pon_id
        if (String(ponNumber) !== String(target.ponNumber)) return pon

        const nextOnus = asList(pon?.onus).map((onu) => {
          const row = rowMap.get(String(onu?.id))
          if (!row) return onu

          changed = true
          return {
            ...onu,
            onu_rx_power: row.onu_rx_power ?? null,
            olt_rx_power: row.olt_rx_power ?? null,
            power_read_at: row.power_read_at ?? null
          }
        })

        return {
          ...pon,
          onus: nextOnus
        }
      })

      return {
        ...slot,
        pons: nextPons
      }
    })

    return {
      ...olt,
      slots: nextSlots
    }
  })

  return changed ? nextOlts : olts
}

const formatDisconnectionWindow = (startValue, endValue, language) => {
  if (!startValue || !endValue) return '—'

  const start = new Date(startValue)
  const end = new Date(endValue)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return '—'

  const locale = language === 'pt' ? 'pt-BR' : 'en-US'
  const timestampFormatter = new Intl.DateTimeFormat(locale, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })

  // Compact table display: show the window upper bound using the same style as power timestamps.
  return timestampFormatter.format(end)
}

const formatReadingAt = (value, language) => {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(language === 'pt' ? 'pt-BR' : 'en-US', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(parsed)
}

const mapTopologyToSlots = (olt, topology) => {
  const slots = asList(topology?.slots).map((slot) => {
    const slotId = `${olt.id}-${slot.slot_id}`
    const pons = asList(slot?.pons).map((pon) => {
      const ponId = `${olt.id}-${slot.slot_id}-${pon.pon_id}`
      return {
        id: ponId,
        db_id: pon.id,
        pon_number: pon.pon_id,
        pon_id: pon.pon_id,
        pon_key: pon.pon_key,
        name: pon.pon_name,
        description: pon.description || '',
        onus: asList(pon?.onus).map((onu) => ({
          id: onu.id,
          onu_number: onu.onu_number ?? onu.onu_id,
          onu_id: onu.onu_id ?? onu.onu_number,
          client_name: onu.client_name || onu.name,
          name: onu.name,
          serial_number: onu.serial_number ?? onu.serial,
          serial: onu.serial ?? onu.serial_number,
          status: onu.status,
          disconnect_reason: onu.disconnect_reason,
          offline_since: onu.offline_since,
          disconnect_window_start: onu.disconnect_window_start,
          disconnect_window_end: onu.disconnect_window_end,
          onu_rx_power: onu.onu_rx_power ?? onu.onu_rx ?? onu.rx_onu ?? null,
          olt_rx_power: onu.olt_rx_power ?? onu.olt_rx ?? onu.rx_olt ?? null,
          power_read_at: onu.power_read_at ?? onu.read_at ?? onu.power_timestamp ?? null
        }))
      }
    })
    return {
      id: slotId,
      slot_number: slot.slot_id,
      slot_id: slot.slot_id,
      slot_key: slot.slot_key,
      name: slot.slot_name,
      pons
    }
  })

  return {
    ...olt,
    supports_olt_rx_power: typeof topology?.olt?.supports_olt_rx_power === 'boolean'
      ? topology.olt.supports_olt_rx_power
      : olt?.supports_olt_rx_power,
    slots,
    slot_count: slots.length
  }
}

const findPonById = (olts, ponId) => {
  for (const olt of olts) {
    for (const slot of asList(olt?.slots)) {
      for (const pon of asList(slot?.pons)) {
        if (String(pon?.id) === String(ponId)) {
          return { olt, slot, pon }
        }
      }
    }
  }
  return null
}

const VarunaIcon = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
    <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2.5" />
    <circle cx="12" cy="12" r="7" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 2" />
    <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeWidth="1" strokeDasharray="1 3" opacity="0.4" />
    <path
      d="M12 2V4M12 20V22M2 12H4M20 12H22M4.93 4.93L6.34 6.34M17.66 17.66L19.07 19.07M4.93 19.07L6.34 17.66M17.66 6.34L19.07 4.93"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
)

const SegmentedControl = ({ options, value, onChange, compact = false }) => (
  <div className="inline-flex w-full items-center gap-0.5 rounded-lg border border-slate-200/80 dark:border-slate-700 bg-slate-100/80 dark:bg-slate-800/80 p-1">
    {options.map((opt) => (
      <button
        key={opt.id}
        onClick={(e) => {
          e.stopPropagation()
          onChange(opt.id)
        }}
        className={`${compact ? 'h-7 flex-1 min-w-0 px-1 text-[9px] tracking-[0.05em]' : 'h-8 min-w-[86px] px-3 text-[10px] tracking-[0.06em]'} font-bold uppercase rounded-md transition-all whitespace-nowrap overflow-hidden text-ellipsis ${
          value === opt.id
            ? 'bg-white dark:bg-slate-700 text-emerald-600 dark:text-emerald-400 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
            : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        {opt.label}
      </button>
    ))}
  </div>
)

const App = () => {
  const { t, i18n } = useTranslation()
  const [selectedPonId, setSelectedPonId] = useState(() => {
    try {
      if (typeof window === 'undefined') return null
      const saved = window.localStorage.getItem(SELECTED_PON_STORAGE_KEY)
      return saved ? String(saved) : null
    } catch (_err) {
      return null
    }
  })
  const [selectedSearchMatch, setSelectedSearchMatch] = useState(() => {
    try {
      const saved = window.localStorage.getItem(SEARCH_MATCH_STORAGE_KEY)
      return saved ? JSON.parse(saved) : null
    } catch (_err) {
      return null
    }
  })
  const [isDarkMode, setIsDarkMode] = useState(false)
  const [activeTab, setActiveTab] = useState('status')
  const [statusSortMode, setStatusSortMode] = useState('onu_id')
  const [powerSortMode, setPowerSortMode] = useState('default')
  const [alarmSortConfig, setAlarmSortConfig] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('varuna.alarmConfig'))
      if (saved) {
        const reasons = saved.reasons && typeof saved.reasons === 'object'
          ? Object.entries(saved.reasons).filter(([, v]) => v).map(([k]) => k).filter((r) => ALARM_REASON_PRIORITY.includes(r))
          : ['linkLoss']
        return {
          enabled: saved.enabled ?? true,
          reasons: reasons.length ? reasons : ['linkLoss']
        }
      }
    } catch { /* use defaults */ }
    return { enabled: true, reasons: ['linkLoss'] }
  })
  const [activeNav, setActiveNav] = useState(() => {
    const saved = localStorage.getItem('varuna_active_tab')
    return ['topology', 'settings'].includes(saved) ? saved : 'topology'
  })

  useEffect(() => {
    localStorage.setItem('varuna_active_tab', activeNav)
  }, [activeNav])

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      if (selectedPonId) {
        window.localStorage.setItem(SELECTED_PON_STORAGE_KEY, String(selectedPonId))
      } else {
        window.localStorage.removeItem(SELECTED_PON_STORAGE_KEY)
      }
    } catch (_err) {
      // noop
    }
  }, [selectedPonId])
  useEffect(() => {
    try {
      if (selectedSearchMatch) {
        window.localStorage.setItem(SEARCH_MATCH_STORAGE_KEY, JSON.stringify(selectedSearchMatch))
      } else {
        window.localStorage.removeItem(SEARCH_MATCH_STORAGE_KEY)
      }
    } catch (_err) {
      // noop
    }
  }, [selectedSearchMatch])
  const [isResizingPonPanel, setIsResizingPonPanel] = useState(false)
  const [ponPanelWidth, setPonPanelWidth] = useState(() => {
    try {
      if (typeof window === 'undefined') return 42
      const saved = window.localStorage.getItem('varuna.ponSidebarWidth')
      return clampPonPanelWidth(saved ?? 42)
    } catch (_err) {
      return 42
    }
  })
  const [olts, setOlts] = useState([])
  const [vendorProfiles, setVendorProfiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [vendorLoading, setVendorLoading] = useState(false)
  const [error, setError] = useState(null)
  const [vendorError, setVendorError] = useState(null)
  const [settingsActionError, setSettingsActionError] = useState(null)
  const [settingsActionMessage, setSettingsActionMessage] = useState('')
  const [settingsActionBusy, setSettingsActionBusy] = useState({})
  const [isRefreshingPonPanel, setIsRefreshingPonPanel] = useState(false)
  const [ponPanelError, setPonPanelError] = useState('')
  const ponPanelErrorTimerRef = useRef(null)
  useEffect(() => () => clearTimeout(ponPanelErrorTimerRef.current), [])
  const [healthTick, setHealthTick] = useState(() => Date.now())
  const [snmpStatus, setSnmpStatus] = useState({}) // { [oltId]: { status: 'pending'|'reachable'|'unreachable', sysDescr } }
  const snmpInFlightRef = useRef(false)
  const oltsRef = useRef([])
  const selectedPonMissingCyclesRef = useRef(0)
  const maintenanceLocksRef = useRef({
    polling: new Map(),
    discovery: new Map(),
    power: new Map()
  })
  const selectedPonDataRef = useRef(null)
  const wasAlarmEnabledRef = useRef(false)
  const mainLayoutRef = useRef(null)
  const resizePointerIdRef = useRef(null)
  const previousBodyCursorRef = useRef('')
  const previousBodyUserSelectRef = useRef('')
  const previousHtmlCursorRef = useRef('')

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [isDarkMode])

  const showPonPanelError = useCallback((message) => {
    setPonPanelError(message)
    clearTimeout(ponPanelErrorTimerRef.current)
    ponPanelErrorTimerRef.current = setTimeout(() => setPonPanelError(''), 6000)
  }, [])

  const fetchOlts = useCallback(async ({ surfaceError = false } = {}) => {
    const isInitialLoad = !oltsRef.current.length
    if (isInitialLoad) setLoading(true)
    if (isInitialLoad || surfaceError) setError(null)
    try {
      const res = await api.get('/olts/', { params: { include_topology: 'true' } })
      const nextOlts = normalizeList(res.data)
      setOlts((previousOlts) => mergeTopologyPowerSnapshots(previousOlts, nextOlts))
      return { ok: true }
    } catch (err) {
      try {
        const base = await api.get('/olts/')
        const baseOlts = normalizeList(base.data)
        const enriched = await Promise.all(
          baseOlts.map(async (olt) => {
            try {
              const topoRes = await api.get(`/olts/${olt.id}/topology/`)
              return mapTopologyToSlots(olt, topoRes.data)
            } catch (_innerErr) {
              return olt
            }
          })
        )
        setOlts((previousOlts) => mergeTopologyPowerSnapshots(previousOlts, enriched))
        return { ok: true, usedFallback: true }
      } catch (fallbackErr) {
        const message = getApiErrorMessage(err, getApiErrorMessage(fallbackErr, 'Failed to load OLT data'))
        if (isInitialLoad || surfaceError) {
          setError(message)
        }
        return { ok: false, message }
      }
    } finally {
      if (isInitialLoad) setLoading(false)
    }
  }, [])

  const fetchVendorProfiles = useCallback(async () => {
    setVendorLoading(true)
    setVendorError(null)
    try {
      const res = await api.get('/vendor-profiles/')
      setVendorProfiles(normalizeList(res.data))
    } catch (err) {
      setVendorError(getApiErrorMessage(err, 'Failed to load vendor profiles'))
    } finally {
      setVendorLoading(false)
    }
  }, [])

  // ─── SNMP connectivity checks (shared across Settings + Topology) ───
  const runSnmpChecks = useCallback(async (oltList) => {
    const targets = Array.isArray(oltList) ? oltList.filter((olt) => olt?.id) : []
    if (!targets.length || snmpInFlightRef.current) return

    snmpInFlightRef.current = true
    try {
      setSnmpStatus((prev) => {
        const next = { ...prev }
        targets.forEach((olt) => {
          if (!next[olt.id]) {
            next[olt.id] = {
              status: 'pending',
              sysDescr: '',
              failureStreak: 0,
            }
          }
        })
        return next
      })

      const results = await Promise.allSettled(
        targets.map(async (olt) => {
          try {
            const res = await api.post(`/olts/${olt.id}/snmp_check/`)
            return {
              id: olt.id,
              reachable: Boolean(res.data?.reachable),
              sysDescr: res.data?.sys_descr || '',
            }
          } catch {
            return {
              id: olt.id,
              reachable: false,
              sysDescr: '',
            }
          }
        })
      )

      setSnmpStatus((prev) => {
        const next = { ...prev }
        const activeIds = new Set(targets.map((olt) => String(olt.id)))

        Object.keys(next).forEach((id) => {
          if (!activeIds.has(String(id))) delete next[id]
        })

        results.forEach((result) => {
          if (result.status !== 'fulfilled') return
          const { id, reachable, sysDescr } = result.value
          const prevEntry = next[id] || {}

          if (reachable) {
            next[id] = {
              status: 'reachable',
              sysDescr,
              failureStreak: 0,
              checkedAt: Date.now(),
            }
            return
          }

          const failureStreak = Number(prevEntry.failureStreak || 0) + 1
          next[id] = {
            status: failureStreak >= 2 ? 'unreachable' : (prevEntry.status || 'pending'),
            sysDescr: prevEntry.sysDescr || '',
            failureStreak,
            checkedAt: Date.now(),
          }
        })

        return next
      })
    } finally {
      snmpInFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    fetchOlts()
    fetchVendorProfiles()
    const interval = setInterval(fetchOlts, 30000)
    return () => clearInterval(interval)
  }, [fetchOlts, fetchVendorProfiles])

  useEffect(() => {
    oltsRef.current = olts
  }, [olts])

  useEffect(() => {
    const timer = setInterval(() => setHealthTick(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [])

  const runDueMaintenance = useCallback(async (oltList) => {
    if (!Array.isArray(oltList) || !oltList.length) return

    const nowMs = Date.now()
    const requests = []
    const pollLocks = maintenanceLocksRef.current.polling
    const discoveryLocks = maintenanceLocksRef.current.discovery
    const powerLocks = maintenanceLocksRef.current.power

    const isPending = (lockMap, oltId, latestMetricMs) => {
      const pending = lockMap.get(oltId)
      if (!pending) return false

      const completed = Number.isFinite(latestMetricMs) && (
        (Number.isFinite(pending.lastMetricMs) && latestMetricMs > pending.lastMetricMs) ||
        (!Number.isFinite(pending.lastMetricMs) && latestMetricMs >= pending.startedAt - 1000)
      )
      const expired = nowMs >= pending.expiresAt
      if (completed || expired) {
        lockMap.delete(oltId)
        return false
      }
      return true
    }

    const queueBackgroundMaintenance = ({
      lockMap,
      oltId,
      endpoint,
      lastMetricMs,
      pendingWindowMs
    }) => {
      lockMap.set(oltId, {
        startedAt: nowMs,
        lastMetricMs,
        expiresAt: nowMs + pendingWindowMs
      })

      requests.push(
        api.post(
          endpoint,
          { background: true },
          { timeout: BACKGROUND_ACTION_TIMEOUT_MS }
        )
          .then((response) => {
            const queuedStatus = String(response?.data?.status || '').toLowerCase()
            if (!['accepted', 'already_running', 'completed'].includes(queuedStatus)) {
              lockMap.delete(oltId)
            }
          })
          .catch(() => {
            lockMap.delete(oltId)
          })
      )
    }

    oltList.forEach((olt) => {
      const oltId = olt?.id
      if (!oltId) return

      const lastPollMs = parseTimestampMs(olt.last_poll_at)
      const lastDiscoveryMs = parseTimestampMs(olt.last_discovery_at)
      const lastPowerMs = parseTimestampMs(olt.last_power_at)

      if (
        isPending(pollLocks, oltId, lastPollMs) ||
        isPending(discoveryLocks, oltId, lastDiscoveryMs) ||
        isPending(powerLocks, oltId, lastPowerMs)
      ) {
        return
      }

      if (olt.polling_enabled !== false) {
        const pollIntervalMs = toPositiveInt(olt.polling_interval_seconds, 300) * 1000
        const pollDue = !lastPollMs || nowMs - lastPollMs >= pollIntervalMs
        if (pollDue) {
          queueBackgroundMaintenance({
            lockMap: pollLocks,
            oltId,
            endpoint: `/olts/${oltId}/run_polling/`,
            lastMetricMs: lastPollMs,
            pendingWindowMs: MAINTENANCE_PENDING_WINDOW_MS.polling
          })
          return
        }
      }

      const onuCount = Number(olt?.onu_count ?? 0)
      if (onuCount > 0) {
        const powerIntervalMs = toPositiveInt(olt.power_interval_seconds, 300) * 1000
        const powerDue = !lastPowerMs || nowMs - lastPowerMs >= powerIntervalMs

        if (powerDue) {
          queueBackgroundMaintenance({
            lockMap: powerLocks,
            oltId,
            endpoint: `/olts/${oltId}/refresh_power/`,
            lastMetricMs: lastPowerMs,
            pendingWindowMs: MAINTENANCE_PENDING_WINDOW_MS.power
          })
          return
        }
      }

      if (olt.discovery_enabled !== false) {
        const discoveryIntervalMs = toPositiveInt(olt.discovery_interval_minutes, 240) * 60 * 1000
        const discoveryDue = !lastDiscoveryMs || nowMs - lastDiscoveryMs >= discoveryIntervalMs
        if (discoveryDue) {
          queueBackgroundMaintenance({
            lockMap: discoveryLocks,
            oltId,
            endpoint: `/olts/${oltId}/run_discovery/`,
            lastMetricMs: lastDiscoveryMs,
            pendingWindowMs: MAINTENANCE_PENDING_WINDOW_MS.discovery
          })
        }
      }
    })

    if (!requests.length) return
    await Promise.allSettled(requests)
    await fetchOlts()
  }, [fetchOlts])

  useEffect(() => {
    if (!olts.length) return
    runDueMaintenance(olts)
  }, [olts, runDueMaintenance])

  const oltIdsSignature = useMemo(
    () => [...olts.map((olt) => String(olt?.id)).filter(Boolean)].sort().join(','),
    [olts]
  )

  // Run SNMP checks when the OLT set changes.
  useEffect(() => {
    if (!olts?.length) return
    runSnmpChecks(olts)
  }, [oltIdsSignature, runSnmpChecks])

  // Keep checks periodic without recreating timers on every topology refresh payload.
  useEffect(() => {
    const timer = setInterval(() => runSnmpChecks(oltsRef.current), 180_000)
    return () => clearInterval(timer)
  }, [runSnmpChecks])

  const runSettingsAction = async (key, request, successMessage = '') => {
    setSettingsActionError(null)
    setSettingsActionMessage('')
    setSettingsActionBusy((prev) => ({ ...prev, [key]: true }))

    try {
      const result = await request()
      if (successMessage) {
        setSettingsActionMessage(successMessage)
        setTimeout(() => setSettingsActionMessage(''), 4000)
      }
      return result
    } catch (err) {
      setSettingsActionError(getApiErrorMessage(err, 'Failed to execute settings action'))
      setTimeout(() => setSettingsActionError(null), 5000)
      return null
    } finally {
      setSettingsActionBusy((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  const runQueuedSettingsAction = async ({ endpoint, acceptedMessage, alreadyRunningMessage }) => {
    setSettingsActionError(null)
    setSettingsActionMessage('')
    try {
      const response = await api.post(endpoint, { background: true })
      const queuedStatus = response?.data?.status
      const queuedDetail = response?.data?.detail
      if (queuedStatus === 'already_running') {
        setSettingsActionMessage(queuedDetail || alreadyRunningMessage || acceptedMessage)
      } else {
        setSettingsActionMessage(queuedDetail || acceptedMessage)
      }
      setTimeout(() => setSettingsActionMessage(''), 4000)
      return response?.data || null
    } catch (err) {
      setSettingsActionError(getApiErrorMessage(err, 'Failed to execute settings action'))
      setTimeout(() => setSettingsActionError(null), 5000)
      return null
    }
  }

  const createOlt = async (payload) => {
    const created = await runSettingsAction(
      'create',
      async () => {
        const response = await api.post('/olts/', payload)
        await fetchOlts()
        return response.data
      },
      t('OLT created successfully')
    )
    return created
  }

  const updateOlt = async (oltId, payload) => {
    const updated = await runSettingsAction(
      `update:${oltId}`,
      async () => {
        const response = await api.patch(`/olts/${oltId}/`, payload)
        await fetchOlts()
        return response.data
      },
      t('OLT updated successfully')
    )
    return updated
  }

  const deleteOlt = async (oltId) => {
    const removed = await runSettingsAction(
      `delete:${oltId}`,
      async () => {
        await api.delete(`/olts/${oltId}/`)
        await fetchOlts()
        return true
      },
      t('OLT removed successfully')
    )
    return Boolean(removed)
  }

  const runDiscovery = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/run_discovery/`,
      acceptedMessage: t('Discovery queued successfully'),
      alreadyRunningMessage: t('Discovery already running')
    })
  }

  const runPolling = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/run_polling/`,
      acceptedMessage: t('Polling queued successfully'),
      alreadyRunningMessage: t('Polling already running')
    })
  }

  const refreshPower = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/refresh_power/`,
      acceptedMessage: t('Power refresh queued successfully'),
      alreadyRunningMessage: t('Power refresh already running')
    })
  }

  const rawSelectedPonData = useMemo(() => {
    if (!selectedPonId) return null
    return findPonById(olts, selectedPonId)
  }, [olts, selectedPonId])

  const selectedPonData = useMemo(() => {
    if (rawSelectedPonData) return rawSelectedPonData
    if (!selectedPonId) return null
    const cached = selectedPonDataRef.current
    if (!cached) return null
    return String(cached?.pon?.id) === String(selectedPonId) ? cached : null
  }, [rawSelectedPonData, selectedPonId])

  useEffect(() => {
    if (rawSelectedPonData) {
      selectedPonDataRef.current = rawSelectedPonData
      return
    }
    if (!selectedPonId) {
      selectedPonDataRef.current = null
    }
  }, [rawSelectedPonData, selectedPonId])

  const collectPowerForSelectedPon = useCallback(async () => {
    const oltId = toIntOrNull(selectedPonData?.olt?.id)
    const slotNumber = toIntOrNull(selectedPonData?.slot?.slot_number ?? selectedPonData?.slot?.slot_id)
    const ponNumber = toIntOrNull(selectedPonData?.pon?.pon_number ?? selectedPonData?.pon?.pon_id)

    if (!oltId || slotNumber == null || ponNumber == null) {
      return { ok: false, message: t('Failed to refresh power data') }
    }

    const response = await api.post(
      '/onu/batch-power/',
      {
        olt_id: oltId,
        slot_id: slotNumber,
        pon_id: ponNumber,
        refresh: true
      },
      { timeout: LONG_RUNNING_ACTION_TIMEOUT_MS }
    )
    const rows = asList(response.data?.results)
    setOlts((previous) => patchPonPowerRows(previous, { oltId, slotNumber, ponNumber }, rows))
    return { ok: true }
  }, [selectedPonData, t])

  const handleRefreshPonPanel = useCallback(async () => {
    if (isRefreshingPonPanel) return

    setPonPanelError('')
    setIsRefreshingPonPanel(true)
    try {
      const selectedOltId = toIntOrNull(selectedPonData?.olt?.id)
      if (selectedOltId && activeTab === 'status') {
        await api.post(`/olts/${selectedOltId}/run_polling/`, {}, { timeout: LONG_RUNNING_ACTION_TIMEOUT_MS })
      } else if (activeTab === 'power') {
        const powerResult = await collectPowerForSelectedPon()
        if (!powerResult?.ok) {
          showPonPanelError(powerResult?.message || t('Failed to refresh power data'))
        }
      }

      const result = await fetchOlts({ surfaceError: false })
      if (!result?.ok) {
        showPonPanelError(result?.message || t('Failed to refresh panel data'))
      }
    } catch (err) {
      const fallback = activeTab === 'power'
        ? t('Failed to refresh power data')
        : t('Failed to refresh status data')
      showPonPanelError(getApiErrorMessage(err, fallback))
    } finally {
      setIsRefreshingPonPanel(false)
    }
  }, [activeTab, collectPowerForSelectedPon, fetchOlts, isRefreshingPonPanel, selectedPonData, showPonPanelError, t])

  useEffect(() => {
    if (!selectedPonId) {
      selectedPonMissingCyclesRef.current = 0
      return
    }
    if (rawSelectedPonData) {
      selectedPonMissingCyclesRef.current = 0
      return
    }
    if (!olts.length || loading || error) return

    selectedPonMissingCyclesRef.current += 1
    if (selectedPonMissingCyclesRef.current >= 3) {
      selectedPonMissingCyclesRef.current = 0
      setSelectedSearchMatch(null)
      setSelectedPonId(null)
    }
  }, [selectedPonId, rawSelectedPonData, loading, error, olts.length])

  const selectedSlotNumber = selectedPonData?.slot?.slot_number ?? selectedPonData?.slot?.slot_id
  const selectedPonNumber = selectedPonData?.pon?.pon_number ?? selectedPonData?.pon?.pon_id
  const selectedPonPath = [
    selectedPonData?.olt?.name || 'OLT',
    `${t('SLOT')} ${selectedSlotNumber ?? '—'}`,
    `PON ${selectedPonNumber ?? '—'}`
  ]
  const isPonPanelOpen = activeNav === 'topology' && Boolean(selectedPonId)

  const oltHealthById = useMemo(() => {
    return olts.reduce((acc, olt) => {
      acc[String(olt.id)] = deriveOltHealthState(olt, snmpStatus?.[olt.id], healthTick)
      return acc
    }, {})
  }, [olts, snmpStatus, healthTick])

  const lastCollectionAt = useMemo(() => {
    let latest = null
    for (const olt of olts) {
      const ms = parseTimestampMs(olt.last_poll_at)
      if (ms && (!latest || ms > latest)) latest = ms
    }
    return latest ? new Date(latest) : null
  }, [olts])

  const updatePonPanelWidthByClientX = (clientX) => {
    const container = mainLayoutRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (!rect.width) return
    const widthFromRight = ((rect.right - clientX) / rect.width) * 100
    setPonPanelWidth(clampPonPanelWidth(widthFromRight))
  }

  const stopPonPanelResize = () => {
    resizePointerIdRef.current = null
    setIsResizingPonPanel(false)
  }

  const handlePonResizePointerDown = (event) => {
    if (event.button !== 0) return
    event.preventDefault()
    resizePointerIdRef.current = event.pointerId
    setIsResizingPonPanel(true)
    event.currentTarget.setPointerCapture(event.pointerId)
    updatePonPanelWidthByClientX(event.clientX)
  }

  const handlePonResizePointerMove = (event) => {
    if (!isResizingPonPanel) return
    if (resizePointerIdRef.current !== event.pointerId) return
    updatePonPanelWidthByClientX(event.clientX)
  }

  const handlePonResizePointerUp = (event) => {
    if (resizePointerIdRef.current !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    stopPonPanelResize()
  }

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      window.localStorage.setItem('varuna.ponSidebarWidth', String(ponPanelWidth))
    } catch (_err) {
      // noop
    }
  }, [ponPanelWidth])

  useEffect(() => {
    if (!isPonPanelOpen && isResizingPonPanel) {
      stopPonPanelResize()
    }
  }, [isPonPanelOpen, isResizingPonPanel])

  useEffect(() => {
    if (!isPonPanelOpen) return undefined

    const handleKeyDown = (event) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      if (isResizingPonPanel) {
        stopPonPanelResize()
      }
      setSelectedSearchMatch(null)
      setSelectedPonId(null)
    }

    window.addEventListener('keydown', handleKeyDown, true)
    return () => window.removeEventListener('keydown', handleKeyDown, true)
  }, [isPonPanelOpen, isResizingPonPanel])

  useEffect(() => {
    if (!isResizingPonPanel) return undefined

    previousBodyCursorRef.current = document.body.style.cursor
    previousBodyUserSelectRef.current = document.body.style.userSelect
    previousHtmlCursorRef.current = document.documentElement.style.cursor

    document.body.style.cursor = 'ew-resize'
    document.body.style.userSelect = 'none'
    document.documentElement.style.cursor = 'ew-resize'

    return () => {
      document.body.style.cursor = previousBodyCursorRef.current
      document.body.style.userSelect = previousBodyUserSelectRef.current
      document.documentElement.style.cursor = previousHtmlCursorRef.current
    }
  }, [isResizingPonPanel])

  const selectedOnus = useMemo(() => {
    const onus = asList(selectedPonData?.pon?.onus)
    return [...onus].sort((a, b) => (a?.onu_number ?? 0) - (b?.onu_number ?? 0))
  }, [selectedPonData])

  const supportsSelectedOltRxPower = useMemo(() => {
    const explicit = selectedPonData?.olt?.supports_olt_rx_power
    if (typeof explicit === 'boolean') return explicit
    return true
  }, [selectedPonData])

  const availableStatusCounts = useMemo(() => {
    return selectedOnus.reduce((acc, onu) => {
      const statusKey = classifyOnu(onu).status
      if (statusKey === 'online') {
        acc.online += 1
      } else {
        acc.offline += 1
      }
      if (Object.prototype.hasOwnProperty.call(acc, statusKey)) {
        acc[statusKey] += 1
      }
      return acc
    }, {
      online: 0,
      offline: 0,
      link_loss: 0,
      dying_gasp: 0,
      unknown: 0,
    })
  }, [selectedOnus])

  const resolveStatusSortMode = (requestedMode) => {
    const hasAny = (statusKey) => (availableStatusCounts[statusKey] || 0) > 0

    if (requestedMode === 'onu_id') return 'onu_id'
    if (requestedMode === 'offline') return hasAny('offline') ? 'offline' : 'onu_id'
    if (requestedMode === 'online') return hasAny('online') ? 'online' : (hasAny('offline') ? 'offline' : 'onu_id')

    if (requestedMode === 'link_loss' || requestedMode === 'dying_gasp' || requestedMode === 'unknown') {
      if (hasAny('offline')) return 'offline'
      if (hasAny('online')) return 'online'
      return 'onu_id'
    }

    return 'onu_id'
  }

  const statusSortOptions = [
    { id: 'onu_id', label: t('Default order') },
    { id: 'offline', label: t('Offline') },
    { id: 'online', label: t('Online') }
  ]

  const powerSortOptions = useMemo(() => {
    const options = [
      { id: 'default', label: t('Default order') },
      { id: 'worst_onu_rx', label: t('Worst ONU RX') },
      { id: 'best_onu_rx', label: t('Best ONU RX') }
    ]
    if (supportsSelectedOltRxPower) {
      options.splice(2, 0, { id: 'worst_olt_rx', label: t('Worst OLT RX') })
      options.push({ id: 'best_olt_rx', label: t('Best OLT RX') })
    }
    return options
  }, [supportsSelectedOltRxPower, t])

  const activeSortOptions = activeTab === 'power' ? powerSortOptions : statusSortOptions
  const currentSortMode = activeTab === 'power' ? powerSortMode : statusSortMode
  const currentSortLabel = activeSortOptions.find((option) => option.id === currentSortMode)?.label || activeSortOptions[0]?.label || t('ONU ID')
  const isSidebarRefreshBusy = isRefreshingPonPanel
  const setCurrentSortMode = (mode) => {
    if (activeTab === 'power') {
      setPowerSortMode(mode)
      return
    }
    setStatusSortMode(resolveStatusSortMode(mode))
  }

  useEffect(() => {
    if (powerSortOptions.some((option) => option.id === powerSortMode)) return
    setPowerSortMode('default')
  }, [powerSortMode, powerSortOptions])

  useEffect(() => {
    const wasEnabled = wasAlarmEnabledRef.current
    wasAlarmEnabledRef.current = alarmSortConfig.enabled
    if (!alarmSortConfig.enabled || wasEnabled) return

    const nextMode = resolveStatusSortMode('offline')
    setStatusSortMode((prev) => (prev === nextMode ? prev : nextMode))
  }, [alarmSortConfig.enabled, availableStatusCounts])

  const statusRows = useMemo(() => {
    const baseRows = selectedOnus.map((onu) => {
      const classification = classifyOnu(onu)
      const statusKey = classification.status
      const parsedOffline = Date.parse(onu?.disconnect_window_end || '')
      return {
        onu,
        statusKey,
        offlineTimestamp: Number.isFinite(parsedOffline) ? parsedOffline : null,
        onuNumber: Number(onu?.onu_number ?? onu?.onu_id ?? 0)
      }
    })

    if (statusSortMode === 'onu_id') {
      return [...baseRows].sort((a, b) => a.onuNumber - b.onuNumber)
    }

    const offlineStatuses = ['link_loss', 'dying_gasp', 'unknown', 'offline']
    let orderedStatuses = ['online', ...offlineStatuses]

    if (statusSortMode === 'offline') {
      const selectedOfflineStatuses = alarmSortConfig.enabled
        ? ALARM_REASON_PRIORITY
            .filter((reason) => alarmSortConfig.reasons.includes(reason))
            .map((reason) => ALARM_REASON_TO_STATUS_KEY[reason])
            .filter((status, index, list) => Boolean(status) && list.indexOf(status) === index && offlineStatuses.includes(status))
        : []
      orderedStatuses = [
        ...selectedOfflineStatuses,
        ...offlineStatuses.filter((status) => !selectedOfflineStatuses.includes(status)),
        'online',
      ]
    }
    const priorities = orderedStatuses.reduce((acc, status, index) => {
      acc[status] = index
      return acc
    }, {})

    return [...baseRows].sort((a, b) => {
      const statusDelta = (priorities[a.statusKey] ?? 9) - (priorities[b.statusKey] ?? 9)
      if (statusDelta !== 0) return statusDelta

      if (a.statusKey !== 'online' && b.statusKey !== 'online') {
        const timeDelta = (b.offlineTimestamp ?? 0) - (a.offlineTimestamp ?? 0)
        if (timeDelta !== 0) return timeDelta
      }

      return a.onuNumber - b.onuNumber
    })
  }, [selectedOnus, statusSortMode, alarmSortConfig.enabled, alarmSortConfig.reasons])

  const powerRows = useMemo(() => {
    const baseRows = selectedOnus.map((onu) => {
      const classification = classifyOnu(onu)
      const { onuRx, oltRx, readAt } = getOnuPowerSnapshot(onu)
      const parsedOnuRx = asNumericPower(onuRx)
      const parsedOltRx = asNumericPower(oltRx)
      return {
        onu,
        statusKey: classification.status,
        onuNumber: Number(onu?.onu_number ?? onu?.onu_id ?? 0),
        onuRx: parsedOnuRx,
        oltRx: parsedOltRx,
        readAt
      }
    })

    const compareNullable = (a, b, direction = 'asc') => {
      if (a === null && b === null) return 0
      if (a === null) return 1
      if (b === null) return -1
      if (a === b) return 0
      return direction === 'asc' ? a - b : b - a
    }

    const sortBy = (primaryKey, direction) => {
      const secondaryKey = supportsSelectedOltRxPower
        ? (primaryKey === 'oltRx' ? 'onuRx' : 'oltRx')
        : 'onuRx'
      return [...baseRows].sort((a, b) => {
        const primaryDelta = compareNullable(a[primaryKey], b[primaryKey], direction)
        if (primaryDelta !== 0) return primaryDelta

        const secondaryDelta = compareNullable(a[secondaryKey], b[secondaryKey], direction)
        if (secondaryDelta !== 0) return secondaryDelta

        return a.onuNumber - b.onuNumber
      })
    }

    if (powerSortMode === 'default') return [...baseRows].sort((a, b) => a.onuNumber - b.onuNumber)
    if (powerSortMode === 'worst_olt_rx') {
      return supportsSelectedOltRxPower ? sortBy('oltRx', 'asc') : sortBy('onuRx', 'asc')
    }
    if (powerSortMode === 'worst_onu_rx') return sortBy('onuRx', 'asc')
    if (powerSortMode === 'best_olt_rx') {
      return supportsSelectedOltRxPower ? sortBy('oltRx', 'desc') : sortBy('onuRx', 'desc')
    }
    if (powerSortMode === 'best_onu_rx') return sortBy('onuRx', 'desc')
    return [...baseRows].sort((a, b) => a.onuNumber - b.onuNumber)
  }, [selectedOnus, powerSortMode, supportsSelectedOltRxPower])

  useEffect(() => {
    if (!selectedSearchMatch) return
    if (!selectedPonId || String(selectedSearchMatch.ponId) !== String(selectedPonId)) return

    const scrollToHighlight = () => {
      const row = document.querySelector('[data-onu-highlight="true"]')
      if (row) {
        row.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
    const frame = requestAnimationFrame(() => {
      setTimeout(scrollToHighlight, 80)
    })
    return () => cancelAnimationFrame(frame)
  }, [selectedSearchMatch, selectedPonId, selectedOnus, activeTab])

  const selectedPonStats = useMemo(() => {
    return selectedOnus.reduce((acc, onu) => {
      const { status } = classifyOnu(onu)
      if (status === 'online') acc.online++
      else acc.offline++
      return acc
    }, { online: 0, offline: 0 })
  }, [selectedOnus])

  const isSelectedOltGray = useMemo(() => {
    const oltId = selectedPonData?.olt?.id
    if (!oltId) return false
    const health = oltHealthById?.[String(oltId)]
    return health?.state === 'gray'
  }, [selectedPonData, oltHealthById])

  const GRAY_STATUS_STYLE = 'bg-slate-100 text-slate-500 ring-1 ring-inset ring-slate-200 dark:bg-slate-700/50 dark:text-slate-400 dark:ring-slate-500/40'

  const statusStyle = (statusKey) => {
    if (isSelectedOltGray) return GRAY_STATUS_STYLE
    if (statusKey === 'online') {
      return 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-400/30'
    }
    if (statusKey === 'dying_gasp') {
      return 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200 dark:bg-blue-500/15 dark:text-blue-300 dark:ring-blue-400/30'
    }
    if (statusKey === 'link_loss') {
      return 'bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/30'
    }
    if (statusKey === 'unknown') {
      return 'bg-purple-50 text-purple-600 ring-1 ring-inset ring-purple-200 dark:bg-purple-500/15 dark:text-purple-300 dark:ring-purple-400/30'
    }
    if (statusKey === 'offline') {
      return 'bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/30'
    }
    return 'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200 dark:bg-slate-700/50 dark:text-slate-200 dark:ring-slate-500/40'
  }

  const statusDot = (statusKey) => {
    if (isSelectedOltGray) return 'bg-slate-400 dark:bg-slate-500'
    if (statusKey === 'online') return 'bg-emerald-500'
    if (statusKey === 'dying_gasp') return 'bg-blue-500'
    if (statusKey === 'link_loss') return 'bg-rose-500'
    if (statusKey === 'unknown') return 'bg-purple-500'
    if (statusKey === 'offline') return 'bg-rose-500'
    return 'bg-slate-400'
  }

  const handleAlarmModeChange = useCallback((config) => {
    const reasons = Array.isArray(config?.reasons)
      ? config.reasons.filter((reason) => ALARM_REASON_PRIORITY.includes(reason))
      : ALARM_REASON_PRIORITY
    const nextEnabled = Boolean(config?.enabled)
    const nextReasons = reasons.length ? reasons : ALARM_REASON_PRIORITY

    setAlarmSortConfig((prev) => {
      const sameEnabled = prev.enabled === nextEnabled
      const sameReasons = prev.reasons.length === nextReasons.length && prev.reasons.every((reason, index) => reason === nextReasons[index])
      if (sameEnabled && sameReasons) return prev
      return {
        enabled: nextEnabled,
        reasons: nextReasons
      }
    })
  }, [])

  return (
    <div className="h-screen bg-white dark:bg-slate-950 flex flex-col font-sans transition-colors duration-300">
      <nav className="h-16 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-700/50 flex items-center px-6 sticky top-0 z-[100] transition-colors shadow-sm">
        <div className="flex items-center gap-3 mr-4 sm:mr-10">
          <div className="w-9 h-9 bg-emerald-600 rounded-lg flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <VarunaIcon className="w-6 h-6 text-white" />
          </div>
          <span className="text-[12px] font-black text-slate-900 dark:text-white tracking-widest uppercase hidden md:block">VARUNA</span>
        </div>

        <div className="flex items-center gap-1 h-full">
          <button
            onClick={() => setActiveNav('topology')}
            className={`flex items-center justify-center gap-2.5 px-4 h-full sm:w-[156px] transition-all relative group ${activeNav === 'topology' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <Network className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider hidden sm:block">{t('Topology')}</span>
            {activeNav === 'topology' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
          <button
            onClick={() => setActiveNav('settings')}
            className={`flex items-center justify-center gap-2.5 px-4 h-full sm:w-[156px] transition-all relative group ${activeNav === 'settings' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <SettingsIcon className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider hidden sm:block">{t('Settings')}</span>
            {activeNav === 'settings' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
        </div>

        <div className="ml-auto flex items-center gap-3">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="flex items-center gap-2.5 p-1.5 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-all group outline-none">
                <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center text-emerald-600 dark:text-emerald-400 transition-colors group-hover:bg-emerald-200 dark:group-hover:bg-emerald-500/20">
                  <User className="w-[18px] h-[18px]" />
                </div>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400 transition-transform duration-200 group-data-[state=open]:rotate-180" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                className="w-[252px] bg-white dark:bg-slate-900 rounded-2xl p-2.5 shadow-2xl border border-slate-100 dark:border-slate-700/50 z-[200] animate-in fade-in zoom-in-95 duration-200"
                sideOffset={8}
                align="end"
              >
                <div className="px-2 py-2 mb-1.5 border-b border-slate-100 dark:border-slate-700/50">
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center text-emerald-600 dark:text-emerald-400">
                      <User className="w-[17px] h-[17px]" />
                    </div>
                    <div>
                      <p className="text-[12px] font-extrabold text-slate-900 dark:text-white leading-none">Administrator</p>
                    </div>
                  </div>
                </div>

                <div className="px-0.5 py-0.5">
                  <div className="flex flex-col gap-3">
                    {/* Theme Section */}
                    <div className="flex flex-col gap-1.5">
                       <div className="flex items-center gap-2.5">
                          <div className="w-6 h-6 rounded-md bg-indigo-100/80 dark:bg-indigo-500/15 text-indigo-500 dark:text-indigo-400 flex items-center justify-center">
                              <Palette className="w-3.5 h-3.5" />
                          </div>
                          <span className="text-[10px] font-bold text-slate-600 dark:text-slate-300 uppercase tracking-wider">{t('THEME')}</span>
                       </div>
                       <SegmentedControl
                        compact
                        value={isDarkMode ? 'dark' : 'light'}
                        onChange={(val) => setIsDarkMode(val === 'dark')}
                        options={[{ id: 'light', label: t('LIGHT') }, { id: 'dark', label: t('DARK') }]}
                      />
                    </div>

                    {/* Language Section */}
                    <div className="flex flex-col gap-1.5">
                       <div className="flex items-center gap-2.5">
                          <div className="w-6 h-6 rounded-md bg-emerald-100/80 dark:bg-emerald-500/15 text-emerald-500 dark:text-emerald-400 flex items-center justify-center">
                               <Languages className="w-3.5 h-3.5" />
                          </div>
                          <span className="text-[10px] font-bold text-slate-600 dark:text-slate-300 uppercase tracking-wider">{t('LANGUAGE')}</span>
                       </div>
                      <SegmentedControl
                        compact
                        value={i18n.language}
                        onChange={(val) => i18n.changeLanguage(val)}
                        options={[{ id: 'pt', label: 'PT-BR' }, { id: 'en', label: 'EN' }]}
                      />
                    </div>
                  </div>
                </div>
                <DropdownMenu.Separator className="h-px bg-slate-100 dark:bg-slate-800 my-2 mx-0.5" />
                <DropdownMenu.Item className="flex items-center gap-2.5 px-2 py-2 text-[10px] font-black text-rose-500 rounded-xl cursor-pointer outline-none transition-colors hover:bg-rose-50 dark:hover:bg-rose-900/20 uppercase group">
                  <div className="w-6 h-6 rounded-md bg-rose-100 dark:bg-rose-900/30 flex items-center justify-center text-rose-500 group-hover:bg-rose-200 dark:group-hover:bg-rose-800/50 transition-colors">
                    <LogOut className="w-3.5 h-3.5" />
                  </div>
                  <span>{t('LOGOUT')}</span>
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </nav>

      <main
        ref={mainLayoutRef}
        className="flex-1 min-h-0 flex overflow-hidden"
        style={isPonPanelOpen ? { '--pon-panel-width': `${ponPanelWidth}%` } : undefined}
      >
        <section
          className={`
            min-w-0 overflow-y-auto custom-scrollbar ${isResizingPonPanel ? '' : 'transition-[width] duration-150'}
            ${isPonPanelOpen
              ? 'hidden lg:block lg:w-[calc(100%-var(--pon-panel-width))] border-r border-slate-100 dark:border-slate-700/50'
              : 'flex-1'}
          `}
        >
          {activeNav === 'topology' ? (
            <NetworkTopology
              olts={olts}
              loading={loading}
              error={error}
              snmpStatus={snmpStatus}
              oltHealthById={oltHealthById}
              selectedPonId={selectedPonId}
              selectedSearchMatch={selectedSearchMatch}
              onAlarmModeChange={handleAlarmModeChange}
              onSearchMatchSelect={setSelectedSearchMatch}
              onPonSelect={(id, options = {}) => {
                const nextId = id !== null && id !== undefined ? String(id) : null
                if (!options?.force) {
                  setSelectedSearchMatch(null)
                }
                if (options?.force) {
                  setSelectedPonId(nextId)
                  return
                }
                setSelectedPonId((prev) => {
                  const prevId = prev !== null && prev !== undefined ? String(prev) : null
                  return prevId === nextId ? null : nextId
                })
              }}
            />
          ) : (
            <SettingsPanel
              olts={olts}
              vendorProfiles={vendorProfiles}
              loading={loading}
              vendorLoading={vendorLoading}
              error={error}
              vendorError={vendorError}
              actionError={settingsActionError}
              actionMessage={settingsActionMessage}
              onCreateOlt={createOlt}
              onUpdateOlt={updateOlt}
              onDeleteOlt={deleteOlt}
              onRunDiscovery={runDiscovery}
              onRunPolling={runPolling}
              onRefreshPower={refreshPower}
              actionBusy={settingsActionBusy}
              snmpStatus={snmpStatus}
              oltHealthById={oltHealthById}
            />
          )}
        </section>

        {activeNav === 'topology' && isPonPanelOpen && (
          <div className="hidden lg:flex relative w-4 -mx-1 flex-shrink-0 items-stretch z-20">
            <button
              type="button"
              onPointerDown={handlePonResizePointerDown}
              onPointerMove={handlePonResizePointerMove}
              onPointerUp={handlePonResizePointerUp}
              onPointerCancel={handlePonResizePointerUp}
              onLostPointerCapture={stopPonPanelResize}
              onDoubleClick={() => setPonPanelWidth(42)}
              aria-label={t('Resize PON sidebar')}
              className="relative h-full w-full cursor-ew-resize touch-none focus:outline-none group"
              style={{ cursor: 'ew-resize' }}
            >
              <span
                className={`
                  absolute inset-y-0 left-1/2 -translate-x-1/2 w-[2px] rounded-full transition-colors
                  ${isResizingPonPanel
                    ? 'bg-emerald-500'
                    : 'bg-slate-200 dark:bg-slate-700 group-hover:bg-emerald-400/80'}
                `}
                style={{ cursor: 'ew-resize' }}
              />
            </button>
          </div>
        )}

        {activeNav === 'topology' && (
          <aside
            className={`
              h-full min-h-0 flex flex-col flex-shrink-0 bg-slate-100 dark:bg-slate-950 overflow-hidden ${isResizingPonPanel ? '' : 'transition-[width,opacity,transform] duration-300 ease-out'}
              ${isPonPanelOpen
                ? 'w-full lg:w-[var(--pon-panel-width)] opacity-100 translate-x-0 border-l border-slate-100 dark:border-slate-700/50'
                : 'w-0 opacity-0 translate-x-full pointer-events-none border-l-0'}
            `}
          >
            {selectedPonId && (() => {
              const handleDescriptionSave = async (newValue) => {
                const pon = selectedPonData?.pon
                const dbId = pon?.db_id ?? pon?.id
                if (!dbId || typeof dbId !== 'number') return
                setOlts((prev) =>
                  prev.map((olt) => ({
                    ...olt,
                    slots: asList(olt?.slots).map((slot) => ({
                      ...slot,
                      pons: asList(slot?.pons).map((p) =>
                        (p.db_id ?? p.id) === dbId ? { ...p, description: newValue } : p
                      ),
                    })),
                  }))
                )
                try {
                  await updatePonDescription(dbId, newValue)
                } catch (_err) {
                  await fetchOlts()
                }
              }
              const handleClosePanel = () => {
                setSelectedSearchMatch(null)
                setSelectedPonId(null)
              }
              return (
              <div className="h-full min-h-0 flex flex-col">
                {/* Desktop header */}
                <div className="hidden lg:flex pl-8 pr-4 py-2.5 border-b border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 items-center">
                  <div className="w-full flex items-start justify-between gap-3">
                    <div className="min-w-0 flex flex-col">
                      <div className="flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-wide">
                        {selectedPonPath.map((part, idx) => (
                          <React.Fragment key={`${part}-${idx}`}>
                            {idx > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600" strokeWidth={2.5} />}
                            <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                              {part}
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                      <InlineEditableText
                        value={selectedPonData?.pon?.description || ''}
                        placeholder={t('addDescription')}
                        onSave={handleDescriptionSave}
                      />
                    </div>
                    <button
                      onClick={handleClosePanel}
                      className="mt-0.5 h-9 w-9 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors shrink-0"
                      aria-label={t('Close')}
                    >
                      <X className="w-[18px] h-[18px]" />
                    </button>
                  </div>
                </div>
                {/* Mobile header */}
                <div className="flex lg:hidden flex-col px-4 py-3 border-b border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 gap-1">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-wide">
                        {selectedPonPath.map((part, idx) => (
                          <React.Fragment key={`m-${part}-${idx}`}>
                            {idx > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600 shrink-0" strokeWidth={2.5} />}
                            <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                              {part}
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                      <InlineEditableText
                        value={selectedPonData?.pon?.description || ''}
                        placeholder={t('addDescription')}
                        onSave={handleDescriptionSave}
                      />
                    </div>
                    <button
                      onClick={handleClosePanel}
                      className="mt-1 h-8 w-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors active:scale-95 shrink-0"
                      aria-label={t('Close')}
                    >
                      <X className="w-[18px] h-[18px]" />
                    </button>
                  </div>
                </div>

                <div className="flex-1 min-h-0 flex flex-col p-3 lg:p-4 bg-slate-100 dark:bg-slate-950 overflow-hidden">
                  <div className="flex items-center gap-2 mb-3">
                    <div className="inline-flex h-9 items-center gap-1 p-1 rounded-lg border border-slate-200/80 dark:border-slate-700/80 bg-slate-50/90 dark:bg-slate-900/70">
                      {[
                        { id: 'status', label: t('Status') },
                        { id: 'power', label: t('Potência') }
                      ].map((tab) => {
                        const isActive = activeTab === tab.id
                        return (
                          <button
                            key={tab.id}
                            type="button"
                            onClick={() => setActiveTab(tab.id)}
                            className={`
                              h-7 min-w-[72px] lg:min-w-[88px] px-3 rounded-md text-[10px] font-black uppercase tracking-[0.06em] transition-all active:scale-95
                              ${isActive
                                ? 'bg-white dark:bg-slate-800 text-emerald-600 dark:text-emerald-400 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                                : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-white/70 dark:hover:bg-slate-800/60'}
                            `}
                          >
                            {tab.label}
                          </button>
                        )
                      })}
                    </div>
                    <div className="ml-auto flex items-center gap-2">
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger asChild>
                          <button
                            className="relative h-9 w-[130px] lg:w-[156px] rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-500 hover:text-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-95"
                            aria-label={t('Sort by')}
                            title={t('Sort by')}
                          >
                            <ArrowDownUp className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 shrink-0" />
                            <span className="absolute left-7 right-7 top-1/2 -translate-y-1/2 text-center text-[10px] font-black uppercase tracking-[0.03em] whitespace-nowrap overflow-hidden text-ellipsis text-emerald-600 dark:text-emerald-400">
                              {currentSortLabel}
                            </span>
                            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5" />
                          </button>
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Portal>
                          <DropdownMenu.Content
                            className="w-[156px] bg-white dark:bg-slate-900 rounded-xl p-1 shadow-xl border border-slate-200 dark:border-slate-700/50 z-[220] animate-in fade-in zoom-in-95 duration-150"
                            sideOffset={8}
                            align="end"
                          >
                            {activeSortOptions.map((option) => (
                              <DropdownMenu.Item
                                key={option.id}
                                onSelect={() => setCurrentSortMode(option.id)}
                                className={`
                                  relative flex items-center justify-center px-2 py-1.5 rounded-lg outline-none cursor-pointer transition-colors
                                  ${currentSortMode === option.id
                                    ? 'bg-slate-50 dark:bg-slate-800/60'
                                    : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'}
                                `}
                              >
                                <span className="absolute left-2 h-4 w-4 flex items-center justify-center">
                                  {currentSortMode === option.id ? (
                                    <Check className="w-3.5 h-3.5 text-emerald-600" strokeWidth={3} />
                                  ) : (
                                    <span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" />
                                  )}
                                </span>
                                <span
                                  className={`
                                    text-[10px] font-black uppercase tracking-[0.04em] text-center
                                    ${currentSortMode === option.id ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-700 dark:text-slate-200'}
                                  `}
                                >
                                  {option.label}
                                </span>
                              </DropdownMenu.Item>
                            ))}
                          </DropdownMenu.Content>
                        </DropdownMenu.Portal>
                      </DropdownMenu.Root>

                      <button
                        onClick={handleRefreshPonPanel}
                        disabled={isSidebarRefreshBusy}
                        className="shrink-0 p-2.5 rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-400 hover:text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-95 disabled:opacity-55 disabled:cursor-not-allowed"
                        aria-label={t('Refresh')}
                        title={t('Refresh')}
                      >
                        <RotateCw className={`w-4 h-4 ${isRefreshingPonPanel ? 'animate-spin' : ''}`} strokeWidth={2.5} />
                      </button>
                    </div>
                  </div>

                  {ponPanelError && (
                    <div className="mb-2 rounded-lg border border-rose-200 dark:border-rose-500/40 bg-rose-50/80 dark:bg-rose-500/10 px-3 py-2 text-[11px] font-semibold text-rose-600 dark:text-rose-300">
                      {ponPanelError}
                    </div>
                  )}

                  {activeTab === 'status' ? (
                    <>
                    {/* Desktop status table */}
                    <div className="hidden lg:flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="shrink-0 overflow-hidden pr-[7px] bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '10%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '24%' }} />
                          </colgroup>
                          <thead>
                            <tr className="bg-slate-50 dark:bg-slate-800/90">
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU ID')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Name')}</th>
                              <th className="pl-2.5 pr-4 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Serial')}</th>
                              <th className="pl-4 pr-6 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Status')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Desconexão')}</th>
                            </tr>
                          </thead>
                        </table>
                      </div>
                      <div className="overflow-x-auto overflow-y-auto min-h-0 custom-scrollbar">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '10%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '24%' }} />
                          </colgroup>
                          <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                            {statusRows.map(({ onu, statusKey }) => {
                              const statusLabel = statusKey === 'online'
                                ? t('Online')
                                : statusKey === 'dying_gasp'
                                  ? t('Dying Gasp')
                                  : statusKey === 'link_loss'
                                    ? t('Link Loss')
                                    : statusKey === 'unknown'
                                      ? t('Unknown')
                                      : t('Offline')
                              const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                              const serialValue = onu.serial_number || onu.serial || '—'
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                              const disconnectWindow = statusKey === 'online'
                                ? '—'
                                : formatDisconnectionWindow(
                                    onu.disconnect_window_start,
                                    onu.disconnect_window_end,
                                    i18n.language
                                  )
                              const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                              const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                                (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                                (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                                (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                              ))
                              return (
                                <tr
                                  key={onu.id}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/50 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? SEARCH_ROW_HIGHLIGHT_STYLE : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                      {clientLabel}
                                    </span>
                                  </td>
                                  <td className="pl-2.5 pr-4 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 font-mono whitespace-nowrap tracking-[0.01em]">
                                    {serialValue}
                                  </td>
                                  <td className="pl-4 pr-6 py-0 align-middle whitespace-nowrap">
                                    <span
                                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}
                                    >
                                      <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                      {statusLabel}
                                    </span>
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${statusKey !== 'online' && disconnectWindow === '—' ? 'text-red-500 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`}>
                                    {disconnectWindow}
                                  </td>
                                </tr>
                              )
                            })}
                            {statusRows.length === 0 && (
                              <tr>
                                <td colSpan={5} className="p-8 text-center text-[11px] font-bold text-slate-400 uppercase tracking-widest">
                                  {t('No ONU data available')}
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    {/* Mobile status cards */}
                    <div className="flex lg:hidden flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-1.5">
                        {statusRows.map(({ onu, statusKey }) => {
                          const statusLabel = statusKey === 'online'
                            ? t('Online')
                            : statusKey === 'dying_gasp'
                              ? t('Dying Gasp')
                              : statusKey === 'link_loss'
                                ? t('Link Loss')
                                : statusKey === 'unknown'
                                  ? t('Unknown')
                                  : t('Offline')
                          const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                          const serialValue = onu.serial_number || onu.serial || '—'
                          const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                          const disconnectWindow = statusKey === 'online'
                            ? '—'
                            : formatDisconnectionWindow(
                                onu.disconnect_window_start,
                                onu.disconnect_window_end,
                                i18n.language
                              )
                          const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                          const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                            (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                            (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                            (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                          ))
                          return (
                            <div
                              key={onu.id}
                              data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                              className={`rounded-md border px-3 py-2 flex items-center gap-2 ${isHighlightedFromSearch ? 'border-emerald-400 dark:border-emerald-600 bg-emerald-50/90 dark:bg-emerald-900/25' : 'border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900'}`}
                              style={isHighlightedFromSearch ? { boxShadow: '0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate leading-tight">{clientLabel}</span>
                                <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 font-mono tracking-[0.01em] truncate">{serialValue}</span>
                              </div>
                              <div className="shrink-0 flex flex-col items-end gap-0.5">
                                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}>
                                  <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                  {statusLabel}
                                </span>
                                {statusKey !== 'online' && disconnectWindow !== '—' && <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{disconnectWindow}</span>}
                              </div>
                            </div>
                          )
                        })}
                        {statusRows.length === 0 && (
                          <div className="py-12 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                            {t('No ONU data available')}
                          </div>
                        )}
                      </div>
                    </div>
                    </>

                  ) : (
                    <div className="relative flex flex-col w-full max-h-full min-h-0">
                    {isRefreshingPonPanel && (
                      <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 dark:bg-slate-900/60 backdrop-blur-[1px] rounded-xl transition-opacity duration-200">
                        <div className="w-5 h-5 border-2 border-slate-300 dark:border-slate-600 border-t-emerald-500 dark:border-t-emerald-400 rounded-full animate-spin" />
                      </div>
                    )}
                    {/* Desktop power table */}
                    <div className="hidden lg:flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="shrink-0 overflow-hidden pr-[7px] bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '10%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '24%' }} />
                          </colgroup>
                          <thead>
                            <tr className="bg-slate-50 dark:bg-slate-800/90">
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU ID')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Name')}</th>
                              <th className="pl-2.5 pr-4 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Serial')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Power')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Leitura')}</th>
                            </tr>
                          </thead>
                        </table>
                      </div>
                      <div className="overflow-x-auto overflow-y-auto min-h-0 custom-scrollbar">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '10%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '24%' }} />
                            <col style={{ width: '24%' }} />
                          </colgroup>
                          <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                            {powerRows.map(({ onu, statusKey, onuRx, oltRx, readAt }) => {
                              const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                              const serialValue = onu.serial_number || onu.serial || '—'
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                              const hasOnuRx = onuRx !== null
                              const hasOltRx = oltRx !== null
                              const hasAnyPower = hasOnuRx || (supportsSelectedOltRxPower && hasOltRx)
                              const grayPower = 'text-slate-500 dark:text-slate-400'
                              const onuRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(onuRx, 'onu_rx', selectedPonData?.olt?.id))
                              const oltRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(oltRx, 'olt_rx', selectedPonData?.olt?.id))
                              const isOfflineStatus = statusKey !== 'online'
                              const hasReading = readAt !== null && readAt !== undefined && readAt !== ''
                              const readingAt = formatReadingAt(readAt, i18n.language)
                              const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                              const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                                (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                                (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                                (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                              ))
                              return (
                                <tr
                                  key={`power-${onu.id}`}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/50 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? SEARCH_ROW_HIGHLIGHT_STYLE : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                      {clientLabel}
                                    </span>
                                  </td>
                                  <td className="pl-2.5 pr-4 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 font-mono whitespace-nowrap tracking-[0.01em]">
                                    {serialValue}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle text-center">
                                    {!hasAnyPower ? (
                                      <span className={`inline-block text-[11px] font-semibold tabular-nums ${!isSelectedOltGray && isOfflineStatus ? 'text-red-500 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`}>—</span>
                                    ) : (
                                      <div className="inline-flex flex-col items-center gap-1 leading-snug tabular-nums">
                                        <span className="inline-flex items-center text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                          <span className="inline-block w-8 text-left">{t('ONU')}</span>
                                          <span className={`font-semibold ${onuRxColor}`}>{hasOnuRx ? formatPowerValue(onuRx) : '—'}</span>
                                        </span>
                                        {supportsSelectedOltRxPower && (
                                          <span className="inline-flex items-center text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                            <span className="inline-block w-8 text-left">{t('OLT')}</span>
                                            <span className={`font-semibold ${oltRxColor}`}>{hasOltRx ? formatPowerValue(oltRx) : '—'}</span>
                                          </span>
                                        )}
                                      </div>
                                    )}
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${!isSelectedOltGray && !hasReading && isOfflineStatus ? 'text-red-500 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`}>
                                    {readingAt}
                                  </td>
                                </tr>
                              )
                            })}
                            {powerRows.length === 0 && (
                              <tr>
                                <td colSpan={5} className="p-8 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                                  {t('No ONU data available')}
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    {/* Mobile power cards */}
                    <div className="flex lg:hidden flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-1.5">
                        {powerRows.map(({ onu, statusKey, onuRx, oltRx, readAt }) => {
                          const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                          const serialValue = onu.serial_number || onu.serial || '—'
                          const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                          const hasOnuRx = onuRx !== null
                          const hasOltRx = oltRx !== null
                          const hasAnyPower = hasOnuRx || (supportsSelectedOltRxPower && hasOltRx)
                          const grayPower = 'text-slate-500 dark:text-slate-400'
                          const onuRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(onuRx, 'onu_rx', selectedPonData?.olt?.id))
                          const oltRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(oltRx, 'olt_rx', selectedPonData?.olt?.id))
                          const isOfflineStatus = statusKey !== 'online'
                          const hasReading = readAt !== null && readAt !== undefined && readAt !== ''
                          const readingAt = formatReadingAt(readAt, i18n.language)
                          const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                          const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                            (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                            (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                            (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                          ))
                          return (
                            <div
                              key={`power-${onu.id}`}
                              data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                              className={`rounded-md border px-3 py-2 flex items-center gap-2 ${isHighlightedFromSearch ? 'border-emerald-400 dark:border-emerald-600 bg-emerald-50/90 dark:bg-emerald-900/25' : 'border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900'}`}
                              style={isHighlightedFromSearch ? { boxShadow: '0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate leading-tight">{clientLabel}</span>
                                <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 font-mono tracking-[0.01em] truncate">{serialValue}</span>
                              </div>
                              <div className="shrink-0 flex flex-col items-end gap-0.5">
                                {hasAnyPower ? (
                                  <>
                                    <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                                      <span className="font-mono text-slate-400 dark:text-slate-500">{t('ONU')}</span>
                                      <span className={`font-semibold ${onuRxColor}`}>{hasOnuRx ? formatPowerValue(onuRx) : '—'}</span>
                                    </span>
                                    {supportsSelectedOltRxPower && (
                                      <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                                        <span className="font-mono text-slate-400 dark:text-slate-500">{t('OLT')}</span>
                                        <span className={`font-semibold ${oltRxColor}`}>{hasOltRx ? formatPowerValue(oltRx) : '—'}</span>
                                      </span>
                                    )}
                                    <span className={`text-[10px] font-semibold tabular-nums ${!isSelectedOltGray && !hasReading && isOfflineStatus ? 'text-red-500 dark:text-red-400' : 'text-slate-400 dark:text-slate-500'}`}>{readingAt}</span>
                                  </>
                                ) : (
                                  <span className={`text-[11px] font-semibold tabular-nums ${!isSelectedOltGray && isOfflineStatus ? 'text-red-500 dark:text-red-400' : 'text-slate-500 dark:text-slate-400'}`}>—</span>
                                )}
                              </div>
                            </div>
                          )
                        })}
                        {powerRows.length === 0 && (
                          <div className="py-12 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                            {t('No ONU data available')}
                          </div>
                        )}
                      </div>
                    </div>
                    </div>
                  )}
                </div>
              </div>
              )
            })()}
          </aside>
        )}
      </main>
      <footer className="shrink-0 flex items-center justify-between px-4 py-1.5 text-[11px] font-medium text-slate-400 dark:text-slate-400 border-t border-slate-100 dark:border-slate-700/50 bg-white dark:bg-slate-900 transition-colors">
        <span>{t('Version')} {__APP_VERSION__}</span>
        <span>{lastCollectionAt ? `${t('Last update')}: ${formatReadingAt(lastCollectionAt, i18n.language)}` : ''}</span>
      </footer>
    </div>
  )
}

export default App
