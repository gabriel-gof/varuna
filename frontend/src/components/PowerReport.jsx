import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Check, ArrowDownUp, Server, CircuitBoard, Cable } from 'lucide-react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'

import api from '../services/api'
import { getPowerColor, powerColorClass } from '../utils/powerThresholds'

const INITIAL_VISIBLE_ROWS = 300
const LOAD_MORE_ROWS = 300
const DEFAULT_SIGNAL_FILTER = ['good', 'critical', 'warning']
const DEFAULT_SORT_MODE = 'worst_onu_rx'

const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))
const parseTimestampMs = (value) => {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}
const formatPowerValue = (value) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return null
  if (Object.is(numeric, -0) || (numeric > -0.005 && numeric < 0.005)) return null
  if (numeric <= -39.995) return null
  return `${numeric.toFixed(2)} dBm`
}

const classifySignal = (onuRx, oltRx, oltId) => {
  const onuColor = getPowerColor(onuRx, 'onu_rx', oltId)
  const oltColor = getPowerColor(oltRx, 'olt_rx', oltId)
  const colors = [onuColor, oltColor].filter(Boolean)
  if (!colors.length) return 'unknown'
  if (colors.includes('red')) return 'critical'
  if (colors.includes('yellow')) return 'warning'
  return 'good'
}

const formatReadingAt = (value, language) => {
  if (!value) return '—'
  try {
    const date = new Date(value)
    if (isNaN(date.getTime())) return '—'
    return new Intl.DateTimeFormat(language === 'pt' ? 'pt-BR' : 'en-US', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    }).format(date)
  } catch {
    return '—'
  }
}

const normalizeRow = (row) => {
  const onuRx = row?.onu_rx_power ?? row?.onu_rx ?? row?.rx_power ?? null
  const oltRx = row?.olt_rx_power ?? row?.olt_rx ?? null
  return {
    id: row?.id ?? null,
    oltName: row?.olt_name || row?.olt || 'OLT',
    oltId: row?.olt_id ?? row?.oltId,
    powerIntervalSeconds: row?.power_interval_seconds ?? row?.powerIntervalSeconds ?? null,
    slotNumber: row?.slot_id ?? row?.slot_number ?? '',
    slotRefId: row?.slot_ref_id ?? row?.slotRefId ?? null,
    ponNumber: row?.pon_id ?? row?.pon_number ?? '',
    ponRefId: row?.pon_ref_id ?? row?.ponRefId ?? null,
    onuId: row?.onu_number ?? row?.onu_id ?? '',
    clientName: row?.client_name || row?.name || '',
    serial: row?.serial || row?.serial_number || '',
    status: String(row?.status || '').toLowerCase(),
    onuRx,
    oltRx,
    readingAt: row?.power_read_at || row?.reading_at || null,
    readingAtMs: parseTimestampMs(row?.power_read_at || row?.reading_at || null),
    signal: classifySignal(onuRx, oltRx, row?.olt_id ?? row?.oltId),
  }
}

