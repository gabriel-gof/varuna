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
  ArrowDownUp,
  ArrowLeft
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { NetworkTopology } from './components/NetworkTopology'
import { SettingsPanel } from './components/SettingsPanel'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import api, { updatePonDescription } from './services/api'
import { InlineEditableText } from './components/InlineEditableText'
import { classifyOnu } from './utils/stats'
import { deriveOltHealthState, getPowerIntervalSeconds } from './utils/oltHealth'
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
const ALARM_REASON_TO_STATUS_SORT = {
  linkLoss: 'link_loss',
  dyingGasp: 'dying_gasp',
  unknown: 'unknown'
}

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

const formatOfflineSince = (value, language) => {
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
  const [selectedPonId, setSelectedPonId] = useState(null)
  const [selectedSearchMatch, setSelectedSearchMatch] = useState(null)
  const [isDarkMode, setIsDarkMode] = useState(false)
  const [activeTab, setActiveTab] = useState('status')
  const [statusSortMode, setStatusSortMode] = useState('onu_id')
  const [powerSortMode, setPowerSortMode] = useState('default')
  const [alarmSortConfig, setAlarmSortConfig] = useState({
    enabled: false,
    reasons: ALARM_REASON_PRIORITY
  })
  const [activeNav, setActiveNav] = useState(() => {
    const saved = localStorage.getItem('varuna_active_tab')
    return ['topology', 'settings'].includes(saved) ? saved : 'topology'
  })

  useEffect(() => {
    localStorage.setItem('varuna_active_tab', activeNav)
  }, [activeNav])
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
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isRefreshingPonPower, setIsRefreshingPonPower] = useState(false)
  const [healthTick, setHealthTick] = useState(() => Date.now())
  const [snmpStatus, setSnmpStatus] = useState({}) // { [oltId]: { status: 'pending'|'reachable'|'unreachable', sysDescr } }
  const snmpCheckRef = useRef(null)
  const maintenanceLocksRef = useRef({
    polling: new Set(),
    discovery: new Set()
  })
  const selectedPonDataRef = useRef(null)
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

  const fetchOlts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get('/olts/', { params: { include_topology: 'true' } })
      setOlts(normalizeList(res.data))
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
        setOlts(enriched)
      } catch (fallbackErr) {
        setError(getApiErrorMessage(err, getApiErrorMessage(fallbackErr, 'Failed to load OLT data')))
      }
    } finally {
      setLoading(false)
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

  const handleRefresh = async () => {
    setIsRefreshing(true)
    await Promise.all([fetchOlts(), fetchVendorProfiles()])
    setTimeout(() => setIsRefreshing(false), 400)
  }

  // ─── SNMP connectivity checks (shared across Settings + Topology) ───
  const runSnmpChecks = useCallback(async (oltList) => {
    if (!oltList?.length) return
    setSnmpStatus((prev) => {
      const next = { ...prev }
      oltList.forEach((olt) => { next[olt.id] = { status: 'pending' } })
      return next
    })
    const promises = oltList.map(async (olt) => {
      try {
        const res = await api.post(`/olts/${olt.id}/snmp_check/`)
        return { id: olt.id, status: res.data?.reachable ? 'reachable' : 'unreachable', sysDescr: res.data?.sys_descr || '' }
      } catch {
        return { id: olt.id, status: 'unreachable', sysDescr: '' }
      }
    })
    const results = await Promise.allSettled(promises)
    setSnmpStatus((prev) => {
      const next = { ...prev }
      results.forEach((r) => {
        if (r.status === 'fulfilled') next[r.value.id] = r.value
      })
      return next
    })
  }, [])

  useEffect(() => {
    fetchOlts()
    fetchVendorProfiles()
    const interval = setInterval(fetchOlts, 30000)
    return () => clearInterval(interval)
  }, [fetchOlts, fetchVendorProfiles])

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

    oltList.forEach((olt) => {
      const oltId = olt?.id
      if (!oltId) return

      if (olt.polling_enabled !== false) {
        const pollIntervalMs = toPositiveInt(olt.polling_interval_seconds, 300) * 1000
        const lastPollMs = parseTimestampMs(olt.last_poll_at)
        const pollDue = !lastPollMs || nowMs - lastPollMs >= pollIntervalMs
        if (pollDue && !pollLocks.has(oltId)) {
          pollLocks.add(oltId)
          requests.push(
            api.post(`/olts/${oltId}/run_polling/`)
              .catch(() => null)
              .finally(() => pollLocks.delete(oltId))
          )
        }
      }

      if (olt.discovery_enabled !== false) {
        const discoveryIntervalMs = toPositiveInt(olt.discovery_interval_minutes, 240) * 60 * 1000
        const lastDiscoveryMs = parseTimestampMs(olt.last_discovery_at)
        const discoveryDue = !lastDiscoveryMs || nowMs - lastDiscoveryMs >= discoveryIntervalMs
        if (discoveryDue && !discoveryLocks.has(oltId)) {
          discoveryLocks.add(oltId)
          requests.push(
            api.post(`/olts/${oltId}/run_discovery/`)
              .catch(() => null)
              .finally(() => discoveryLocks.delete(oltId))
          )
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

  // Run SNMP checks when OLTs change, repeat every 60s
  useEffect(() => {
    if (!olts?.length) return
    runSnmpChecks(olts)
    snmpCheckRef.current = setInterval(() => runSnmpChecks(olts), 60_000)
    return () => clearInterval(snmpCheckRef.current)
  }, [olts, runSnmpChecks])

  const runSettingsAction = async (key, request, successMessage = '') => {
    setSettingsActionError(null)
    setSettingsActionMessage('')
    setSettingsActionBusy((prev) => ({ ...prev, [key]: true }))

    try {
      const result = await request()
      if (successMessage) {
        setSettingsActionMessage(successMessage)
      }
      return result
    } catch (err) {
      setSettingsActionError(getApiErrorMessage(err, 'Failed to execute settings action'))
      return null
    } finally {
      setSettingsActionBusy((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
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
    await runSettingsAction(
      `discovery:${oltId}`,
      async () => {
        await api.post(`/olts/${oltId}/run_discovery/`)
        await fetchOlts()
        return true
      },
      t('Discovery executed successfully')
    )
  }

  const runPolling = async (oltId) => {
    await runSettingsAction(
      `polling:${oltId}`,
      async () => {
        await api.post(`/olts/${oltId}/run_polling/`)
        await fetchOlts()
        return true
      },
      t('Polling executed successfully')
    )
  }

  const refreshPower = async (oltId) => {
    await runSettingsAction(
      `power:${oltId}`,
      async () => {
        await api.post(`/olts/${oltId}/refresh_power/`)
        await fetchOlts()
        return true
      },
      t('Power refresh executed successfully')
    )
  }

  const selectedPonData = useMemo(() => {
    if (!selectedPonId) return null
    return findPonById(olts, selectedPonId)
  }, [olts, selectedPonId])

  useEffect(() => {
    selectedPonDataRef.current = selectedPonData
  }, [selectedPonData])

  const mergePowerResultsIntoOlts = (currentOlts, rows) => {
    if (!Array.isArray(rows) || rows.length === 0) return currentOlts

    const powerByOnuId = new Map(
      rows
        .filter((row) => row && row.onu_id !== undefined && row.onu_id !== null)
        .map((row) => [String(row.onu_id), row])
    )

    if (!powerByOnuId.size) return currentOlts

    return currentOlts.map((olt) => ({
      ...olt,
      slots: asList(olt?.slots).map((slot) => ({
        ...slot,
        pons: asList(slot?.pons).map((pon) => ({
          ...pon,
          onus: asList(pon?.onus).map((onu) => {
            const patch = powerByOnuId.get(String(onu?.id))
            if (!patch) return onu
            return {
              ...onu,
              onu_rx_power: patch.onu_rx_power ?? null,
              olt_rx_power: patch.olt_rx_power ?? null,
              power_read_at: patch.power_read_at ?? null,
            }
          }),
        })),
      })),
    }))
  }

  const handleRefreshPonPower = useCallback(async () => {
    const currentPonData = selectedPonDataRef.current
    if (!currentPonData?.olt || !currentPonData?.slot || !currentPonData?.pon) return

    const slotId = currentPonData.slot?.slot_number ?? currentPonData.slot?.slot_id
    const ponId = currentPonData.pon?.pon_number ?? currentPonData.pon?.pon_id
    if (slotId === undefined || ponId === undefined) return

    setIsRefreshingPonPower(true)
    try {
      const response = await api.post('/onu/batch-power/', {
        olt_id: currentPonData.olt.id,
        slot_id: slotId,
        pon_id: ponId,
        refresh: true,
      })
      const rows = Array.isArray(response?.data?.results)
        ? response.data.results
        : normalizeList(response?.data)
      setOlts((prev) => mergePowerResultsIntoOlts(prev, rows))
      setError(null)
    } catch (err) {
      setError(getApiErrorMessage(err, 'Failed to refresh power data'))
    } finally {
      setIsRefreshingPonPower(false)
    }
  }, [])

  useEffect(() => {
    if (activeNav !== 'topology' || activeTab !== 'power') return
    if (!selectedPonData?.olt || !selectedPonId) return

    const intervalSeconds = getPowerIntervalSeconds(selectedPonData.olt)
    const timer = setInterval(() => {
      handleRefreshPonPower()
    }, intervalSeconds * 1000)

    return () => clearInterval(timer)
  }, [
    activeNav,
    activeTab,
    selectedPonId,
    selectedPonData?.olt?.id,
    selectedPonData?.olt?.power_interval_seconds,
    handleRefreshPonPower
  ])

  useEffect(() => {
    if (selectedPonId && !selectedPonData) {
      setSelectedPonId(null)
    }
  }, [selectedPonId, selectedPonData])

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
      if (hasAny(requestedMode)) return requestedMode
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

  const powerSortOptions = [
    { id: 'default', label: t('Default order') },
    { id: 'worst_onu_rx', label: t('Worst ONU RX') },
    { id: 'worst_olt_rx', label: t('Worst OLT RX') },
    { id: 'best_onu_rx', label: t('Best ONU RX') },
    { id: 'best_olt_rx', label: t('Best OLT RX') }
  ]

  const activeSortOptions = activeTab === 'power' ? powerSortOptions : statusSortOptions
  const currentSortMode = activeTab === 'power' ? powerSortMode : statusSortMode
  const currentSortLabel = activeSortOptions.find((option) => option.id === currentSortMode)?.label || activeSortOptions[0]?.label || t('ONU ID')
  const isSidebarRefreshBusy = activeTab === 'power' ? isRefreshingPonPower : isRefreshing
  const setCurrentSortMode = (mode) => {
    if (activeTab === 'power') {
      setPowerSortMode(mode)
      return
    }
    setStatusSortMode(resolveStatusSortMode(mode))
  }

  useEffect(() => {
    if (!alarmSortConfig.enabled) return

    const activeReasons = ALARM_REASON_PRIORITY.filter((reason) => alarmSortConfig.reasons.includes(reason))
    let desiredStatusMode = 'offline'

    if (activeReasons.length === 1) {
      desiredStatusMode = ALARM_REASON_TO_STATUS_SORT[activeReasons[0]]
    } else if (activeReasons.length === 0) {
      desiredStatusMode = 'onu_id'
    }

    const nextMode = resolveStatusSortMode(desiredStatusMode)
    setStatusSortMode((prev) => (prev === nextMode ? prev : nextMode))
  }, [alarmSortConfig.enabled, alarmSortConfig.reasons, availableStatusCounts])

  const statusRows = useMemo(() => {
    const baseRows = selectedOnus.map((onu) => {
      const classification = classifyOnu(onu)
      const statusKey = classification.status
      const parsedOffline = Date.parse(onu?.offline_since || '')
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

    const baseOrder = ['online', 'link_loss', 'dying_gasp', 'unknown', 'offline']
    let orderedStatuses = baseOrder
    if (statusSortMode === 'offline') {
      orderedStatuses = ['link_loss', 'dying_gasp', 'unknown', 'offline', 'online']
    } else if (statusSortMode !== 'online') {
      orderedStatuses = [statusSortMode, ...baseOrder.filter((status) => status !== statusSortMode)]
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
  }, [selectedOnus, statusSortMode])

  const powerRows = useMemo(() => {
    const baseRows = selectedOnus.map((onu) => {
      const classification = classifyOnu(onu)
      const { onuRx, oltRx } = getOnuPowerSnapshot(onu)
      const parsedOnuRx = asNumericPower(onuRx)
      const parsedOltRx = asNumericPower(oltRx)
      return {
        onu,
        statusKey: classification.status,
        onuNumber: Number(onu?.onu_number ?? onu?.onu_id ?? 0),
        onuRx: parsedOnuRx,
        oltRx: parsedOltRx,
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

  useEffect(() => {
    if (!selectedSearchMatch) return
    if (!selectedPonId || String(selectedSearchMatch.ponId) !== String(selectedPonId)) return

    const row = document.querySelector('[data-onu-highlight="true"]')
    if (row) {
      row.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [selectedSearchMatch, selectedPonId, selectedOnus, activeTab])

  const selectedPonStats = useMemo(() => {
    return selectedOnus.reduce((acc, onu) => {
      const { status } = classifyOnu(onu)
      if (status === 'online') acc.online++
      else acc.offline++
      return acc
    }, { online: 0, offline: 0 })
  }, [selectedOnus])

  const statusStyle = (statusKey) => {
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
    if (statusKey === 'online') return 'bg-emerald-500'
    if (statusKey === 'dying_gasp') return 'bg-blue-500'
    if (statusKey === 'link_loss') return 'bg-rose-500'
    if (statusKey === 'unknown') return 'bg-purple-500'
    if (statusKey === 'offline') return 'bg-rose-500'
    return 'bg-slate-400'
  }

  return (
    <div className="h-screen bg-white dark:bg-slate-950 flex flex-col font-sans transition-colors duration-300">
      <nav className="h-16 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800 flex items-center px-6 sticky top-0 z-[100] transition-colors shadow-sm">
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
        </div>

        <div className="flex items-center gap-1 h-full ml-auto mr-6">
          <button
            onClick={() => setActiveNav('settings')}
            className={`flex items-center justify-center gap-2.5 px-4 h-full sm:w-[156px] transition-all relative group ${activeNav === 'settings' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <SettingsIcon className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider hidden sm:block">{t('Settings')}</span>
            {activeNav === 'settings' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
        </div>

        <div className="flex items-center gap-3">
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
                className="w-[252px] bg-white dark:bg-slate-900 rounded-2xl p-2.5 shadow-2xl border border-slate-100 dark:border-slate-800 z-[200] animate-in fade-in zoom-in-95 duration-200"
                sideOffset={8}
                align="end"
              >
                <div className="px-2 py-2 mb-1.5 border-b border-slate-100 dark:border-slate-800">
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
              ? 'hidden lg:block lg:w-[calc(100%-var(--pon-panel-width))] border-r border-slate-100 dark:border-slate-800'
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
              onAlarmModeChange={(config) => {
                const reasons = Array.isArray(config?.reasons)
                  ? config.reasons.filter((reason) => ALARM_REASON_PRIORITY.includes(reason))
                  : ALARM_REASON_PRIORITY
                setAlarmSortConfig({
                  enabled: Boolean(config?.enabled),
                  reasons: reasons.length ? reasons : ALARM_REASON_PRIORITY
                })
              }}
              onSearchMatchSelect={setSelectedSearchMatch}
              onPonSelect={(id, options = {}) => {
                if (options?.force) {
                  setSelectedPonId(id)
                  return
                }
                setSelectedSearchMatch(null)
                setSelectedPonId((prev) => (prev === id ? null : id))
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
              h-full min-h-0 flex flex-col flex-shrink-0 bg-slate-100 dark:bg-slate-950 overflow-hidden ${isResizingPonPanel ? '' : 'transition-[width] duration-150'}
              ${isPonPanelOpen
                ? 'w-full lg:w-[var(--pon-panel-width)] opacity-100 border-l border-slate-100 dark:border-slate-800'
                : 'w-0 opacity-0 pointer-events-none border-l-0'}
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
                <div className="hidden lg:flex pl-8 pr-4 h-20 border-b border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 items-center">
                  <div className="w-full flex items-center justify-between gap-3">
                    <div className="min-w-0 flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-wide">
                      {selectedPonPath.map((part, idx) => (
                        <React.Fragment key={`${part}-${idx}`}>
                          {idx > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600" strokeWidth={2.5} />}
                          <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                            {part}
                          </span>
                        </React.Fragment>
                      ))}
                      <span className="text-slate-300/60 dark:text-slate-700/60 font-normal">–</span>
                      <InlineEditableText
                        value={selectedPonData?.pon?.description || ''}
                        placeholder={t('addDescription')}
                        onSave={handleDescriptionSave}
                      />
                    </div>
                    <button
                      onClick={handleClosePanel}
                      className="h-9 w-9 self-center flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors shrink-0"
                      aria-label={t('Close')}
                    >
                      <X className="w-[18px] h-[18px]" />
                    </button>
                  </div>
                </div>
                {/* Mobile header */}
                <div className="flex lg:hidden flex-col px-4 py-3 border-b border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 gap-1">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleClosePanel}
                      className="h-8 w-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors shrink-0 -ml-1"
                      aria-label={t('Close')}
                    >
                      <ArrowLeft className="w-[18px] h-[18px]" />
                    </button>
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
                    </div>
                  </div>
                  <div className="pl-9">
                    <InlineEditableText
                      value={selectedPonData?.pon?.description || ''}
                      placeholder={t('addDescription')}
                      onSave={handleDescriptionSave}
                    />
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
                              h-7 min-w-[88px] px-3 rounded-md text-[10px] font-black uppercase tracking-[0.06em] transition-all
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
                            className="relative h-9 w-[130px] lg:w-[156px] rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-500 hover:text-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all"
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
                            className="w-[156px] bg-white dark:bg-slate-900 rounded-xl p-1 shadow-xl border border-slate-200 dark:border-slate-800 z-[220] animate-in fade-in zoom-in-95 duration-150"
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
                        onClick={() => {
                          if (activeTab === 'power') {
                            handleRefreshPonPower()
                            return
                          }
                          handleRefresh()
                        }}
                        disabled={isSidebarRefreshBusy}
                        className="shrink-0 p-2.5 rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-400 hover:text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-95 disabled:opacity-55 disabled:cursor-not-allowed"
                        aria-label={t('Refresh')}
                        title={t('Refresh')}
                      >
                        <RotateCw className={`w-4 h-4 ${isSidebarRefreshBusy ? 'animate-spin' : ''}`} strokeWidth={2.5} />
                      </button>
                    </div>
                  </div>

                  {activeTab === 'status' ? (
                    <>
                    {/* Desktop status table */}
                    <div className="hidden lg:flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
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
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Client')}</th>
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
                              const offlineSince = statusKey === 'online' ? '—' : formatOfflineSince(onu.offline_since, i18n.language)
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
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/35 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? { boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
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
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums text-center">
                                    {offlineSince}
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
                    <div className="flex lg:hidden flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-2">
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
                          const offlineSince = statusKey === 'online' ? '—' : formatOfflineSince(onu.offline_since, i18n.language)
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
                              className={`rounded-lg border px-3 py-2 flex items-center gap-2 ${isHighlightedFromSearch ? 'border-emerald-400 dark:border-emerald-600 bg-emerald-50/90 dark:bg-emerald-900/25' : 'border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900'}`}
                              style={isHighlightedFromSearch ? { boxShadow: '0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate leading-tight">{clientLabel}</span>
                                <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 font-mono tracking-[0.01em] truncate">{serialValue}</span>
                              </div>
                              <div className="shrink-0 flex flex-col items-end gap-0.5">
                                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}>
                                  <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                  {statusLabel}
                                </span>
                                <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{offlineSince}</span>
                              </div>
                            </div>
                          )
                        })}
                        {statusRows.length === 0 && (
                          <div className="p-8 text-center text-[11px] font-bold text-slate-400 uppercase tracking-widest">
                            {t('No ONU data available')}
                          </div>
                        )}
                      </div>
                    </div>
                    </>

                  ) : (
                    <>
                    {/* Desktop power table */}
                    <div className="hidden lg:flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
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
                              <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Client')}</th>
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
                            {powerRows.map(({ onu, statusKey }) => {
                              const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                              const serialValue = onu.serial_number || onu.serial || '—'
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                              const { onuRx, oltRx, readAt } = getOnuPowerSnapshot(onu)
                              const parsedOnuRx = asNumericPower(onuRx)
                              const parsedOltRx = asNumericPower(oltRx)
                              const hasOnuRx = parsedOnuRx !== null
                              const hasOltRx = parsedOltRx !== null
                              const onuRxColor = powerColorClass(getPowerColor(parsedOnuRx, 'onu_rx', selectedPonData?.olt?.id))
                              const oltRxColor = powerColorClass(getPowerColor(parsedOltRx, 'olt_rx', selectedPonData?.olt?.id))
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
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/35 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? { boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
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
                                    {!hasOnuRx && !hasOltRx ? (
                                      <span className={`inline-block text-[11px] font-semibold tabular-nums ${isOfflineStatus ? 'text-rose-600 dark:text-rose-300' : 'text-slate-500 dark:text-slate-400'}`}>—</span>
                                    ) : (
                                      <div className="inline-flex flex-col items-center gap-1 leading-snug tabular-nums">
                                        <span className="inline-flex items-center text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                          <span className="inline-block w-8 text-left">{t('ONU')}</span>
                                          <span className={`font-semibold ${onuRxColor}`}>{hasOnuRx ? formatPowerValue(parsedOnuRx) : '—'}</span>
                                        </span>
                                        <span className="inline-flex items-center text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                          <span className="inline-block w-8 text-left">{t('OLT')}</span>
                                          <span className={`font-semibold ${oltRxColor}`}>{hasOltRx ? formatPowerValue(parsedOltRx) : '—'}</span>
                                        </span>
                                      </div>
                                    )}
                                  </td>
                                  <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${!hasReading && isOfflineStatus ? 'text-rose-600 dark:text-rose-300' : 'text-slate-500 dark:text-slate-400'}`}>
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
                    <div className="flex lg:hidden flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-2">
                        {powerRows.map(({ onu, statusKey }) => {
                          const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                          const serialValue = onu.serial_number || onu.serial || '—'
                          const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                          const { onuRx, oltRx, readAt } = getOnuPowerSnapshot(onu)
                          const parsedOnuRx = asNumericPower(onuRx)
                          const parsedOltRx = asNumericPower(oltRx)
                          const hasOnuRx = parsedOnuRx !== null
                          const hasOltRx = parsedOltRx !== null
                          const onuRxColor = powerColorClass(getPowerColor(parsedOnuRx, 'onu_rx', selectedPonData?.olt?.id))
                          const oltRxColor = powerColorClass(getPowerColor(parsedOltRx, 'olt_rx', selectedPonData?.olt?.id))
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
                              className={`rounded-lg border px-3 py-2 flex items-center gap-2 ${isHighlightedFromSearch ? 'border-emerald-400 dark:border-emerald-600 bg-emerald-50/90 dark:bg-emerald-900/25' : 'border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900'}`}
                              style={isHighlightedFromSearch ? { boxShadow: '0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                            >
                              <div className="min-w-0 flex-1 flex flex-col">
                                <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{onuNumber}</span>
                                <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate leading-tight">{clientLabel}</span>
                                <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 font-mono tracking-[0.01em] truncate">{serialValue}</span>
                              </div>
                              <div className="shrink-0 flex flex-col items-end gap-0.5">
                                {(hasOnuRx || hasOltRx) ? (
                                  <>
                                    <span className="inline-flex items-center text-[11px] font-bold tabular-nums whitespace-nowrap">
                                      <span className="w-8 text-right font-mono text-slate-400 dark:text-slate-500">{t('ONU')}</span>
                                      <span className={`ml-1 font-semibold ${onuRxColor}`}>{hasOnuRx ? formatPowerValue(parsedOnuRx) : '—'}</span>
                                    </span>
                                    <span className="inline-flex items-center text-[11px] font-bold tabular-nums whitespace-nowrap">
                                      <span className="w-8 text-right font-mono text-slate-400 dark:text-slate-500">{t('OLT')}</span>
                                      <span className={`ml-1 font-semibold ${oltRxColor}`}>{hasOltRx ? formatPowerValue(parsedOltRx) : '—'}</span>
                                    </span>
                                  </>
                                ) : (
                                  <span className={`text-[11px] font-semibold tabular-nums ${isOfflineStatus ? 'text-rose-600 dark:text-rose-300' : 'text-slate-500 dark:text-slate-400'}`}>—</span>
                                )}
                                <span className={`text-[10px] font-semibold tabular-nums ${!hasReading && isOfflineStatus ? 'text-rose-600 dark:text-rose-300' : 'text-slate-400 dark:text-slate-500'}`}>{readingAt}</span>
                              </div>
                            </div>
                          )
                        })}
                        {powerRows.length === 0 && (
                          <div className="p-8 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                            {t('No ONU data available')}
                          </div>
                        )}
                      </div>
                    </div>
                    </>
                  )}
                </div>
              </div>
              )
            })()}
          </aside>
        )}
      </main>
    </div>
  )
}

export default App
