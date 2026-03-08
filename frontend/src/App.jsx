import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  ArrowDownUp,
  Zap,
  History
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { LoginPage } from './components/LoginPage'
import { VarunaIcon } from './components/VarunaIcon'
import { NetworkTopology } from './components/NetworkTopology'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import api, { updatePonDescription } from './services/api'
import { InlineEditableText } from './components/InlineEditableText'
import { classifyOnu, getOnuStats } from './utils/stats'
import { deriveOltHealthState } from './utils/oltHealth'
import { getPowerColor, powerColorClass } from './utils/powerThresholds'
import { getApiErrorMessage } from './utils/apiErrorMessages'
import { MISSING_VALUE_PLACEHOLDER, PLACEHOLDER_CLASS } from './utils/placeholders'

const SettingsPanel = lazy(() =>
  import('./components/SettingsPanel').then((module) => ({ default: module.SettingsPanel }))
)
const PowerReport = lazy(() =>
  import('./components/PowerReport').then((module) => ({ default: module.PowerReport }))
)
const AlarmHistory = lazy(() =>
  import('./components/AlarmHistory').then((module) => ({ default: module.AlarmHistory }))
)

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
const SELECTED_OLT_IDS_STORAGE_KEY = 'varuna.selectedOltIds'
const THEME_STORAGE_KEY = 'varuna.theme'

const formatPowerValue = (value) => {
  if (value === null || value === undefined || value === '') return MISSING_VALUE_PLACEHOLDER
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return MISSING_VALUE_PLACEHOLDER
  return `${numeric.toFixed(2)} dBm`
}


const getDisplaySerial = (onu) => {
  const raw = String(onu?.serial_number ?? onu?.serial ?? '').trim()
  if (!raw || raw === '-' || raw === MISSING_VALUE_PLACEHOLDER) {
    return {
      serialValue: MISSING_VALUE_PLACEHOLDER,
      hasSerial: false
    }
  }
  return {
    serialValue: raw,
    hasSerial: true
  }
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

const arePonStatsEqual = (left = {}, right = {}) => {
  return (
    Number(left.total || 0) === Number(right.total || 0) &&
    Number(left.online || 0) === Number(right.online || 0) &&
    Number(left.offline || 0) === Number(right.offline || 0) &&
    Number(left.linkLoss || 0) === Number(right.linkLoss || 0) &&
    Number(left.dyingGasp || 0) === Number(right.dyingGasp || 0) &&
    Number(left.unknown || 0) === Number(right.unknown || 0)
  )
}

const enrichTopologyWithPonStats = (olts) => {
  return asList(olts).map((olt) => {
    const slots = asList(olt?.slots)
    if (!slots.length) return olt

    let oltChanged = false
    const nextSlots = slots.map((slot) => {
      const pons = asList(slot?.pons)
      if (!pons.length) return slot

      let slotChanged = false
      const nextPons = pons.map((pon) => {
        const stats = getOnuStats(asList(pon?.onus))
        if (arePonStatsEqual(pon?.stats, stats)) return pon
        slotChanged = true
        return {
          ...pon,
          stats
        }
      })

      if (!slotChanged) return slot
      oltChanged = true
      return {
        ...slot,
        pons: nextPons
      }
    })

    if (!oltChanged) return olt
    return {
      ...olt,
      slots: nextSlots
    }
  })
}

const LONG_RUNNING_ACTION_TIMEOUT_MS = 180_000
const RESUME_REFRESH_THROTTLE_MS = 4000
const TOPOLOGY_REFRESH_DEFAULT_INTERVAL_MS = 30_000
const TOPOLOGY_REFRESH_RECOVERY_INTERVAL_MS = 5_000
const SEARCH_HIGHLIGHT_STYLE = {
  boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.6)',
  background: 'rgba(16, 185, 129, 0.06)',
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

const patchPonStatusRows = (olts, target, rows) => {
  const rowMap = new Map(
    asList(rows)
      .filter((row) => row?.id != null)
      .map((row) => [String(row.id), row])
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
            status: row.status ?? onu.status,
            disconnect_reason: row.disconnect_reason ?? null,
            offline_since: row.offline_since ?? null,
            disconnect_window_start: row.disconnect_window_start ?? null,
            disconnect_window_end: row.disconnect_window_end ?? null,
          }
        })

        return {
          ...pon,
          onus: nextOnus,
          stats: getOnuStats(nextOnus)
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

const mergeTopologyPowerSnapshots = (previousOlts, nextOlts) => {
  const previousPowerByOnuId = new Map()

  asList(previousOlts).forEach((olt) => {
    asList(olt?.slots).forEach((slot) => {
      asList(slot?.pons).forEach((pon) => {
        asList(pon?.onus).forEach((onu) => {
          const onuId = onu?.id
          if (onuId == null) return

          const snapshot = {
            onu_rx_power: onu?.onu_rx_power ?? null,
            olt_rx_power: onu?.olt_rx_power ?? null,
            power_read_at: onu?.power_read_at ?? null
          }
          const hasSnapshot =
            snapshot.onu_rx_power !== null ||
            snapshot.olt_rx_power !== null ||
            Boolean(snapshot.power_read_at)

          if (hasSnapshot) {
            previousPowerByOnuId.set(String(onuId), snapshot)
          }
        })
      })
    })
  })

  if (!previousPowerByOnuId.size) return nextOlts

  let changed = false

  const mergedOlts = asList(nextOlts).map((olt) => {
    let oltChanged = false

    const nextSlots = asList(olt?.slots).map((slot) => {
      let slotChanged = false

      const nextPons = asList(slot?.pons).map((pon) => {
        let ponChanged = false

        const nextOnus = asList(pon?.onus).map((onu) => {
          const previousSnapshot = previousPowerByOnuId.get(String(onu?.id))
          if (!previousSnapshot) return onu

          const hasCurrentSnapshot =
            onu?.onu_rx_power !== null && onu?.onu_rx_power !== undefined ||
            onu?.olt_rx_power !== null && onu?.olt_rx_power !== undefined ||
            Boolean(onu?.power_read_at)

          if (hasCurrentSnapshot) return onu

          changed = true
          oltChanged = true
          slotChanged = true
          ponChanged = true

          return {
            ...onu,
            ...previousSnapshot
          }
        })

        if (!ponChanged) return pon
        return {
          ...pon,
          onus: nextOnus
        }
      })

      if (!slotChanged) return slot
      return {
        ...slot,
        pons: nextPons
      }
    })

    if (!oltChanged) return olt
    return {
      ...olt,
      slots: nextSlots
    }
  })

  return changed ? mergedOlts : nextOlts
}

const formatDisconnectionWindow = (startValue, endValue, language) => {
  const anchorValue = endValue || startValue
  if (!anchorValue) return MISSING_VALUE_PLACEHOLDER

  const anchor = new Date(anchorValue)
  if (Number.isNaN(anchor.getTime())) return MISSING_VALUE_PLACEHOLDER

  const locale = language === 'pt' ? 'pt-BR' : 'en-US'
  const timestampFormatter = new Intl.DateTimeFormat(locale, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })

  return timestampFormatter.format(anchor)
}