export const PowerReport = () => {
  const { t, i18n } = useTranslation()
  const [signalFilter, setSignalFilter] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('varuna.powerReport.signalFilter'))
      return Array.isArray(saved) ? saved : DEFAULT_SIGNAL_FILTER
    } catch { return DEFAULT_SIGNAL_FILTER }
  })
  const [sortMode, setSortMode] = useState(() => {
    try {
      return localStorage.getItem('varuna.powerReport.sortMode') || DEFAULT_SORT_MODE
    } catch { return DEFAULT_SORT_MODE }
  })
  useEffect(() => {
    try { localStorage.setItem('varuna.powerReport.signalFilter', JSON.stringify(signalFilter)) } catch {}
  }, [signalFilter])

  useEffect(() => {
    try { localStorage.setItem('varuna.powerReport.sortMode', sortMode) } catch {}
  }, [sortMode])

  const [rows, setRows] = useState([])
  const [visibleRowsLimit, setVisibleRowsLimit] = useState(INITIAL_VISIBLE_ROWS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Cascading OLT → Slot → PON filter state (restored from localStorage)
  const [selectedOltId, setSelectedOltId] = useState(() => {
    try { return localStorage.getItem('varuna.powerReport.oltId') || null } catch { return null }
  })
  const [selectedSlot, setSelectedSlot] = useState(() => {
    try { return localStorage.getItem('varuna.powerReport.slot') || null } catch { return null }
  })
  const [selectedPon, setSelectedPon] = useState(() => {
    try { return localStorage.getItem('varuna.powerReport.pon') || null } catch { return null }
  })

  const fetchRows = useCallback(async ({ background = false } = {}) => {
    if (!background) {
      setLoading(true)
      setError('')
    }
    try {
      const response = await api.get('/onu/power-report/')
      const payload = response?.data
      const nextRows = asList(payload?.results ?? payload).map(normalizeRow)
      setRows(nextRows)
      setError('')
    } catch {
      if (!background) {
        setError(t('Failed to load OLT data'))
      }
    } finally {
      if (!background) setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void fetchRows({ background: false })
  }, [fetchRows])

  useEffect(() => {
    const timer = setInterval(() => {
      void fetchRows({ background: true })
    }, 30000)
    return () => clearInterval(timer)
  }, [fetchRows])


  // Derive available OLTs, slots, PONs from data
  const availableOlts = useMemo(() => {
    const map = new Map()
    rows.forEach((row) => {
      const id = String(row.oltId ?? '')
      if (id && !map.has(id)) map.set(id, row.oltName)
    })
    return Array.from(map, ([id, name]) => ({ id, name }))
  }, [rows])

  const hasOlt = selectedOltId !== null

  const availableSlots = useMemo(() => {
    if (!hasOlt) return []
    const set = new Set()
    rows.forEach((row) => {
      if (String(row.oltId ?? '') === selectedOltId) {
        set.add(String(row.slotNumber))
      }
    })
    return Array.from(set).sort((a, b) => Number(a) - Number(b))
  }, [rows, selectedOltId, hasOlt])

  const availablePons = useMemo(() => {
    if (!hasOlt || selectedSlot === null) return []
    const set = new Set()
    rows.forEach((row) => {
      if (String(row.oltId ?? '') === selectedOltId && String(row.slotNumber) === selectedSlot) {
        set.add(String(row.ponNumber))
      }
    })
    return Array.from(set).sort((a, b) => Number(a) - Number(b))
  }, [rows, selectedOltId, hasOlt, selectedSlot])

  // Wrap setters to handle cascade reset + persistence together
  const changeOlt = useCallback((id) => {
    setSelectedOltId(id)
    setSelectedSlot(null)
    setSelectedPon(null)
    try {
      if (id) localStorage.setItem('varuna.powerReport.oltId', id)
      else localStorage.removeItem('varuna.powerReport.oltId')
      localStorage.removeItem('varuna.powerReport.slot')
      localStorage.removeItem('varuna.powerReport.pon')
    } catch {}
  }, [])

  const changeSlot = useCallback((id) => {
    setSelectedSlot(id)
    setSelectedPon(null)
    try {
      if (id) localStorage.setItem('varuna.powerReport.slot', id)
      else localStorage.removeItem('varuna.powerReport.slot')
      localStorage.removeItem('varuna.powerReport.pon')
    } catch {}
  }, [])

  const changePon = useCallback((id) => {
    setSelectedPon(id)
    try {
      if (id) localStorage.setItem('varuna.powerReport.pon', id)
      else localStorage.removeItem('varuna.powerReport.pon')
    } catch {}
  }, [])

  // Location-filtered rows (before signal filter) — used for stable counts
  const locationFiltered = useMemo(() => {
    let result = rows

    if (hasOlt) {
      result = result.filter((row) => String(row.oltId ?? '') === selectedOltId)
    }
    if (hasOlt && selectedSlot !== null) {
      result = result.filter((row) => String(row.slotNumber) === selectedSlot)
    }
    if (hasOlt && selectedSlot !== null && selectedPon !== null) {
      result = result.filter((row) => String(row.ponNumber) === selectedPon)
    }

    return result
  }, [rows, selectedOltId, hasOlt, selectedSlot, selectedPon])

  // Final filtered rows (location + signal) — used for table display
  const filtered = useMemo(() => {
    if (signalFilter.length === 0) return locationFiltered
    const sigSet = new Set(signalFilter)
    return locationFiltered.filter((row) => sigSet.has(row.signal))
  }, [locationFiltered, signalFilter])

  const sortOptions = useMemo(() => [
    { id: 'worst_onu_rx', label: 'ONU RX ↓' },
    { id: 'worst_olt_rx', label: 'OLT RX ↓' },
    { id: 'best_onu_rx', label: 'ONU RX ↑' },
    { id: 'best_olt_rx', label: 'OLT RX ↑' },
  ], [])

  const sorted = useMemo(() => {
    const result = [...filtered]
    const numericVal = (value) => {
      const numeric = Number(value)
      return Number.isFinite(numeric) ? numeric : Infinity
    }

    switch (sortMode) {
      case 'worst_onu_rx':
        result.sort((a, b) => numericVal(a.onuRx) - numericVal(b.onuRx))
        break
      case 'best_onu_rx':
        result.sort((a, b) => numericVal(b.onuRx) - numericVal(a.onuRx))
        break
      case 'worst_olt_rx':
        result.sort((a, b) => numericVal(a.oltRx) - numericVal(b.oltRx))
        break
      case 'best_olt_rx':
        result.sort((a, b) => numericVal(b.oltRx) - numericVal(a.oltRx))
        break
      default:
        break
    }

    return result
  }, [filtered, sortMode])

  useEffect(() => {
    setVisibleRowsLimit(INITIAL_VISIBLE_ROWS)
  }, [signalFilter, sortMode, rows, selectedOltId, selectedSlot, selectedPon])

  const visibleRows = useMemo(() => {
    return sorted.slice(0, visibleRowsLimit)
  }, [sorted, visibleRowsLimit])

  const hasMore = visibleRows.length < sorted.length
  const sentinelRef = useRef(null)

  useEffect(() => {
    if (!hasMore) return
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisibleRowsLimit((prev) => prev + LOAD_MORE_ROWS)
        }
      },
      { rootMargin: '200px' }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasMore, visibleRows.length])

  const stats = useMemo(() => {
    const summary = { measured: 0, good: 0, warning: 0, critical: 0, noReading: 0 }
    locationFiltered.forEach((row) => {
      if (row.signal === 'unknown') {
        summary.noReading += 1
        return
      }
      summary.measured += 1
      if (row.signal === 'good') summary.good += 1
      else if (row.signal === 'warning') summary.warning += 1
      else if (row.signal === 'critical') summary.critical += 1
    })
    return summary
  }, [locationFiltered])

  const signalPills = [
    { id: 'good', label: t('Good'), dot: 'bg-emerald-500', count: 'text-emerald-600 dark:text-emerald-400' },
    { id: 'warning', label: t('Warning'), dot: 'bg-amber-500', count: 'text-amber-600 dark:text-amber-400' },
    { id: 'critical', label: t('Critical'), dot: 'bg-rose-500', count: 'text-rose-600 dark:text-rose-400' },
    { id: 'unknown', label: t('No reading'), dot: 'bg-violet-500', count: 'text-violet-600 dark:text-violet-400' },
  ]

  const toggleSignal = useCallback((id) => {
    setSignalFilter((prev) => {
      const allSelected = prev.length === 0
      if (allSelected) {
        // all active → deselect this one → keep rest
        return signalPills.filter((p) => p.id !== id).map((p) => p.id)
      }
      if (prev.includes(id)) {
        const next = prev.filter((x) => x !== id)
        return next.length >= signalPills.length ? [] : next
      }
      const next = [...prev, id]
      return next.length >= signalPills.length ? [] : next
    })
  }, [signalPills])

  const currentSortLabel = sortOptions.find((option) => option.id === sortMode)?.label || ''

  return (
    <div className="h-full flex flex-col bg-slate-100 dark:bg-slate-950">
      <div className="flex-1 min-h-0 flex flex-col px-3 lg:px-8 pt-5 pb-4">

        {/* Toolbar */}
        <div className="flex flex-col gap-2 lg:gap-0 mb-4 w-full lg:max-w-[1100px] lg:mx-auto">
          {/* Row 1: OLT + Slot + PON + Sort (mobile) | OLT + Slot + PON + pills + sort (desktop) */}
          <div className="flex items-center gap-1 relative">
            {/* Mobile: OLT in flex wrapper for equal sizing with sort */}
            <div className="flex-1 min-w-0 lg:contents">
              <FilterDropdown
                value={selectedOltId}
                onChange={changeOlt}
                options={[{ id: null, label: t('All OLTs') }, ...availableOlts.map(o => ({ id: o.id, label: o.name }))]}
                label={selectedOltId && availableOlts.find(o => o.id === selectedOltId) ? availableOlts.find(o => o.id === selectedOltId).name : t('All OLTs')}
                icon={<Server className="w-4 h-4" />}
                width="w-full lg:w-[116px]"
              />
            </div>
            <FilterDropdown
              value={selectedSlot}
              onChange={changeSlot}
              options={[{ id: null, label: t('All') }, ...availableSlots.map(s => ({ id: s, label: s }))]}
              label={selectedSlot !== null ? selectedSlot : t('All')}
              disabled={!hasOlt || availableSlots.length === 0}
              icon={<CircuitBoard className="w-4 h-4" />}
              width="w-[80px] lg:w-[72px]"
            />
            <FilterDropdown
              value={selectedPon}
              onChange={changePon}
              options={[{ id: null, label: t('All') }, ...availablePons.map(p => ({ id: p, label: p }))]}
              label={selectedPon !== null ? selectedPon : t('All')}
              disabled={selectedSlot === null || availablePons.length === 0}
              icon={<Cable className="w-4 h-4" />}
              width="w-[80px] lg:w-[72px]"
            />
            {/* Mobile: sort */}
            <div className="lg:hidden flex-1 min-w-0">
              <SortDropdown
                value={sortMode}
                onChange={setSortMode}
                options={sortOptions}
                label={currentSortLabel}
                width="w-full"
              />
            </div>
            {/* Desktop: pills absolutely centered over full row */}
            <div className="hidden lg:flex items-center gap-0.5 absolute left-1/2 -translate-x-1/2 pointer-events-auto">
              {signalPills.map((pill) => {
                const isOn = signalFilter.length === 0 || signalFilter.includes(pill.id)
                const count = pill.id === 'good' ? stats.good : pill.id === 'warning' ? stats.warning : pill.id === 'critical' ? stats.critical : stats.noReading
                return (
                  <button
                    key={pill.id}
                    type="button"
                    onClick={() => toggleSignal(pill.id)}
                    className={`inline-flex items-center gap-1 h-7 px-1 rounded transition-all active:scale-[0.97] ${
                      isOn
                        ? 'hover:bg-slate-200/50 dark:hover:bg-slate-700/40'
                        : 'opacity-35 hover:opacity-55'
                    }`}
                  >
                    <span className="h-3.5 w-3.5 flex items-center justify-center shrink-0">
                      {isOn ? (
                        <Check className="w-3 h-3 text-slate-600 dark:text-slate-400" strokeWidth={3} />
                      ) : (
                        <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />
                      )}
                    </span>
                    <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${pill.dot}`} />
                    <span className={`text-[10px] font-black tabular-nums ${isOn ? pill.count : 'text-slate-400 dark:text-slate-500'}`}>{count}</span>
                  </button>
                )
              })}
              <div className="w-px h-4 bg-slate-200 dark:bg-slate-700/60 mx-0.5" />
              <span className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400">{t('Total')}</span>
              <span className="text-[10px] font-black tabular-nums text-slate-400 dark:text-slate-500">{locationFiltered.length}</span>
            </div>
            <div className="hidden lg:block ml-auto">
              <SortDropdown
                value={sortMode}
                onChange={setSortMode}
                options={sortOptions}
                label={currentSortLabel}
              />
            </div>
          </div>

          {/* Row 2 (mobile only): Signal pills centered */}
          <div className="flex lg:hidden items-center justify-center gap-0.5 w-full">
            {signalPills.map((pill) => {
              const isOn = signalFilter.length === 0 || signalFilter.includes(pill.id)
              const count = pill.id === 'good' ? stats.good : pill.id === 'warning' ? stats.warning : pill.id === 'critical' ? stats.critical : stats.noReading
              return (
                <button
                  key={pill.id}
                  type="button"
                  onClick={() => toggleSignal(pill.id)}
                  className={`inline-flex items-center gap-1 h-7 px-1 rounded transition-all active:scale-[0.97] ${
                    isOn
                      ? 'hover:bg-slate-200/50 dark:hover:bg-slate-700/40'
                      : 'opacity-35 hover:opacity-55'
                  }`}
                >
                  <span className="h-3.5 w-3.5 flex items-center justify-center shrink-0">
                    {isOn ? (
                      <Check className="w-3 h-3 text-slate-600 dark:text-slate-400" strokeWidth={3} />
                    ) : (
                      <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />
                    )}
                  </span>
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${pill.dot}`} />
                  <span className={`text-[10px] font-black tabular-nums ${isOn ? pill.count : 'text-slate-400 dark:text-slate-500'}`}>{count}</span>
                </button>
              )
            })}
            <div className="w-px h-4 bg-slate-200 dark:bg-slate-700/60 mx-0.5" />
            <span className="text-[10px] font-black tabular-nums text-slate-400 dark:text-slate-500">{locationFiltered.length}</span>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </div>
        )}

        {/* Desktop table */}
        <div className="hidden lg:flex flex-col flex-1 min-h-0 w-full max-w-[1100px] mx-auto rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
          <div className="shrink-0 overflow-hidden bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-700/50">
            <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '800px' }}>
              <colgroup>
                <col style={{ width: '10%' }} />
                <col style={{ width: '5%' }} />
                <col style={{ width: '5%' }} />
                <col style={{ width: '5%' }} />
                <col />
                <col style={{ width: '12%' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '160px' }} />
              </colgroup>
              <thead>
                <tr>
                  <th className="px-3 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('OLT')}</th>
                  <th className="px-2 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('Slot')}</th>
                  <th className="px-2 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('PON')}</th>
                  <th className="px-2 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">ONU</th>
                  <th className="px-3 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Name')}</th>
                  <th className="px-3 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Serial')}</th>
                  <th className="px-3 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('ONU RX')}</th>
                  <th className="px-3 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('OLT RX')}</th>
                  <th className="px-2.5 py-2.5 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Leitura')}</th>
                </tr>
              </thead>
            </table>
          </div>

          <div className="flex-1 overflow-x-auto overflow-y-auto min-h-0 custom-scrollbar">
            <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '800px' }}>
              <colgroup>
                <col style={{ width: '10%' }} />
                <col style={{ width: '5%' }} />
                <col style={{ width: '5%' }} />
                <col style={{ width: '5%' }} />
                <col />
                <col style={{ width: '12%' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '120px' }} />
                <col style={{ width: '160px' }} />
              </colgroup>
              <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                {loading && rows.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-16 text-center">
                      <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('Loading live data')}</p>
                    </td>
                  </tr>
                )}
                {!loading && sorted.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-16 text-center">
                      <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('No ONU data available')}</p>
                    </td>
                  </tr>
                )}
                {visibleRows.map((row, idx) => {
                  const onuRxFormatted = formatPowerValue(row.onuRx)
                  const oltRxFormatted = formatPowerValue(row.oltRx)
                  const onuRxColor = onuRxFormatted ? powerColorClass(getPowerColor(row.onuRx, 'onu_rx', row.oltId)) : ''
                  const oltRxColor = oltRxFormatted ? powerColorClass(getPowerColor(row.oltRx, 'olt_rx', row.oltId)) : ''
                  return (
                    <tr
                      key={`d-${row.id ?? idx}`}
                      className="h-11 odd:bg-white even:bg-slate-50/50 dark:odd:bg-slate-900 dark:even:bg-slate-800/40 hover:bg-slate-100/70 dark:hover:bg-slate-800/60 transition-colors"
                    >
                      <td className="px-3 py-0 align-middle text-[11px] font-bold text-slate-700 dark:text-slate-200 truncate">{row.oltName}</td>
                      <td className="px-2 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center">{row.slotNumber}</td>
                      <td className="px-2 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center">{row.ponNumber}</td>
                      <td className="px-2 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center">{row.onuId}</td>
                      <td className="px-3 py-0 align-middle text-[11px] font-bold text-slate-800 dark:text-slate-100 truncate">{row.clientName || <span className="text-slate-300 dark:text-slate-600">—</span>}</td>
                      <td className="px-3 py-0 align-middle text-[11px] font-semibold font-mono tracking-tight text-slate-500 dark:text-slate-400 truncate">{row.serial || <span className="text-slate-300 dark:text-slate-600">—</span>}</td>
                      <td className={`px-3 py-0 align-middle text-[11px] font-bold tabular-nums text-right ${onuRxFormatted ? onuRxColor : 'text-slate-300 dark:text-slate-600'}`}>{onuRxFormatted || '—'}</td>
                      <td className={`px-3 py-0 align-middle text-[11px] font-bold tabular-nums text-right ${oltRxFormatted ? oltRxColor : 'text-slate-300 dark:text-slate-600'}`}>{oltRxFormatted || '—'}</td>
                      <td className={`px-2.5 py-0 align-middle text-[11px] font-semibold whitespace-nowrap tabular-nums text-center ${row.readingAt ? 'text-slate-500 dark:text-slate-400' : 'text-slate-300 dark:text-slate-600'}`}>{formatReadingAt(row.readingAt, i18n.language)}</td>
                    </tr>
                  )
                })}
                {hasMore && (
                  <tr ref={sentinelRef}>
                    <td colSpan={9} className="py-4 text-center">
                      <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{t('Loading live data')}</p>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Mobile cards */}
        <div className="flex lg:hidden flex-col flex-1 min-h-0 w-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
          <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-1.5">
            {loading && rows.length === 0 && (
              <div className="py-16 text-center">
                <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('Loading live data')}</p>
              </div>
            )}
            {!loading && sorted.length === 0 && (
              <div className="py-16 text-center">
                <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('No ONU data available')}</p>
              </div>
            )}
            {visibleRows.map((row, idx) => {
              const onuRxFormatted = formatPowerValue(row.onuRx)
              const oltRxFormatted = formatPowerValue(row.oltRx)
              const onuRxColor = onuRxFormatted ? powerColorClass(getPowerColor(row.onuRx, 'onu_rx', row.oltId)) : ''
              const oltRxColor = oltRxFormatted ? powerColorClass(getPowerColor(row.oltRx, 'olt_rx', row.oltId)) : ''
              const hasPower = onuRxFormatted || oltRxFormatted
              const hasSerial = row.serial && row.serial !== '—' && row.serial !== '-'
              return (
                <div
                  key={`m-${row.id ?? idx}`}
                  className="rounded-md border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-3 py-2 flex items-center gap-2 active:bg-slate-50 dark:active:bg-slate-800/60 transition-colors"
                >
                  <div className="min-w-0 flex-1 flex flex-col gap-1">
                    <span className="text-[11px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums uppercase">
                      {[
                        !selectedOltId && row.oltName,
                        [
                          selectedSlot === null && row.slotNumber,
                          selectedPon === null && row.ponNumber,
                          row.onuId,
                        ].filter(Boolean).join('/'),
                      ].filter(Boolean).join(' · ')}
                    </span>
                    {row.clientName ? (
                      <span className="text-[12px] font-bold text-slate-800 dark:text-slate-100 truncate">{row.clientName}</span>
                    ) : (
                      <span className="text-[11px] text-slate-300 dark:text-slate-600">—</span>
                    )}
                    {hasSerial ? (
                      <span className="text-[11px] font-semibold font-mono tracking-[0.01em] text-slate-500 dark:text-slate-400 truncate">{row.serial}</span>
                    ) : (
                      <span className="text-[11px] text-slate-300 dark:text-slate-600">—</span>
                    )}
                  </div>
                  <div className="shrink-0 flex flex-col gap-1">
                    {hasPower ? (
                      <>
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                          <span className="font-mono text-slate-400 dark:text-slate-500">{t('ONU')}</span>
                          <span className={`w-[76px] text-right font-semibold ${onuRxFormatted ? onuRxColor : 'text-slate-300 dark:text-slate-600'}`}>{onuRxFormatted || '—'}</span>
                        </span>
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold tabular-nums whitespace-nowrap">
                          <span className="font-mono text-slate-400 dark:text-slate-500">{t('OLT')}</span>
                          <span className={`w-[76px] text-right font-semibold ${oltRxFormatted ? oltRxColor : 'text-slate-300 dark:text-slate-600'}`}>{oltRxFormatted || '—'}</span>
                        </span>
                        <span className="self-stretch text-left text-[10px] font-semibold tabular-nums text-slate-400 dark:text-slate-500">
                          {formatReadingAt(row.readingAt, i18n.language)}
                        </span>
                      </>
                    ) : (
                      <span className="text-[11px] font-semibold tabular-nums text-slate-300 dark:text-slate-600">—</span>
                    )}
                  </div>
                </div>
              )
            })}
            {hasMore && (
              <div ref={sentinelRef} className="py-3 text-center">
                <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{t('Loading live data')}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

const SortDropdown = ({ value, onChange, options, label, width }) => (
  <DropdownMenu.Root>
    <DropdownMenu.Trigger asChild>
      <button
        className={`flex items-center gap-0.5 h-7 rounded-md border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 shadow-sm text-slate-500 hover:text-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-all active:scale-[0.97] pl-1.5 pr-1 ${width || 'w-[120px]'}`}
      >
        <ArrowDownUp className="w-3.5 h-3.5 shrink-0" />
        <span className="flex-1 min-w-0 text-center text-[10px] font-black uppercase tracking-[0.03em] truncate text-emerald-600 dark:text-emerald-400">
          {label}
        </span>
        <ChevronDown className="w-2.5 h-2.5 shrink-0" />
      </button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        className="w-[136px] bg-white dark:bg-slate-900 rounded-xl p-1 shadow-xl border border-slate-200 dark:border-slate-700/50 z-[220] animate-in fade-in zoom-in-95 duration-150"
        sideOffset={8}
        align="end"
      >
        {options.map((option) => (
          <DropdownMenu.Item
            key={option.id}
            onSelect={() => onChange(option.id)}
            className={`relative flex items-center justify-center px-2 py-1.5 rounded-lg outline-none cursor-pointer transition-colors ${
              value === option.id ? 'bg-slate-50 dark:bg-slate-800/60' : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'
            }`}
          >
            <span className="absolute left-2 h-4 w-4 flex items-center justify-center">
              {value === option.id ? (
                <Check className="w-3.5 h-3.5 text-slate-700 dark:text-slate-300" strokeWidth={3} />
              ) : (
                <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />
              )}
            </span>
            <span className={`text-[10px] font-black uppercase tracking-[0.04em] text-center ${
              value === option.id ? 'text-slate-800 dark:text-slate-200' : 'text-slate-500 dark:text-slate-400'
            }`}>
              {option.label}
            </span>
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
)

const FilterDropdown = ({ value, onChange, options, label, icon, disabled = false, width }) => (
  <DropdownMenu.Root>
    <DropdownMenu.Trigger asChild disabled={disabled}>
      <button
        disabled={disabled}
        className={`flex items-center gap-0.5 h-7 rounded-md border transition-all active:scale-[0.97] pl-1.5 pr-1 ${width || ''} ${disabled
            ? 'cursor-not-allowed opacity-45 border-slate-200/60 bg-slate-50 dark:border-slate-700/30 dark:bg-slate-800/40'
            : 'bg-white dark:bg-slate-800 border-slate-200/80 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 shadow-sm'
          }`}
      >
        {icon && <span className="shrink-0 [&>svg]:w-3.5 [&>svg]:h-3.5 text-slate-400 dark:text-slate-500">{icon}</span>}
        <span className={`flex-1 min-w-0 text-[10px] font-black uppercase tracking-[0.03em] truncate text-center outline-none ${disabled
            ? 'text-slate-400 dark:text-slate-500'
            : value !== null ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-700 dark:text-slate-300'
          }`}>
          {label}
        </span>
        <ChevronDown className="w-2.5 h-2.5 shrink-0 text-slate-400 dark:text-slate-500" />
      </button>
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal>
      <DropdownMenu.Content
        className="min-w-[140px] bg-white dark:bg-slate-900 rounded-xl p-1 shadow-xl border border-slate-200 dark:border-slate-700/50 z-[220] animate-in fade-in zoom-in-95 duration-150"
        sideOffset={6}
        align="start"
      >
        {options.map((option) => (
          <DropdownMenu.Item
            key={option.id}
            onSelect={() => onChange(option.id)}
            className={`relative flex items-center gap-2 px-2 py-1.5 rounded-lg outline-none cursor-pointer transition-colors ${value === option.id ? 'bg-slate-100 dark:bg-slate-800/60' : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'
              }`}
          >
            <span className="h-4 w-4 flex items-center justify-center shrink-0">
              {value === option.id ? (
                <Check className="w-3.5 h-3.5 text-slate-700 dark:text-slate-300" strokeWidth={3} />
              ) : (
                <span className="w-1.5 h-1.5 rounded-full bg-slate-300 dark:bg-slate-600" />
              )}
            </span>
            <span className={`text-[10px] font-black uppercase tracking-[0.04em] truncate ${value === option.id ? 'text-slate-800 dark:text-slate-200' : 'text-slate-600 dark:text-slate-300'}`}>
              {option.label}
            </span>
          </DropdownMenu.Item>
        ))}
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>
)