const formatReadingAt = (value, language) => {
  if (!value) return MISSING_VALUE_PLACEHOLDER
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
      const onus = asList(pon?.onus).map((onu) => ({
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
      return {
        id: ponId,
        db_id: pon.id,
        pon_number: pon.pon_id,
        pon_id: pon.pon_id,
        pon_key: pon.pon_key,
        name: pon.pon_name,
        description: pon.description || '',
        onus,
        stats: getOnuStats(onus)
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
        if (String(pon?.id) === String(ponId) || String(pon?.db_id) === String(ponId)) {
          return { olt, slot, pon }
        }
      }
    }
  }
  return null
}

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

const LazyPanelFallback = () => (
  <div className="flex w-full items-center justify-center py-20 text-slate-400 dark:text-slate-500">
    <RotateCw className="h-4 w-4 animate-spin" strokeWidth={2.5} />
  </div>
)

const App = () => {
  const { t, i18n } = useTranslation()
  const [authToken, setAuthToken] = useState(() => localStorage.getItem('auth_token'))
  const [authUser, setAuthUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const authRole = String(authUser?.role || '').toLowerCase()
  const canManageSettings = Boolean(
    authUser?.can_modify_settings ?? (authRole === 'admin')
  )
  const canOperateTopology = Boolean(
    authUser?.can_operate_topology ?? (authRole === 'admin' || authRole === 'operator')
  )

  useEffect(() => {
    if (!authToken) {
      setAuthChecked(true)
      return
    }
    api.get('/auth/me/')
      .then((res) => {
        setAuthUser(res.data)
        setAuthChecked(true)
      })
      .catch(() => {
        localStorage.removeItem('auth_token')
        setAuthToken(null)
        setAuthChecked(true)
      })
  }, [authToken])

  const handleLogin = useCallback(async (username, password) => {
    const res = await api.post('/auth/login/', { username, password })
    localStorage.setItem('auth_token', res.data.token)
    setAuthToken(res.data.token)
    setAuthUser(res.data.user)
  }, [])

  const handleLogout = useCallback(() => {
    api.post('/auth/logout/').catch(() => {})
    localStorage.removeItem('auth_token')
    setAuthToken(null)
    setAuthUser(null)
  }, [])

  const [selectedPonId, setSelectedPonId] = useState(() => {
    try {
      if (typeof window === 'undefined') return null
      const saved = window.localStorage.getItem(SELECTED_PON_STORAGE_KEY)
      return saved ? String(saved) : null
    } catch {
      return null
    }
  })
  const [ponHighlightTarget, setPonHighlightTarget] = useState(null)
  const [isDarkMode, setIsDarkMode] = useState(() => {
    try {
      if (typeof window === 'undefined') return false
      const saved = window.localStorage.getItem(THEME_STORAGE_KEY)
      if (saved === 'dark') return true
      if (saved === 'light') return false
    } catch {
      // noop
    }
    return false
  })
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
    return ['topology', 'power-report', 'alarm-history', 'settings'].includes(saved) ? saved : 'topology'
  })
  const settingsOpen = activeNav === 'settings'

  useEffect(() => {
    localStorage.setItem('varuna_active_tab', activeNav)
  }, [activeNav])

  // Clean up stale localStorage key from removed universal search
  useEffect(() => {
    try { localStorage.removeItem('varuna.searchMatch') } catch {}
  }, [])

  useEffect(() => {
    if (!authChecked || !authToken) return
    if (!canManageSettings && settingsOpen) {
      setActiveNav('topology')
    }
  }, [authChecked, authToken, canManageSettings, settingsOpen])

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      if (selectedPonId) {
        window.localStorage.setItem(SELECTED_PON_STORAGE_KEY, String(selectedPonId))
      } else {
        window.localStorage.removeItem(SELECTED_PON_STORAGE_KEY)
      }
    } catch {
      // noop
    }
  }, [selectedPonId])
  const [isResizingPonPanel, setIsResizingPonPanel] = useState(false)
  const [ponPanelWidth, setPonPanelWidth] = useState(() => {
    try {
      if (typeof window === 'undefined') return 42
      const saved = window.localStorage.getItem('varuna.ponSidebarWidth')
      return clampPonPanelWidth(saved ?? 42)
    } catch {
      return 42
    }
  })
  const [selectedOltIds, setSelectedOltIds] = useState(() => {
    try {
      if (typeof window === 'undefined') return []
      const saved = window.localStorage.getItem(SELECTED_OLT_IDS_STORAGE_KEY)
      return saved ? JSON.parse(saved) : []
    } catch {
      return []
    }
  })
  const selectedOltIdsInitializedRef = useRef(false)
  const [olts, setOlts] = useState([])
  const [vendorProfiles, setVendorProfiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [vendorLoading, setVendorLoading] = useState(false)
  const [error, setError] = useState(null)
  const [settingsActionError, setSettingsActionError] = useState(null)
  const [settingsActionMessage, setSettingsActionMessage] = useState(null)
  const [settingsActionBusy, setSettingsActionBusy] = useState({})
  const [isRefreshingPonPanel, setIsRefreshingPonPanel] = useState(false)
  const [isLoadingPonPowerSnapshot, setIsLoadingPonPowerSnapshot] = useState(false)
  const [refreshCooldownActive, setRefreshCooldownActive] = useState(false)
  const refreshCooldownTimerRef = useRef(null)
  const [ponPanelError, setPonPanelError] = useState('')
  const ponPanelErrorTimerRef = useRef(null)
  useEffect(() => () => clearTimeout(ponPanelErrorTimerRef.current), [])
  useEffect(() => { setRefreshCooldownActive(false); clearTimeout(refreshCooldownTimerRef.current) }, [selectedPonId])
  const [healthTick, setHealthTick] = useState(() => Date.now())
  const oltsRef = useRef([])
  const selectedPonMissingCyclesRef = useRef(0)
  const wasAlarmEnabledRef = useRef(false)
  const mainLayoutRef = useRef(null)
  const lastResumeRefreshAtRef = useRef(0)
  const resizePointerIdRef = useRef(null)
  const previousBodyCursorRef = useRef('')
  const previousBodyUserSelectRef = useRef('')
  const previousHtmlCursorRef = useRef('')
  const fetchOltsInflightRef = useRef({})
  const powerSnapshotLoadKeyRef = useRef('')

  useEffect(() => {
    try {
      if (typeof window !== 'undefined') {
        window.localStorage.setItem(THEME_STORAGE_KEY, isDarkMode ? 'dark' : 'light')
      }
    } catch {
      // noop
    }
    document.documentElement.classList.toggle('dark', isDarkMode)
  }, [isDarkMode])

  const showPonPanelError = useCallback((message) => {
    setPonPanelError(message)
    clearTimeout(ponPanelErrorTimerRef.current)
    ponPanelErrorTimerRef.current = setTimeout(() => setPonPanelError(''), 6000)
  }, [])

  const fetchOlts = useCallback(async ({ surfaceError = false, includeTopology = true } = {}) => {
    const requestKey = includeTopology ? 'topology' : 'base'
    if (fetchOltsInflightRef.current[requestKey]) return fetchOltsInflightRef.current[requestKey]
    const run = async () => {
      const hasCurrentTopology = oltsRef.current.some((olt) => asList(olt?.slots).length > 0)
      const shouldBlockRender = includeTopology ? !hasCurrentTopology : !oltsRef.current.length
      if (shouldBlockRender) setLoading(true)
      if (shouldBlockRender || surfaceError) setError(null)
      try {
        if (!includeTopology) {
          const res = await api.get('/olts/')
          setOlts(normalizeList(res.data))
          return { ok: true, includeTopology: false }
        }

        const res = await api.get('/olts/', { params: { include_topology: 'true' } })
        const nextOlts = enrichTopologyWithPonStats(normalizeList(res.data))
        setOlts((previous) => mergeTopologyPowerSnapshots(previous, nextOlts))
        return { ok: true, includeTopology: true }
      } catch (err) {
        if (!includeTopology) {
          const message = getApiErrorMessage(err, t('Failed to load OLT data'), t)
          if (shouldBlockRender || surfaceError) {
            setError(message)
          }
          return { ok: false, message }
        }

        try {
          const base = await api.get('/olts/')
          const baseOlts = normalizeList(base.data)
          const enriched = await Promise.all(
            baseOlts.map(async (olt) => {
              try {
                const topoRes = await api.get(`/olts/${olt.id}/topology/`)
                return mapTopologyToSlots(olt, topoRes.data)
              } catch {
                return olt
              }
            })
          )
          setOlts((previous) => mergeTopologyPowerSnapshots(previous, enrichTopologyWithPonStats(enriched)))
          return { ok: true, usedFallback: true, includeTopology: true }
        } catch (fallbackErr) {
          const message = getApiErrorMessage(err, getApiErrorMessage(fallbackErr, t('Failed to load OLT data'), t), t)
          if (shouldBlockRender || surfaceError) {
            setError(message)
          }
          return { ok: false, message }
        }
      } finally {
        if (shouldBlockRender) setLoading(false)
        delete fetchOltsInflightRef.current[requestKey]
      }
    }
    fetchOltsInflightRef.current[requestKey] = run()
    return fetchOltsInflightRef.current[requestKey]
  }, [t])

  const fetchVendorProfiles = useCallback(async () => {
    setVendorLoading(true)
    try {
      const res = await api.get('/vendor-profiles/')
      setVendorProfiles(normalizeList(res.data))
    } catch {
      // Keep previously loaded vendor profiles if refresh fails.
    } finally {
      setVendorLoading(false)
    }
  }, [])

  const hasGrayOlt = useMemo(() => {
    const now = Date.now()
    return olts.some((olt) => deriveOltHealthState(olt, now).state === 'gray')
  }, [olts, healthTick])

  useEffect(() => {
    if (!authToken) return
    void fetchOlts({ includeTopology: activeNav === 'topology' })

    if (canManageSettings) {
      void fetchVendorProfiles()
    } else {
      setVendorProfiles([])
    }

    const refreshIntervalMs = hasGrayOlt
      ? TOPOLOGY_REFRESH_RECOVERY_INTERVAL_MS
      : TOPOLOGY_REFRESH_DEFAULT_INTERVAL_MS

    const interval = setInterval(() => {
      void fetchOlts({ includeTopology: activeNav === 'topology' })
    }, refreshIntervalMs)
    return () => {
      clearInterval(interval)
    }
  }, [activeNav, authToken, canManageSettings, fetchOlts, fetchVendorProfiles, hasGrayOlt])

  useEffect(() => {
    oltsRef.current = olts
  }, [olts])

  useEffect(() => {
    const allIds = olts.map((olt) => String(olt.id))
    setSelectedOltIds((prev) => {
      if (!selectedOltIdsInitializedRef.current) {
        if (!allIds.length) return prev
        selectedOltIdsInitializedRef.current = true
        return prev.length ? prev.filter((id) => allIds.includes(id)) : allIds
      }
      return prev.filter((id) => allIds.includes(id))
    })
  }, [olts])

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      window.localStorage.setItem(SELECTED_OLT_IDS_STORAGE_KEY, JSON.stringify(selectedOltIds))
    } catch {
      // noop
    }
  }, [selectedOltIds])

  useEffect(() => {
    const healthIntervalMs = hasGrayOlt
      ? TOPOLOGY_REFRESH_RECOVERY_INTERVAL_MS
      : TOPOLOGY_REFRESH_DEFAULT_INTERVAL_MS
    const timer = setInterval(() => setHealthTick(Date.now()), healthIntervalMs)
    return () => clearInterval(timer)
  }, [hasGrayOlt])

  useEffect(() => {
    if (!authToken) return

    const refreshAfterResume = () => {
      const now = Date.now()
      if (now - lastResumeRefreshAtRef.current < RESUME_REFRESH_THROTTLE_MS) return
      lastResumeRefreshAtRef.current = now
      setHealthTick(now)
      void fetchOlts({ includeTopology: activeNav === 'topology' })
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return
      refreshAfterResume()
    }

    const handleWindowFocus = () => {
      refreshAfterResume()
    }

    const handlePageShow = (event) => {
      if (event.persisted || document.visibilityState === 'visible') {
        refreshAfterResume()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('focus', handleWindowFocus)
    window.addEventListener('pageshow', handlePageShow)

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('focus', handleWindowFocus)
      window.removeEventListener('pageshow', handlePageShow)
    }
  }, [activeNav, authToken, canManageSettings, fetchOlts])

  const runSettingsAction = async (key, request, successMessage = '', { oltId = null } = {}) => {
    setSettingsActionError(null)
    setSettingsActionMessage(null)
    setSettingsActionBusy((prev) => ({ ...prev, [key]: true }))

    try {
      const result = await request()
      if (successMessage) {
        setSettingsActionMessage({ oltId, message: successMessage })
        setTimeout(() => setSettingsActionMessage(null), 4000)
      }
      return result
    } catch (err) {
      setSettingsActionError(getApiErrorMessage(err, t('Failed to execute settings action'), t))
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

  const runQueuedSettingsAction = async ({ endpoint, acceptedMessage, alreadyRunningMessage, oltId = null }) => {
    setSettingsActionError(null)
    setSettingsActionMessage(null)
    try {
      const response = await api.post(endpoint, { background: true })
      const queuedStatus = response?.data?.status
      if (queuedStatus === 'already_running') {
        setSettingsActionMessage({ oltId, message: alreadyRunningMessage || acceptedMessage })
      } else {
        setSettingsActionMessage({ oltId, message: acceptedMessage })
      }
      setTimeout(() => setSettingsActionMessage(null), 4000)
      return response?.data || null
    } catch (err) {
      setSettingsActionError(getApiErrorMessage(err, t('Failed to execute settings action'), t))
      setTimeout(() => setSettingsActionError(null), 5000)
      return null
    }
  }

  const createOlt = async (payload) => {
    const created = await runSettingsAction(
      'create',
      async () => {
        const response = await api.post('/olts/', payload)
        fetchOlts({ includeTopology: activeNav === 'topology' })
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
        fetchOlts({ includeTopology: activeNav === 'topology' })
        return response.data
      },
      t('OLT updated successfully'),
      { oltId }
    )
    return updated
  }

  const deleteOlt = async (oltId) => {
    const removed = await runSettingsAction(
      `delete:${oltId}`,
      async () => {
        await api.delete(`/olts/${oltId}/`)
        fetchOlts({ includeTopology: activeNav === 'topology' })
        return true
      },
      t('OLT removed successfully'),
      { oltId }
    )
    return Boolean(removed)
  }

  const runDiscovery = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/run_discovery/`,
      acceptedMessage: t('Discovery queued successfully'),
      alreadyRunningMessage: t('Discovery already running'),
      oltId,
    })
  }

  const runPolling = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/run_polling/`,
      acceptedMessage: t('Polling queued successfully'),
      alreadyRunningMessage: t('Polling already running'),
      oltId,
    })
  }

  const refreshPower = async (oltId) => {
    await runQueuedSettingsAction({
      endpoint: `/olts/${oltId}/refresh_power/`,
      acceptedMessage: t('Power refresh queued successfully'),
      alreadyRunningMessage: t('Power refresh already running'),
      oltId,
    })
  }

  const rawSelectedPonData = useMemo(() => {
    if (!selectedPonId) return null
    return findPonById(olts, selectedPonId)
  }, [olts, selectedPonId])

  const selectedPonData = rawSelectedPonData


  const collectPowerForSelectedPon = useCallback(async ({ refresh = true } = {}) => {
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
        refresh
      },
      { timeout: LONG_RUNNING_ACTION_TIMEOUT_MS }
    )
    const rows = asList(response.data?.results)
    setOlts((previous) => patchPonPowerRows(previous, { oltId, slotNumber, ponNumber }, rows))
    return { ok: true }
  }, [selectedPonData, t])

  const collectStatusForSelectedPon = useCallback(async () => {
    const oltId = toIntOrNull(selectedPonData?.olt?.id)
    const slotNumber = toIntOrNull(selectedPonData?.slot?.slot_number ?? selectedPonData?.slot?.slot_id)
    const ponNumber = toIntOrNull(selectedPonData?.pon?.pon_number ?? selectedPonData?.pon?.pon_id)

    if (!oltId || slotNumber == null || ponNumber == null) {
      return { ok: false, message: t('Failed to refresh status data') }
    }

    const response = await api.post(
      '/onu/batch-status/',
      {
        olt_id: oltId,
        slot_id: slotNumber,
        pon_id: ponNumber,
        refresh: true
      },
      { timeout: LONG_RUNNING_ACTION_TIMEOUT_MS }
    )
    const rows = asList(response.data?.results)
    setOlts((previous) => patchPonStatusRows(previous, { oltId, slotNumber, ponNumber }, rows))
    return { ok: true }
  }, [selectedPonData, t])

  const handleRefreshPonPanel = useCallback(async () => {
    if (!canOperateTopology || isRefreshingPonPanel || refreshCooldownActive) return

    setPonPanelError('')
    setIsRefreshingPonPanel(true)
    try {
      let shouldReloadTopology = true
      if (activeTab === 'status') {
        const statusResult = await collectStatusForSelectedPon()
        if (!statusResult?.ok) {
          showPonPanelError(statusResult?.message || t('Failed to refresh status data'))
        } else {
          shouldReloadTopology = false
        }
      } else if (activeTab === 'power') {
        const powerResult = await collectPowerForSelectedPon()
        if (!powerResult?.ok) {
          showPonPanelError(powerResult?.message || t('Failed to refresh power data'))
        } else {
          shouldReloadTopology = false
        }
      }

      if (shouldReloadTopology) {
        const result = await fetchOlts({ surfaceError: false })
        if (!result?.ok) {
          showPonPanelError(result?.message || t('Failed to refresh panel data'))
        }
      }
    } catch (err) {
      const fallback = activeTab === 'power'
        ? t('Failed to refresh power data')
        : t('Failed to refresh status data')
      showPonPanelError(getApiErrorMessage(err, fallback, t))
    } finally {
      setIsRefreshingPonPanel(false)
      setRefreshCooldownActive(true)
      clearTimeout(refreshCooldownTimerRef.current)
      refreshCooldownTimerRef.current = setTimeout(() => setRefreshCooldownActive(false), 5000)
    }
  }, [activeTab, canOperateTopology, collectPowerForSelectedPon, collectStatusForSelectedPon, fetchOlts, isRefreshingPonPanel, refreshCooldownActive, showPonPanelError, t])

  useEffect(() => {
    if (activeNav !== 'topology') return
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
      setPonHighlightTarget(null)
      setSelectedPonId(null)
    }
  }, [activeNav, selectedPonId, rawSelectedPonData, loading, error, olts.length])

  const selectedSlotNumber = selectedPonData?.slot?.slot_number ?? selectedPonData?.slot?.slot_id
  const selectedPonNumber = selectedPonData?.pon?.pon_number ?? selectedPonData?.pon?.pon_id
  const selectedPonPath = [
    selectedPonData?.olt?.name || 'OLT',
    `${t('SLOT')} ${selectedSlotNumber ?? MISSING_VALUE_PLACEHOLDER}`,
    `PON ${selectedPonNumber ?? MISSING_VALUE_PLACEHOLDER}`
  ]
  const isPonPanelOpen = activeNav === 'topology' && Boolean(selectedPonId)

  const oltHealthById = useMemo(() => {
    return olts.reduce((acc, olt) => {
      acc[String(olt.id)] = deriveOltHealthState(olt, healthTick)
      return acc
    }, {})
  }, [olts, healthTick])

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
    } catch {
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
      if (isPonPanelOpen) {
        event.preventDefault()
        if (isResizingPonPanel) {
          stopPonPanelResize()
        }
        setPonHighlightTarget(null)
        setSelectedPonId(null)
      }
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

  const powerSortOptions = useMemo(() => [
    { id: 'default', label: t('Default order') },
    { id: 'worst_onu_rx', label: 'ONU RX ↓' },
    { id: 'worst_olt_rx', label: 'OLT RX ↓' },
    { id: 'best_onu_rx', label: 'ONU RX ↑' },
    { id: 'best_olt_rx', label: 'OLT RX ↑' },
  ], [t])

  const activeSortOptions = activeTab === 'power' ? powerSortOptions : statusSortOptions
  const currentSortMode = activeTab === 'power' ? powerSortMode : statusSortMode
  const currentSortLabel = activeSortOptions.find((option) => option.id === currentSortMode)?.label || activeSortOptions[0]?.label || t('ONU ID')
  const isSidebarRefreshBusy = isRefreshingPonPanel || isLoadingPonPowerSnapshot || refreshCooldownActive
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

  useEffect(() => {
    if (!selectedPonId || !alarmSortConfig.enabled) return
    const nextMode = resolveStatusSortMode('offline')
    setStatusSortMode((prev) => (prev === nextMode ? prev : nextMode))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPonId])

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

    const offlineStatuses = ['link_loss', 'dying_gasp', 'unknown']
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

  const selectedPonStats = useMemo(() => getOnuStats(selectedOnus), [selectedOnus])

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
      const secondaryKey = primaryKey === 'oltRx' ? 'onuRx' : 'oltRx'
      return [...baseRows].sort((a, b) => {
        const primaryDelta = compareNullable(a[primaryKey], b[primaryKey], direction)
        if (primaryDelta !== 0) return primaryDelta

        const secondaryDelta = compareNullable(a[secondaryKey], b[secondaryKey], direction)
        if (secondaryDelta !== 0) return secondaryDelta

        return a.onuNumber - b.onuNumber
      })
    }

    if (powerSortMode === 'default') return [...baseRows].sort((a, b) => a.onuNumber - b.onuNumber)
    if (powerSortMode === 'worst_olt_rx') return sortBy('oltRx', 'asc')
    if (powerSortMode === 'worst_onu_rx') return sortBy('onuRx', 'asc')
    if (powerSortMode === 'best_olt_rx') return sortBy('oltRx', 'desc')
    if (powerSortMode === 'best_onu_rx') return sortBy('onuRx', 'desc')
    return [...baseRows].sort((a, b) => a.onuNumber - b.onuNumber)
  }, [selectedOnus, powerSortMode])

  const powerSignalCounts = useMemo(() => {
    const oltId = selectedPonData?.olt?.id
    const counts = { good: 0, warning: 0, critical: 0, noReading: 0 }
    powerRows.forEach(({ onuRx, oltRx }) => {
      const onuColor = getPowerColor(onuRx, 'onu_rx', oltId)
      const oltColor = getPowerColor(oltRx, 'olt_rx', oltId)
      const colors = [onuColor, oltColor].filter(Boolean)
      if (!colors.length) { counts.noReading += 1; return }
      if (colors.includes('red')) counts.critical += 1
      else if (colors.includes('yellow')) counts.warning += 1
      else counts.good += 1
    })
    return counts
  }, [powerRows, selectedPonData])

  useEffect(() => {
    if (!ponHighlightTarget) return
    if (!selectedPonId) return

    const scrollToHighlight = () => {
      const rows = document.querySelectorAll('[data-onu-highlight="true"]')
      for (const row of rows) {
        if (row.offsetParent !== null) {
          row.scrollIntoView({ behavior: 'smooth', block: 'center' })
          break
        }
      }
    }
    const frame = requestAnimationFrame(() => {
      setTimeout(scrollToHighlight, 120)
    })
    return () => cancelAnimationFrame(frame)
  }, [ponHighlightTarget, selectedPonId, selectedOnus, activeTab])

  const isSelectedOltGray = useMemo(() => {
    const oltId = selectedPonData?.olt?.id
    if (!oltId) return false
    const health = oltHealthById?.[String(oltId)]
    return health?.state === 'gray'
  }, [selectedPonData, oltHealthById])

  const selectedPonOnuSignature = useMemo(() => {
    return selectedOnus.map((onu) => String(onu?.id ?? '')).join(',')
  }, [selectedOnus])

  const hasSelectedPonPowerSnapshot = useMemo(() => {
    return selectedOnus.some((onu) => (
      (onu?.onu_rx_power !== null && onu?.onu_rx_power !== undefined) ||
      (onu?.olt_rx_power !== null && onu?.olt_rx_power !== undefined) ||
      Boolean(onu?.power_read_at)
    ))
  }, [selectedOnus])

  useEffect(() => {
    if (activeNav !== 'topology' || activeTab !== 'power') return
    if (!selectedPonId || !selectedPonData?.olt?.id) return

    const loadKey = `${selectedPonId}:${selectedPonOnuSignature}`
    if (hasSelectedPonPowerSnapshot) {
      powerSnapshotLoadKeyRef.current = loadKey
      return
    }
    if (powerSnapshotLoadKeyRef.current === loadKey) return

    powerSnapshotLoadKeyRef.current = loadKey
    let cancelled = false
    setIsLoadingPonPowerSnapshot(true)
    setPonPanelError('')

    const loadSnapshot = async () => {
      try {
        const result = await collectPowerForSelectedPon({ refresh: false })
        if (cancelled) return
        if (!result?.ok) {
          powerSnapshotLoadKeyRef.current = ''
          showPonPanelError(result?.message || t('Failed to load power data'))
        }
      } catch (err) {
        if (cancelled) return
        powerSnapshotLoadKeyRef.current = ''
        showPonPanelError(getApiErrorMessage(err, t('Failed to load power data'), t))
      } finally {
        if (!cancelled) {
          setIsLoadingPonPowerSnapshot(false)
        }
      }
    }

    void loadSnapshot()

    return () => {
      cancelled = true
      setIsLoadingPonPowerSnapshot(false)
    }
  }, [activeNav, activeTab, selectedPonData, selectedPonId, selectedPonOnuSignature, hasSelectedPonPowerSnapshot, collectPowerForSelectedPon, showPonPanelError, t])

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
    return 'bg-slate-100 text-slate-600 ring-1 ring-inset ring-slate-200 dark:bg-slate-700/50 dark:text-slate-200 dark:ring-slate-500/40'
  }

  const statusDot = (statusKey) => {
    if (isSelectedOltGray) return 'bg-slate-400 dark:bg-slate-500'
    if (statusKey === 'online') return 'bg-emerald-500'
    if (statusKey === 'dying_gasp') return 'bg-blue-500'
    if (statusKey === 'link_loss') return 'bg-rose-500'
    if (statusKey === 'unknown') return 'bg-purple-500'
    return 'bg-slate-400'
  }

  const disconnectWindowClass = (statusKey, disconnectWindow) => {
    if (disconnectWindow !== MISSING_VALUE_PLACEHOLDER) {
      return 'text-slate-500 dark:text-slate-400'
    }
    return 'text-slate-300 dark:text-slate-600'
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

  if (!authChecked) {
    return <div className="h-[100dvh] min-h-[100dvh] bg-white dark:bg-slate-950" />
  }

  if (!authToken || !authUser) {
    return <LoginPage onLogin={handleLogin} />
  }

  return (
    <div className="h-[100dvh] min-h-[100dvh] bg-white dark:bg-slate-950 flex flex-col font-sans transition-colors duration-300">
      <nav className="h-16 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-700/50 flex items-center px-3 lg:px-6 sticky top-0 z-[100] transition-colors shadow-sm gap-2 lg:gap-0">
        <div className="flex items-center gap-3 mr-2 lg:mr-4">
          <div className="w-9 h-9 bg-emerald-600 rounded-lg flex items-center justify-center shadow-lg shadow-emerald-500/20 shrink-0">
            <VarunaIcon className="w-6 h-6 text-white" />
          </div>
          <span className="text-[12px] font-black text-slate-900 dark:text-white tracking-widest uppercase hidden md:block">VARUNA</span>
        </div>

        <div className="flex items-center gap-1 h-full lg:ml-4">
          <button
            onClick={() => setActiveNav('topology')}
            className={`flex items-center justify-center gap-2.5 px-3 lg:px-4 h-full transition-all relative group ${activeNav === 'topology' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <Network className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider whitespace-nowrap hidden sm:block">{t('Topology')}</span>
            {activeNav === 'topology' && <div className="absolute bottom-0 left-2 right-1.5 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
          <button
            onClick={() => setActiveNav('power-report')}
            className={`flex items-center justify-center gap-2.5 px-3 lg:px-4 h-full transition-all relative group ${activeNav === 'power-report' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <Zap className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider whitespace-nowrap hidden sm:block">{t('Power Report')}</span>
            {activeNav === 'power-report' && <div className="absolute bottom-0 left-2 right-1.5 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
          <button
            onClick={() => setActiveNav('alarm-history')}
            className={`flex items-center justify-center gap-2.5 px-3 lg:px-4 h-full transition-all relative group ${activeNav === 'alarm-history' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <History className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider whitespace-nowrap hidden sm:block">{t('Alarm History')}</span>
            {activeNav === 'alarm-history' && <div className="absolute bottom-0 left-2 right-1.5 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
        </div>

        <div className="flex items-center gap-1 ml-auto">
          {canManageSettings && (
            <button
              onClick={() => setActiveNav('settings')}
              className={`flex items-center justify-center gap-2.5 px-3 lg:px-4 h-16 transition-all relative group ${activeNav === 'settings' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
            >
              <SettingsIcon className="w-[18px] h-[18px] shrink-0" />
              <span className="text-[12px] font-black uppercase tracking-wider whitespace-nowrap hidden sm:block">{t('Settings')}</span>
              {activeNav === 'settings' && <div className="absolute bottom-0 left-2 right-1.5 h-0.5 bg-emerald-600 rounded-t-full" />}
            </button>
          )}
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
                className="w-[240px] bg-white dark:bg-slate-900 rounded-xl p-1.5 shadow-xl shadow-black/8 dark:shadow-black/30 border border-slate-200/60 dark:border-slate-700/50 z-[200] animate-in fade-in zoom-in-95 duration-200"
                sideOffset={8}
                align="end"
              >
                {/* User identity */}
                <div className="flex items-center gap-2.5 px-2.5 pt-2 pb-2.5">
                  <div className="w-7 h-7 rounded-lg bg-emerald-100 dark:bg-emerald-500/10 flex items-center justify-center text-emerald-600 dark:text-emerald-400">
                    <User className="w-4 h-4" />
                  </div>
                  <p className="text-[11px] font-extrabold text-slate-900 dark:text-white leading-none">{authUser?.username || 'User'}</p>
                </div>

                <div className="h-px bg-slate-100 dark:bg-slate-800 my-1.5 mx-1" />

                {/* Preferences group */}
                <div className="flex flex-col gap-2 px-1.5 py-1.5">
                  {/* Theme */}
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2 px-1">
                      <Palette className="w-3 h-3 text-indigo-400 dark:text-indigo-500" />
                      <span className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest">{t('THEME')}</span>
                    </div>
                    <SegmentedControl
                      compact
                      value={isDarkMode ? 'dark' : 'light'}
                      onChange={(val) => setIsDarkMode(val === 'dark')}
                      options={[{ id: 'light', label: t('LIGHT') }, { id: 'dark', label: t('DARK') }]}
                    />
                  </div>
                  {/* Language */}
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2 px-1">
                      <Languages className="w-3 h-3 text-emerald-400 dark:text-emerald-500" />
                      <span className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest">{t('LANGUAGE')}</span>
                    </div>
                    <SegmentedControl
                      compact
                      value={i18n.language}
                      onChange={(val) => i18n.changeLanguage(val)}
                      options={[{ id: 'pt', label: 'PT-BR' }, { id: 'en', label: 'EN' }]}
                    />
                  </div>
                </div>

                <div className="h-px bg-slate-100 dark:bg-slate-800 my-1.5 mx-1" />

                {/* Logout */}
                <DropdownMenu.Item
                  onSelect={handleLogout}
                  className="flex items-center gap-2.5 px-2.5 py-2 text-[10px] font-bold text-rose-500 rounded-lg cursor-pointer outline-none transition-colors hover:bg-rose-50 dark:hover:bg-rose-900/20 uppercase tracking-wide group"
                >
                  <LogOut className="w-3.5 h-3.5 ml-1 text-rose-400 group-hover:text-rose-500 dark:group-hover:text-rose-400 transition-colors" />
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
            min-w-0 ${activeNav === 'alarm-history' ? 'overflow-hidden' : 'overflow-y-auto custom-scrollbar'} ${isResizingPonPanel ? '' : 'transition-[width] duration-150'}
            ${isPonPanelOpen
              ? 'hidden lg:block lg:w-[calc(100%-var(--pon-panel-width))] border-r border-slate-100 dark:border-slate-700/50'
              : 'flex-1'}
          `}
        >
          {activeNav === 'settings' && canManageSettings ? (
            <Suspense fallback={<LazyPanelFallback />}>
              <SettingsPanel
                olts={olts}
                vendorProfiles={vendorProfiles}
                loading={loading}
                vendorLoading={vendorLoading}
                actionError={settingsActionError}
                actionMessage={settingsActionMessage}
                onCreateOlt={createOlt}
                onUpdateOlt={updateOlt}
                onDeleteOlt={deleteOlt}
                onRunDiscovery={runDiscovery}
                onRunPolling={runPolling}
                onRefreshPower={refreshPower}
                actionBusy={settingsActionBusy}
                oltHealthById={oltHealthById}
              />
            </Suspense>
          ) : activeNav === 'power-report' ? (
            <Suspense fallback={<LazyPanelFallback />}>
              <PowerReport />
            </Suspense>
          ) : activeNav === 'alarm-history' ? (
            <Suspense fallback={<LazyPanelFallback />}>
              <AlarmHistory />
            </Suspense>
          ) : (
            <NetworkTopology
              olts={olts}
              loading={loading}
              error={error}
              oltHealthById={oltHealthById}
              selectedOltIds={selectedOltIds}
              onSelectedOltIdsChange={setSelectedOltIds}
              selectedPonId={selectedPonId}
              onAlarmModeChange={handleAlarmModeChange}
              onPonSelect={(id, options = {}) => {
                const nextId = id !== null && id !== undefined ? String(id) : null
                if (options?.highlight) {
                  setPonHighlightTarget(options.highlight)
                } else {
                  setPonHighlightTarget(null)
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
                if (!canOperateTopology) return
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
                } catch {
                  await fetchOlts()
                }
              }
              const handleClosePanel = () => {
                setPonHighlightTarget(null)
                setSelectedPonId(null)
              }
              const renderDescriptionField = () => {
                const description = selectedPonData?.pon?.description || ''
                if (!canOperateTopology) {
                  return (
                    <span
                      className={`text-[11px] font-medium truncate leading-none ${
                        description
                          ? 'text-slate-500 dark:text-slate-400'
                          : 'text-slate-400/60 dark:text-slate-500/70'
                      }`}
                    >
                      {description || t('addDescription')}
                    </span>
                  )
                }
                return (
                  <InlineEditableText
                    value={description}
                    placeholder={t('addDescription')}
                    onSave={handleDescriptionSave}
                  />
                )
              }
              return (
              <div className="h-full min-h-0 flex flex-col">
                {/* Desktop header */}
                <div className="hidden lg:flex pl-8 pr-4 py-3.5 border-b border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 items-center">
                  <div className="w-full flex items-start gap-3">
                    <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                      <div className="flex items-center gap-1.5 text-[12px] font-semibold tracking-[0.03em]">
                        {selectedPonPath.map((part, idx) => (
                          <React.Fragment key={`${part}-${idx}`}>
                            {idx > 0 && <ChevronRight className="w-3 h-3 text-slate-300 dark:text-slate-600" strokeWidth={2.5} />}
                            <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                              {part}
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                      {renderDescriptionField()}
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
                    <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                      <div className="flex items-center gap-1.5 text-[12px] font-semibold tracking-[0.03em]">
                        {selectedPonPath.map((part, idx) => (
                          <React.Fragment key={`m-${part}-${idx}`}>
                            {idx > 0 && <ChevronRight className="w-3 h-3 text-slate-300 dark:text-slate-600 shrink-0" strokeWidth={2.5} />}
                            <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                              {part}
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                      {renderDescriptionField()}
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
                  <div className="flex items-center gap-1.5 mb-3">
                    <div className="inline-flex h-7 items-center gap-0.5 p-0.5 rounded-md border border-slate-200/80 dark:border-slate-700/80 bg-slate-50/90 dark:bg-slate-900/70">
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
                              h-6 min-w-[60px] lg:min-w-[76px] px-2.5 rounded text-[10px] font-black uppercase tracking-[0.06em] transition-all active:scale-[0.97]
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
                    <div className="ml-auto flex items-center gap-1">
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger asChild>
                          <button
                            className="flex items-center gap-0.5 h-7 w-[120px] rounded-md border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-500 hover:text-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-[0.97] pl-1.5 pr-1"
                            aria-label={t('Sort by')}
                            title={t('Sort by')}
                          >
                            <ArrowDownUp className="w-3.5 h-3.5 shrink-0" />
                            <span className="flex-1 min-w-0 text-center text-[10px] font-black uppercase tracking-[0.03em] truncate text-emerald-600 dark:text-emerald-400">
                              {currentSortLabel}
                            </span>
                            <ChevronDown className="w-2.5 h-2.5 shrink-0" />
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

                      {canOperateTopology && (
                        <button
                          onClick={handleRefreshPonPanel}
                          disabled={isSidebarRefreshBusy}
                          className="shrink-0 h-7 w-7 flex items-center justify-center rounded-md border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-400 hover:text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-[0.97] disabled:cursor-not-allowed"
                          aria-label={t('Refresh')}
                          title={refreshCooldownActive ? t('Cooldown') : t('Refresh')}
                        >
                          {refreshCooldownActive ? (
                            <svg className="w-4 h-4" viewBox="0 0 16 16">
                              <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.2" className="text-slate-200 dark:text-slate-700" />
                              <circle
                                cx="8" cy="8" r="6.5"
                                fill="none"
                                strokeWidth="1.8"
                                strokeLinecap="round"
                                strokeDasharray={`${2 * Math.PI * 6.5}`}
                                className="origin-center -rotate-90 text-emerald-500 dark:text-emerald-400"
                                style={{ animation: 'cooldown-ring 5s linear forwards', stroke: 'currentColor' }}
                              />
                            </svg>
                          ) : (
                            <RotateCw className={`w-4 h-4 ${isRefreshingPonPanel ? 'animate-spin' : ''}`} strokeWidth={2.5} />
                          )}
                        </button>
                      )}
                    </div>
                  </div>

                  {ponPanelError && (
                    <div className="mb-2 rounded-lg border border-rose-200 dark:border-rose-500/40 bg-rose-50/80 dark:bg-rose-500/10 px-3 py-2 text-[11px] font-semibold text-rose-600 dark:text-rose-300">
                      {ponPanelError}
                    </div>
                  )}

                  {activeTab === 'status' ? (
                    <div className="relative flex flex-col w-full max-h-full min-h-0">
                    {(isRefreshingPonPanel || isLoadingPonPowerSnapshot) && (
                      <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 dark:bg-slate-900/60 backdrop-blur-[1px] rounded-xl transition-opacity duration-200">
                        <div className="w-5 h-5 border-2 border-slate-300 dark:border-slate-600 border-t-emerald-500 dark:border-t-emerald-400 rounded-full animate-spin" />
                      </div>
                    )}
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
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU')}</th>
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
                                    : t('Unknown')
                              const rawClientName = String(onu.client_name || onu.name || '').trim()
                              const clientLabel = rawClientName || MISSING_VALUE_PLACEHOLDER
                              const { serialValue, hasSerial } = getDisplaySerial(onu)
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? MISSING_VALUE_PLACEHOLDER
                              const disconnectWindow = statusKey === 'online'
                                ? MISSING_VALUE_PLACEHOLDER
                                : formatDisconnectionWindow(
                                    onu.disconnect_window_start,
                                    onu.disconnect_window_end,
                                    i18n.language
                                  )
                              const isHighlightedFromSearch = Boolean(ponHighlightTarget && (
                                (ponHighlightTarget.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(ponHighlightTarget.serial)) ||
                                (ponHighlightTarget.onuId && Number(onuNumber) === Number(ponHighlightTarget.onuId)) ||
                                (ponHighlightTarget.clientName && normalizeMatchValue(rawClientName) === normalizeMatchValue(ponHighlightTarget.clientName))
                              ))
                              return (
                                <tr
                                  key={onu.id}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-11 transition-colors
                                    ${isHighlightedFromSearch ? 'relative z-10' : 'odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/50 hover:bg-slate-100/70 dark:hover:bg-slate-800/60'}
                                  `}
                                  style={isHighlightedFromSearch ? SEARCH_HIGHLIGHT_STYLE : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    {rawClientName ? (
                                      <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                        {clientLabel}
                                      </span>
                                    ) : (
                                      <span className={PLACEHOLDER_CLASS}>{MISSING_VALUE_PLACEHOLDER}</span>
                                    )}
                                  </td>
                                  <td className={`pl-2.5 pr-4 py-0 align-middle whitespace-nowrap ${hasSerial ? 'text-[11px] font-semibold font-mono tracking-[0.01em] text-slate-600 dark:text-slate-300' : 'text-center'}`}>
                                    {hasSerial ? serialValue : (
                                      <span className={PLACEHOLDER_CLASS}>{serialValue}</span>
                                    )}
                                  </td>
                                  <td className="pl-4 pr-6 py-0 align-middle whitespace-nowrap">
                                    <span
                                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}
                                    >
                                      <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                      {statusLabel}
                                    </span>
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${disconnectWindowClass(statusKey, disconnectWindow)}`}>
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
                      {selectedPonStats.total > 0 && (
                        <div className="shrink-0 border-t border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-4 py-1 flex items-center justify-center gap-3">
                          <div className="flex items-center gap-2.5">
                            {selectedPonStats.online > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.online}</span>
                              </div>
                            )}
                            {selectedPonStats.linkLoss > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-rose-500 shadow-sm shadow-rose-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.linkLoss}</span>
                              </div>
                            )}
                            {selectedPonStats.dyingGasp > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.dyingGasp}</span>
                              </div>
                            )}
                            {selectedPonStats.unknown > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-purple-500 shadow-sm shadow-purple-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.unknown}</span>
                              </div>
                            )}
                          </div>
                          <div className="w-px h-3 bg-slate-200 dark:bg-slate-700" />
                          <p className="text-[11px] font-bold tabular-nums leading-none">
                            <span className="text-slate-500 dark:text-slate-300">{selectedPonStats.total}</span>
                            {selectedPonStats.offline > 0 && (
                              <>
                                <span className="text-slate-300 dark:text-slate-400"> / </span>
                                <span className="text-amber-600 dark:text-amber-400">{selectedPonStats.offline}</span>
                              </>
                            )}
                          </p>
                        </div>
                      )}
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
                                    : t('Unknown')
                          const rawClientName = String(onu.client_name || onu.name || '').trim()
                          const clientLabel = rawClientName || MISSING_VALUE_PLACEHOLDER
                          const { serialValue, hasSerial } = getDisplaySerial(onu)
                          const onuNumber = onu.onu_number ?? onu.onu_id ?? MISSING_VALUE_PLACEHOLDER
                          const disconnectWindow = statusKey === 'online'
                            ? MISSING_VALUE_PLACEHOLDER
                            : formatDisconnectionWindow(
                                onu.disconnect_window_start,
                                onu.disconnect_window_end,
                                i18n.language
                              )
                          const isHighlightedFromSearch = Boolean(ponHighlightTarget && (
                            (ponHighlightTarget.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(ponHighlightTarget.serial)) ||
                            (ponHighlightTarget.onuId && Number(onuNumber) === Number(ponHighlightTarget.onuId)) ||
                            (ponHighlightTarget.clientName && normalizeMatchValue(rawClientName) === normalizeMatchValue(ponHighlightTarget.clientName))
                          ))
                          return (
                            <div
                              key={onu.id}
                              data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                              className={`rounded-md border px-3 py-2 flex items-center gap-2 transition-colors ${isHighlightedFromSearch ? 'border-transparent' : 'border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 hover:bg-slate-50 dark:hover:bg-slate-800/60'}`}
                              style={isHighlightedFromSearch ? SEARCH_HIGHLIGHT_STYLE : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col gap-1">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                {rawClientName ? (
                                  <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate">{clientLabel}</span>
                                ) : (
                                  <span className="text-[11px] text-slate-300 dark:text-slate-600">{MISSING_VALUE_PLACEHOLDER}</span>
                                )}
                                {hasSerial ? (
                                  <span className="block text-[11px] font-semibold font-mono tracking-[0.01em] text-slate-500 dark:text-slate-400 truncate">{serialValue}</span>
                                ) : (
                                  <span className={`block text-center ${PLACEHOLDER_CLASS}`}>{serialValue}</span>
                                )}
                              </div>
                              <div className="shrink-0 flex flex-col items-end gap-1">
                                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}>
                                  <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                  {statusLabel}
                                </span>
                                {statusKey !== 'online' && disconnectWindow !== MISSING_VALUE_PLACEHOLDER && <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{disconnectWindow}</span>}
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
                      {selectedPonStats.total > 0 && (
                        <div className="shrink-0 border-t border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-4 py-1 flex items-center justify-center gap-3">
                          {selectedPonStats.online > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.online}</span>
                            </div>
                          )}
                          {selectedPonStats.linkLoss > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-rose-500 shadow-sm shadow-rose-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.linkLoss}</span>
                            </div>
                          )}
                          {selectedPonStats.dyingGasp > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-blue-500 shadow-sm shadow-blue-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.dyingGasp}</span>
                            </div>
                          )}
                          {selectedPonStats.unknown > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-purple-500 shadow-sm shadow-purple-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{selectedPonStats.unknown}</span>
                            </div>
                          )}
                          <div className="w-px h-3 bg-slate-200 dark:bg-slate-700" />
                          <span className="text-[11px] font-bold tabular-nums text-slate-400 dark:text-slate-300">{selectedPonStats.total}</span>
                          {selectedPonStats.offline > 0 && (
                            <>
                              <span className="text-[10px] text-slate-300 dark:text-slate-400">/</span>
                              <span className="text-[11px] font-bold tabular-nums text-amber-600 dark:text-amber-400">{selectedPonStats.offline}</span>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                    </div>

                  ) : (
                    <div className="relative flex flex-col w-full max-h-full min-h-0">
                    {(isRefreshingPonPanel || isLoadingPonPowerSnapshot) && (
                      <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 dark:bg-slate-900/60 backdrop-blur-[1px] rounded-xl transition-opacity duration-200">
                        <div className="w-5 h-5 border-2 border-slate-300 dark:border-slate-600 border-t-emerald-500 dark:border-t-emerald-400 rounded-full animate-spin" />
                      </div>
                    )}
                    {/* Desktop power table */}
                    <div className="hidden lg:flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="shrink-0 overflow-hidden pr-[7px] bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '8%' }} />
                            <col style={{ width: '20%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '14%' }} />
                            <col style={{ width: '14%' }} />
                            <col style={{ width: '26%' }} />
                          </colgroup>
                          <thead>
                            <tr className="bg-slate-50 dark:bg-slate-800/90">
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Name')}</th>
                              <th className="pl-2.5 pr-4 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Serial')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-right">{t('ONU RX')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-right">{t('OLT RX')}</th>
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Leitura')}</th>
                            </tr>
                          </thead>
                        </table>
                      </div>
                      <div className="overflow-x-auto overflow-y-auto min-h-0 custom-scrollbar">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <colgroup>
                            <col style={{ width: '8%' }} />
                            <col style={{ width: '20%' }} />
                            <col style={{ width: '18%' }} />
                            <col style={{ width: '14%' }} />
                            <col style={{ width: '14%' }} />
                            <col style={{ width: '26%' }} />
                          </colgroup>
                          <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                            {powerRows.map(({ onu, statusKey, onuRx, oltRx, readAt }) => {
                              const rawClientName = String(onu.client_name || onu.name || '').trim()
                              const clientLabel = rawClientName || MISSING_VALUE_PLACEHOLDER
                              const { serialValue, hasSerial } = getDisplaySerial(onu)
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? MISSING_VALUE_PLACEHOLDER
                              const hasOnuRx = onuRx !== null
                              const hasOltRx = oltRx !== null
                              const grayPower = 'text-slate-500 dark:text-slate-400'
                              const onuRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(onuRx, 'onu_rx', selectedPonData?.olt?.id))
                              const oltRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(oltRx, 'olt_rx', selectedPonData?.olt?.id))
                              const hasReading = readAt !== null && readAt !== undefined && readAt !== ''
                              const readingAt = formatReadingAt(readAt, i18n.language)
                              const onuRxFormatted = hasOnuRx ? formatPowerValue(onuRx) : null
                              const oltRxFormatted = hasOltRx ? formatPowerValue(oltRx) : null
                              const isHighlightedFromSearch = Boolean(ponHighlightTarget && (
                                (ponHighlightTarget.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(ponHighlightTarget.serial)) ||
                                (ponHighlightTarget.onuId && Number(onuNumber) === Number(ponHighlightTarget.onuId)) ||
                                (ponHighlightTarget.clientName && normalizeMatchValue(rawClientName) === normalizeMatchValue(ponHighlightTarget.clientName))
                              ))
                              return (
                                <tr
                                  key={`power-${onu.id}`}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-11 transition-colors
                                    ${isHighlightedFromSearch ? 'relative z-10' : 'odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/50 hover:bg-slate-100/70 dark:hover:bg-slate-800/60'}
                                  `}
                                  style={isHighlightedFromSearch ? SEARCH_HIGHLIGHT_STYLE : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    {rawClientName ? (
                                      <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                        {clientLabel}
                                      </span>
                                    ) : (
                                      <span className={PLACEHOLDER_CLASS}>{MISSING_VALUE_PLACEHOLDER}</span>
                                    )}
                                  </td>
                                  <td className={`pl-2.5 pr-4 py-0 align-middle whitespace-nowrap ${hasSerial ? 'text-[11px] font-semibold font-mono tracking-[0.01em] text-slate-600 dark:text-slate-300' : 'text-center'}`}>
                                    {hasSerial ? serialValue : (
                                      <span className={PLACEHOLDER_CLASS}>{serialValue}</span>
                                    )}
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-bold tabular-nums text-right ${onuRxFormatted ? onuRxColor : 'text-slate-300 dark:text-slate-600'}`}>
                                    {onuRxFormatted || MISSING_VALUE_PLACEHOLDER}
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-bold tabular-nums text-right ${oltRxFormatted ? oltRxColor : 'text-slate-300 dark:text-slate-600'}`}>
                                    {oltRxFormatted || MISSING_VALUE_PLACEHOLDER}
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${hasReading ? 'text-slate-500 dark:text-slate-400' : 'text-slate-300 dark:text-slate-600'}`}>
                                    {readingAt}
                                  </td>
                                </tr>
                              )
                            })}
                            {powerRows.length === 0 && (
                              <tr>
                                <td colSpan={6} className="p-8 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                                  {t('No ONU data available')}
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                      {powerRows.length > 0 && (
                        <div className="shrink-0 border-t border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-4 py-1 flex items-center justify-center gap-2.5">
                            {powerSignalCounts.good > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.good}</span>
                              </div>
                            )}
                            {powerSignalCounts.warning > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-amber-500 shadow-sm shadow-amber-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.warning}</span>
                              </div>
                            )}
                            {powerSignalCounts.critical > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-rose-500 shadow-sm shadow-rose-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.critical}</span>
                              </div>
                            )}
                            {powerSignalCounts.noReading > 0 && (
                              <div className="flex items-center gap-1">
                                <div className="w-2 h-2 rounded-full bg-violet-500 shadow-sm shadow-violet-500/20" />
                                <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.noReading}</span>
                              </div>
                            )}
                          <div className="w-px h-3 bg-slate-200 dark:bg-slate-700" />
                          <span className="text-[11px] font-bold tabular-nums text-slate-400 dark:text-slate-300">{powerRows.length}</span>
                        </div>
                      )}
                    </div>
                    {/* Mobile power cards */}
                    <div className="flex lg:hidden flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-1.5">
                        {powerRows.map(({ onu, statusKey, onuRx, oltRx, readAt }) => {
                          const rawClientName = String(onu.client_name || onu.name || '').trim()
                          const clientLabel = rawClientName || MISSING_VALUE_PLACEHOLDER
                          const { serialValue, hasSerial } = getDisplaySerial(onu)
                          const onuNumber = onu.onu_number ?? onu.onu_id ?? MISSING_VALUE_PLACEHOLDER
                          const hasOnuRx = onuRx !== null
                          const hasOltRx = oltRx !== null
                          const hasAnyPower = hasOnuRx || hasOltRx
                          const grayPower = 'text-slate-500 dark:text-slate-400'
                          const onuRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(onuRx, 'onu_rx', selectedPonData?.olt?.id))
                          const oltRxColor = isSelectedOltGray ? grayPower : powerColorClass(getPowerColor(oltRx, 'olt_rx', selectedPonData?.olt?.id))
                          const hasReading = readAt !== null && readAt !== undefined && readAt !== ''
                          const readingAt = formatReadingAt(readAt, i18n.language)
                          const isHighlightedFromSearch = Boolean(ponHighlightTarget && (
                            (ponHighlightTarget.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(ponHighlightTarget.serial)) ||
                            (ponHighlightTarget.onuId && Number(onuNumber) === Number(ponHighlightTarget.onuId)) ||
                            (ponHighlightTarget.clientName && normalizeMatchValue(rawClientName) === normalizeMatchValue(ponHighlightTarget.clientName))
                          ))
                          return (
                            <div
                              key={`power-${onu.id}`}
                              data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                              className={`rounded-md border px-3 py-2 flex items-center gap-2 transition-colors ${isHighlightedFromSearch ? 'border-transparent' : 'border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 hover:bg-slate-50 dark:hover:bg-slate-800/60'}`}
                              style={isHighlightedFromSearch ? SEARCH_HIGHLIGHT_STYLE : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col gap-1">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                {rawClientName ? (
                                  <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate">{clientLabel}</span>
                                ) : (
                                  <span className="text-[11px] text-slate-300 dark:text-slate-600">{MISSING_VALUE_PLACEHOLDER}</span>
                                )}
                                {hasSerial ? (
                                  <span className="block text-[11px] font-semibold font-mono tracking-[0.01em] text-slate-500 dark:text-slate-400 truncate">{serialValue}</span>
                                ) : (
                                  <span className="block text-center text-[11px] font-semibold tabular-nums text-slate-300 dark:text-slate-600">{serialValue}</span>
                                )}
                              </div>
                              <div className="shrink-0 flex flex-col gap-1">
                                {hasAnyPower ? (
                                  <>
                                    <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                                      <span className="font-mono text-slate-400 dark:text-slate-500">{t('ONU')}</span>
                                      <span className={`w-[76px] text-right font-semibold ${hasOnuRx ? onuRxColor : 'text-slate-300 dark:text-slate-600'}`}>{hasOnuRx ? formatPowerValue(onuRx) : MISSING_VALUE_PLACEHOLDER}</span>
                                    </span>
                                    <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                                      <span className="font-mono text-slate-400 dark:text-slate-500">{t('OLT')}</span>
                                      <span className={`w-[76px] text-right font-semibold ${hasOltRx ? oltRxColor : 'text-slate-300 dark:text-slate-600'}`}>{hasOltRx ? formatPowerValue(oltRx) : MISSING_VALUE_PLACEHOLDER}</span>
                                    </span>
                                    <span className={`self-stretch text-left text-[10px] font-semibold tabular-nums ${hasReading ? 'text-slate-400 dark:text-slate-500' : 'text-slate-300 dark:text-slate-600'}`}>{readingAt}</span>
                                  </>
                                ) : (
                                  <span className="text-[11px] font-semibold tabular-nums text-slate-300 dark:text-slate-600">{MISSING_VALUE_PLACEHOLDER}</span>
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
                      {powerRows.length > 0 && (
                        <div className="shrink-0 border-t border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-4 py-1 flex items-center justify-center gap-2.5">
                          {powerSignalCounts.good > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.good}</span>
                            </div>
                          )}
                          {powerSignalCounts.warning > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-amber-500 shadow-sm shadow-amber-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.warning}</span>
                            </div>
                          )}
                          {powerSignalCounts.critical > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-rose-500 shadow-sm shadow-rose-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.critical}</span>
                            </div>
                          )}
                          {powerSignalCounts.noReading > 0 && (
                            <div className="flex items-center gap-1">
                              <div className="w-2 h-2 rounded-full bg-violet-500 shadow-sm shadow-violet-500/20" />
                              <span className="text-[11px] font-bold tabular-nums text-slate-700 dark:text-slate-200">{powerSignalCounts.noReading}</span>
                            </div>
                          )}
                          <div className="w-px h-3 bg-slate-200 dark:bg-slate-700" />
                          <span className="text-[11px] font-bold tabular-nums text-slate-400 dark:text-slate-300">{powerRows.length}</span>
                        </div>
                      )}
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
      <footer className="shrink-0 flex items-center justify-between px-4 pt-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))] text-[11px] font-medium text-slate-400 dark:text-slate-400 border-t border-slate-100 dark:border-slate-700/50 bg-white dark:bg-slate-900 transition-colors">
        <span>{t('Version')} {__APP_VERSION__}</span>
        <span>{lastCollectionAt ? `${t('Last update')}: ${formatReadingAt(lastCollectionAt, i18n.language)}` : ''}</span>
      </footer>

    </div>
  )
}

export default App
