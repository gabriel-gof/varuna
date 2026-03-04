import { useCallback, useRef, useState, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, X } from 'lucide-react'

import api from '../services/api'
import { getPowerColor, powerColorClass } from '../utils/powerThresholds'

const toDateKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

const formatDateLabel = (key) => {
  const [, m, d] = key.split('-')
  return `${d}/${m}`
}

const formatTimestamp = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const d = String(date.getDate()).padStart(2, '0')
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const y = String(date.getFullYear()).slice(2)
  const h = String(date.getHours()).padStart(2, '0')
  const min = String(date.getMinutes()).padStart(2, '0')
  return `${d}/${m}/${y} ${h}:${min}`
}

const formatDuration = (startValue, endValue) => {
  if (!startValue || !endValue) return '—'
  const start = new Date(startValue)
  const end = new Date(endValue)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return '—'

  const diff = end.getTime() - start.getTime()
  if (diff <= 0) return '—'

  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(mins / 60)
  const days = Math.floor(hours / 24)
  if (days > 0) return `${days}d ${hours % 24}h`
  if (hours > 0) return `${hours}h ${mins % 60}m`
  return `${mins}m`
}

const normalizeAlarm = (alarm) => ({
  id: alarm?.id,
  type: alarm?.event_type || 'unknown',
  start: alarm?.start_at || null,
  end: alarm?.end_at || null,
  status: alarm?.status || 'resolved',
})

const normalizePowerPoint = (point) => {
  const toNullableNumber = (value) => {
    if (value === null || value === undefined || value === '') return null
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  const timestamp = Date.parse(point?.timestamp || '')
  if (!Number.isFinite(timestamp)) return null
  const onuRx = toNullableNumber(point?.onu_rx_power)
  const oltRx = toNullableNumber(point?.olt_rx_power)
  if (!Number.isFinite(onuRx) && !Number.isFinite(oltRx)) return null
  return {
    timestamp,
    onuRx,
    oltRx,
  }
}

const buildLastNDays = (n) => {
  const days = []
  const now = new Date()
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    days.push(toDateKey(d))
  }
  return days
}


export const AlarmHistory = () => {
  const { t } = useTranslation()

  const [activeTab, setActiveTab] = useState('status')
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [alarms, setAlarms] = useState([])
  const [powerHistory, setPowerHistory] = useState([])

  // Local search state
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchFocused, setSearchFocused] = useState(false)
  const [selectedClient, setSelectedClient] = useState(null)
  const searchContainerRef = useRef(null)

  // Debounced search effect
  useEffect(() => {
    const trimmed = searchTerm.trim()
    if (trimmed.length < 2) {
      setSearchResults([])
      setSearchLoading(false)
      return
    }

    setSearchLoading(true)
    const timer = setTimeout(() => {
      let cancelled = false
      const doSearch = async () => {
        try {
          const response = await api.get('/onu/alarm-clients/', { params: { search: trimmed, limit: 7 } })
          if (cancelled) return
          const results = Array.isArray(response?.data?.results) ? response.data.results : []
          setSearchResults(results)
        } catch {
          if (cancelled) return
          setSearchResults([])
        } finally {
          if (!cancelled) setSearchLoading(false)
        }
      }
      doSearch()
      return () => { cancelled = true }
    }, 300)
    return () => clearTimeout(timer)
  }, [searchTerm])

  // Click-outside to close suggestions
  useEffect(() => {
    const handlePointerDown = (event) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(event.target)) {
        setSearchFocused(false)
      }
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [])

  const handleSelectClient = useCallback((client) => {
    setSelectedClient(client)
    setSearchTerm('')
    setSearchResults([])
    setSearchFocused(false)
    setDetailError('')
  }, [])

  const handleClearClient = useCallback(() => {
    setSelectedClient(null)
    setSearchTerm('')
    setSearchResults([])
    setAlarms([])
    setPowerHistory([])
    setDetailError('')
  }, [])
  const historyDays = selectedClient?.history_days || 7
  const selectedClientLabel = selectedClient
    ? ((selectedClient.client_name && selectedClient.client_name !== '-')
      ? selectedClient.client_name
      : (selectedClient.serial || '-'))
    : searchTerm

  useEffect(() => {
    if (!selectedClient?.id) {
      setAlarms([])
      setPowerHistory([])
      setDetailError('')
      setDetailLoading(false)
      return
    }

    let cancelled = false
    const loadDetail = async () => {
      setDetailLoading(true)
      setDetailError('')
      try {
        const response = await api.get(`/onu/${selectedClient.id}/alarm-history/`, {
          params: {
            alarm_days: historyDays,
            power_days: historyDays,
            alarm_limit: 1000,
            max_power_points: 744,
          },
        })
        if (cancelled) return

        const payload = response?.data || {}
        const nextAlarms = (payload.alarms || []).map(normalizeAlarm)
        const nextPower = (payload.power_history || []).map(normalizePowerPoint).filter(Boolean)

        setAlarms(nextAlarms)
        setPowerHistory(nextPower)
      } catch {
        if (cancelled) return
        setAlarms([])
        setPowerHistory([])
        setDetailError(t('Failed to load OLT data'))
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    }

    loadDetail()
    return () => { cancelled = true }
  }, [selectedClient, t])

  const eventTypeStyle = (type) => {
    if (type === 'dying_gasp') return 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200 dark:bg-blue-500/15 dark:text-blue-300 dark:ring-blue-400/30'
    if (type === 'link_loss') return 'bg-rose-50 text-rose-600 ring-1 ring-inset ring-rose-200 dark:bg-rose-500/15 dark:text-rose-300 dark:ring-rose-400/30'
    return 'bg-purple-50 text-purple-600 ring-1 ring-inset ring-purple-200 dark:bg-purple-500/15 dark:text-purple-300 dark:ring-purple-400/30'
  }

  const eventTypeDot = (type) => {
    if (type === 'dying_gasp') return 'bg-blue-500'
    if (type === 'link_loss') return 'bg-rose-500'
    return 'bg-purple-500'
  }

  const eventTypeLabel = (type) => {
    if (type === 'dying_gasp') return t('Dying Gasp')
    if (type === 'link_loss') return t('Link Loss')
    return t('Unknown')
  }

  const lastNDays = useMemo(() => buildLastNDays(historyDays), [historyDays])

  // All N days, zeros for days with no events
  const dailyDisconnections = useMemo(() => {
    const buckets = {}
    for (const alarm of alarms) {
      if (!alarm.start) continue
      const key = toDateKey(new Date(alarm.start))
      if (!buckets[key]) buckets[key] = { date: key, link_loss: 0, dying_gasp: 0, unknown: 0 }
      const reason = alarm.type === 'link_loss' ? 'link_loss' : alarm.type === 'dying_gasp' ? 'dying_gasp' : 'unknown'
      buckets[key][reason]++
    }
    return lastNDays.map(date => buckets[date] || { date, link_loss: 0, dying_gasp: 0, unknown: 0 })
  }, [alarms, lastNDays])

  const sortedPowerAsc = useMemo(() =>
    [...powerHistory].sort((a, b) => a.timestamp - b.timestamp),
    [powerHistory]
  )


  // Individual samples sorted newest first for the table
  const sortedPowerHistory = useMemo(() =>
    [...powerHistory].sort((a, b) => b.timestamp - a.timestamp),
    [powerHistory]
  )

  const totalDisconnections = useMemo(() =>
    dailyDisconnections.reduce((sum, d) => sum + d.link_loss + d.dying_gasp + d.unknown, 0),
    [dailyDisconnections]
  )

  return (
    <div className="h-full flex flex-col bg-slate-100 dark:bg-slate-950">
      <div className="flex-1 flex flex-col min-h-0 px-3 lg:px-8 pt-5 pb-4">

        {/* Toolbar */}
        <div className="mb-4 w-full lg:max-w-[1400px] lg:mx-auto">
          <div className="flex items-center gap-1.5 lg:gap-2">
            {/* Search */}
            <div ref={searchContainerRef} className="relative min-w-0 flex-1 lg:flex-none lg:w-[280px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-300 dark:text-slate-500 pointer-events-none" />
              <input
                type="text"
                placeholder={t('Search ONU...')}
                value={selectedClientLabel}
                readOnly={Boolean(selectedClient)}
                onFocus={() => { if (!selectedClient) setSearchFocused(true) }}
                onChange={(e) => { if (!selectedClient) setSearchTerm(e.target.value) }}
                className="h-9 w-full bg-white dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-lg pl-9 pr-8 text-[11px] text-compact font-semibold text-slate-600 dark:text-slate-200 shadow-sm transition-all placeholder:text-slate-400/70 dark:placeholder:text-slate-500 focus:border-emerald-500/30 focus:ring-2 focus:ring-emerald-500/10 focus:outline-none"
              />
              {(selectedClient || searchTerm) && (
                <button
                  type="button"
                  onClick={selectedClient ? handleClearClient : () => { setSearchTerm(''); setSearchResults([]) }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                  aria-label={t('Clear')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
              {!selectedClient && searchFocused && searchTerm.trim().length >= 2 && (
                <div className="absolute left-0 top-11 z-30 w-full lg:w-[340px] p-2 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-xl max-h-[320px] overflow-y-auto">
                  {searchLoading && searchResults.length === 0 && (
                    <p className="px-2 py-2 text-[11px] font-semibold text-slate-400">{t('Loading...')}</p>
                  )}
                  {!searchLoading && searchResults.length === 0 && (
                    <p className="px-2 py-2 text-[11px] font-semibold text-slate-400">{t('No clients found')}</p>
                  )}
                  {searchResults.map((client) => (
                    <button
                      key={client.id}
                      type="button"
                      onClick={() => handleSelectClient(client)}
                      className="w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                    >
                      <p className="text-[11px] font-black tracking-tight text-slate-800 dark:text-slate-100 whitespace-nowrap overflow-hidden text-ellipsis">
                        {client.client_name || '-'}
                      </p>
                      <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap overflow-hidden text-ellipsis mt-0.5">
                        {client.serial || '-'}
                      </p>
                      <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 whitespace-nowrap overflow-hidden text-ellipsis mt-0.5">
                        {client.olt_name} · {client.slot_id}/{client.pon_id}/{client.onu_number}
                      </p>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Mobile: tab switcher — always visible */}
            <div className="flex lg:hidden shrink-0">
              <div className="inline-flex h-7 items-center gap-0.5 p-0.5 rounded-md border border-slate-200/80 dark:border-slate-700/80 bg-slate-50/90 dark:bg-slate-900/70">
                {[
                  { id: 'status', label: t('Status') },
                  { id: 'power', label: t('Potência') },
                ].map((tab) => {
                  const isActive = activeTab === tab.id
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setActiveTab(tab.id)}
                      className={`h-6 w-[76px] rounded text-[10px] font-black uppercase tracking-[0.06em] transition-all active:scale-[0.97] ${
                        isActive
                          ? 'bg-white dark:bg-slate-800 text-emerald-600 dark:text-emerald-400 shadow-sm ring-1 ring-black/5 dark:ring-white/10'
                          : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-white/70 dark:hover:bg-slate-800/60'
                      }`}
                    >
                      {tab.label}
                    </button>
                  )
                })}
              </div>
            </div>

            {selectedClient && <span className="hidden lg:inline ml-auto text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500">{t('Last {{days}} days', { days: historyDays })}</span>}
          </div>
        </div>

        {detailError && (
          <div className="mb-4 lg:max-w-[1400px] lg:mx-auto rounded-lg border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
            {detailError}
          </div>
        )}

        {/* Empty state — shown on both breakpoints when no client selected */}
        {!selectedClient && (
          <div className="flex flex-1 items-center justify-center pb-[28vh]">
            <div className="flex flex-col items-center gap-3">
              <Search className="w-8 h-8 text-slate-300 dark:text-slate-600" />
              <p className="text-sm font-semibold text-slate-400 dark:text-slate-500">{t('Select a client to view alarm history')}</p>
            </div>
          </div>
        )}

        {/* Desktop: side-by-side grid */}
        {selectedClient && (
          <div className="hidden lg:grid grid-cols-2 gap-3 flex-1 min-h-0 overflow-hidden max-w-[1400px] mx-auto w-full">

            {/* Left: Disconnection History */}
            <div className="flex flex-col min-h-0 rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
              <div className="shrink-0 px-4 pt-3 pb-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400">{t('Disconnection History')}</p>
                  {totalDisconnections > 0 && (
                    <span className="text-[10px] font-black tabular-nums text-slate-400 dark:text-slate-500">{t('Total')}: {totalDisconnections}</span>
                  )}
                </div>
                {detailLoading && alarms.length === 0 ? (
                  <p className="text-[11px] font-semibold text-slate-400 py-4">{t('Loading live data')}</p>
                ) : (
                  <DisconnectionChart data={dailyDisconnections} t={t} />
                )}
              </div>
              <div className="shrink-0 bg-slate-50/80 dark:bg-slate-800/60 border-y border-slate-200/80 dark:border-slate-700/50">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col style={{ width: '110px' }} />
                    <col />
                    <col />
                    <col style={{ width: '72px' }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="px-3 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Event Type')}</th>
                      <th className="px-2.5 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center whitespace-nowrap">{t('Start')}</th>
                      <th className="px-2.5 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center whitespace-nowrap">{t('End')}</th>
                      <th className="px-2.5 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center whitespace-nowrap">{t('Duration')}</th>
                    </tr>
                  </thead>
                </table>
              </div>
              <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col style={{ width: '110px' }} />
                    <col />
                    <col />
                    <col style={{ width: '72px' }} />
                  </colgroup>
                  <tbody>
                    {!detailLoading && alarms.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-4 py-10 text-center">
                          <p className="text-[11px] font-semibold text-slate-400">{t('No alarm data available')}</p>
                        </td>
                      </tr>
                    )}
                    {alarms.map((alarm, idx) => (
                      <tr key={alarm.id} className={`h-9 ${idx % 2 === 0 ? 'bg-white dark:bg-slate-900' : 'bg-slate-50 dark:bg-slate-800/50'} hover:bg-slate-100/70 dark:hover:bg-slate-800/60 transition-colors`}>
                        <td className="px-3 py-0 align-middle">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase whitespace-nowrap ${eventTypeStyle(alarm.type)}`}>
                            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${eventTypeDot(alarm.type)}`} />
                            {eventTypeLabel(alarm.type)}
                          </span>
                        </td>
                        <td className="px-2.5 py-0 align-middle text-[10px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center whitespace-nowrap">{formatTimestamp(alarm.start)}</td>
                        <td className="px-2.5 py-0 align-middle text-[10px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center whitespace-nowrap">{formatTimestamp(alarm.end)}</td>
                        <td className="px-2.5 py-0 align-middle text-[10px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center whitespace-nowrap">{formatDuration(alarm.start, alarm.end)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Right: Power History */}
            <div className="flex flex-col min-h-0 rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
              <div className="shrink-0 px-4 pt-3 pb-3">
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-2">{t('Power History')}</p>
                {detailLoading && powerHistory.length === 0 ? (
                  <p className="text-[11px] font-semibold text-slate-400 py-4">{t('Loading live data')}</p>
                ) : (
                  <PowerChart data={sortedPowerAsc} lastNDays={lastNDays} t={t} />
                )}
              </div>
              <div className="shrink-0 bg-slate-50/80 dark:bg-slate-800/60 border-y border-slate-200/80 dark:border-slate-700/50">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col />
                    <col style={{ width: '100px' }} />
                    <col style={{ width: '100px' }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="px-3 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Reading')}</th>
                      <th className="px-2.5 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right whitespace-nowrap">ONU Rx</th>
                      <th className="px-3 py-2 text-[10px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right whitespace-nowrap">OLT Rx</th>
                    </tr>
                  </thead>
                </table>
              </div>
              <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col />
                    <col style={{ width: '100px' }} />
                    <col style={{ width: '100px' }} />
                  </colgroup>
                  <tbody>
                    {sortedPowerHistory.length === 0 ? (
                      <tr>
                        <td colSpan={3} className="px-4 py-10 text-center">
                          <p className="text-[11px] font-semibold text-slate-400">{t('Power data not available')}</p>
                        </td>
                      </tr>
                    ) : sortedPowerHistory.map((pt, idx) => (
                      <tr key={pt.timestamp} className={`h-9 ${idx % 2 === 0 ? 'bg-white dark:bg-slate-900' : 'bg-slate-50 dark:bg-slate-800/50'} hover:bg-slate-100/70 dark:hover:bg-slate-800/60 transition-colors`}>
                        <td className="px-3 py-0 align-middle text-[10px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums whitespace-nowrap">{formatTimestamp(new Date(pt.timestamp).toISOString())}</td>
                        <td className="px-2.5 py-0 align-middle text-[10px] font-semibold tabular-nums text-right">{pt.onuRx != null ? <span className={powerColorClass(getPowerColor(pt.onuRx, 'onu_rx', selectedClient.olt_id))}>{pt.onuRx.toFixed(2)}</span> : <span className="text-slate-300 dark:text-slate-600">—</span>}</td>
                        <td className="px-3 py-0 align-middle text-[10px] font-semibold tabular-nums text-right">{pt.oltRx != null ? <span className={powerColorClass(getPowerColor(pt.oltRx, 'olt_rx', selectedClient.olt_id))}>{pt.oltRx.toFixed(2)}</span> : <span className="text-slate-300 dark:text-slate-600">—</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* Mobile: tab-switched single card */}
        {selectedClient && (
          <div className="flex lg:hidden flex-col flex-1 min-h-0 w-full">
            {activeTab === 'status' && (
              <div className="flex flex-col flex-1 min-h-0 rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                {/* Title */}
                <div className="shrink-0 px-3 pt-3 pb-2 flex items-center justify-between">
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400">{t('Disconnection History')}</p>
                  {totalDisconnections > 0 && (
                    <span className="text-[10px] font-black tabular-nums text-slate-400 dark:text-slate-500">{t('Total')}: {totalDisconnections}</span>
                  )}
                </div>

                {/* Chart — all N days, no scroll */}
                <div className="shrink-0 border-y border-slate-200/80 dark:border-slate-700/50 px-2 pt-1">
                  <DisconnectionChart data={dailyDisconnections} t={t} mobile />
                </div>

                {/* Column headers — pinned */}
                <div className="shrink-0 bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-700/50">
                  <table className="w-full table-fixed text-left border-collapse">
                    <colgroup><col style={{ width: '108px' }} /><col /><col /><col style={{ width: '72px' }} /></colgroup>
                    <thead>
                      <tr>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Event')}</th>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('Start')}</th>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('End')}</th>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('Duration')}</th>
                      </tr>
                    </thead>
                  </table>
                </div>

                {/* Rows — scrollable */}
                <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
                  <table className="w-full table-fixed text-left border-collapse">
                    <colgroup><col style={{ width: '108px' }} /><col /><col /><col style={{ width: '72px' }} /></colgroup>
                    <tbody>
                      {detailLoading && alarms.length === 0 ? (
                        <tr><td colSpan={4} className="px-3 py-10 text-center text-[11px] font-semibold text-slate-400">{t('Loading live data')}</td></tr>
                      ) : alarms.length === 0 ? (
                        <tr><td colSpan={4} className="px-3 py-10 text-center text-[11px] font-semibold text-slate-400">{t('No alarm data available')}</td></tr>
                      ) : alarms.map((alarm, idx) => (
                        <tr key={alarm.id} className={`h-8 ${idx % 2 === 0 ? 'bg-white dark:bg-slate-900' : 'bg-slate-50 dark:bg-slate-800/50'}`}>
                          <td className="px-2 py-0 align-middle">
                            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-black uppercase whitespace-nowrap ${eventTypeStyle(alarm.type)}`}>
                              <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${eventTypeDot(alarm.type)}`} />
                              {eventTypeLabel(alarm.type)}
                            </span>
                          </td>
                          <td className="px-2 py-0 align-middle text-[9px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center whitespace-nowrap">{formatTimestamp(alarm.start)}</td>
                          <td className="px-2 py-0 align-middle text-[9px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center whitespace-nowrap">{formatTimestamp(alarm.end)}</td>
                          <td className="px-2 py-0 align-middle text-[9px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center whitespace-nowrap">{formatDuration(alarm.start, alarm.end)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Footer */}
                <div className="shrink-0 border-t border-slate-200/80 dark:border-slate-700/50 px-3 py-1.5 flex justify-center">
                  <span className="text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500">{t('Last {{days}} days', { days: historyDays })}</span>
                </div>
              </div>
            )}

            {activeTab === 'power' && (
              <div className="flex flex-col flex-1 min-h-0 rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                {/* Title */}
                <div className="shrink-0 px-3 pt-3 pb-2">
                  <p className="text-[10px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400">{t('Power History')}</p>
                </div>

                {/* Chart — all N days, no scroll */}
                <div className="shrink-0 border-y border-slate-200/80 dark:border-slate-700/50 px-2 pt-1">
                  <PowerChart data={sortedPowerAsc} lastNDays={lastNDays} t={t} mobile />
                </div>

                {/* Column headers — pinned */}
                <div className="shrink-0 bg-slate-50/80 dark:bg-slate-800/60 border-b border-slate-200/80 dark:border-slate-700/50">
                  <table className="w-full table-fixed text-left border-collapse">
                    <colgroup><col style={{ width: '110px' }} /><col /><col /></colgroup>
                    <thead>
                      <tr>
                        <th className="px-3 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Reading')}</th>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">ONU Rx</th>
                        <th className="px-2 py-2 text-[9px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">OLT Rx</th>
                      </tr>
                    </thead>
                  </table>
                </div>

                {/* Rows — scrollable */}
                <div className="flex-1 overflow-y-auto min-h-0 custom-scrollbar">
                  <table className="w-full table-fixed text-left border-collapse">
                    <colgroup><col style={{ width: '110px' }} /><col /><col /></colgroup>
                    <tbody>
                      {detailLoading && powerHistory.length === 0 ? (
                        <tr><td colSpan={3} className="px-3 py-10 text-center text-[11px] font-semibold text-slate-400">{t('Loading live data')}</td></tr>
                      ) : sortedPowerHistory.length === 0 ? (
                        <tr><td colSpan={3} className="px-3 py-10 text-center text-[11px] font-semibold text-slate-400">{t('Power data not available')}</td></tr>
                      ) : sortedPowerHistory.map((pt, idx) => (
                        <tr key={pt.timestamp} className={`h-8 ${idx % 2 === 0 ? 'bg-white dark:bg-slate-900' : 'bg-slate-50 dark:bg-slate-800/50'}`}>
                          <td className="px-3 py-0 align-middle text-[9px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums whitespace-nowrap">{formatTimestamp(new Date(pt.timestamp).toISOString())}</td>
                          <td className="px-2 py-0 align-middle text-[9px] font-semibold tabular-nums text-center">
                            {pt.onuRx != null ? <span className={powerColorClass(getPowerColor(pt.onuRx, 'onu_rx', selectedClient.olt_id))}>{pt.onuRx.toFixed(2)}</span> : <span className="text-slate-300 dark:text-slate-600">—</span>}
                          </td>
                          <td className="px-2 py-0 align-middle text-[9px] font-semibold tabular-nums text-center">
                            {pt.oltRx != null ? <span className={powerColorClass(getPowerColor(pt.oltRx, 'olt_rx', selectedClient.olt_id))}>{pt.oltRx.toFixed(2)}</span> : <span className="text-slate-300 dark:text-slate-600">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Footer */}
                <div className="shrink-0 border-t border-slate-200/80 dark:border-slate-700/50 px-3 py-1.5 flex justify-center">
                  <span className="text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500">{t('Last {{days}} days', { days: historyDays })}</span>
                </div>
              </div>
            )}
          </div>
        )}

      </div>
    </div>
  )
}

const DisconnectionChart = ({ data, t, mobile = false }) => {
  const [hoveredIdx, setHoveredIdx] = useState(null)

  const width = 500
  const height = 200
  const padL = 40
  const padR = 12
  const padT = 8
  const padB = 36
  const chartW = width - padL - padR
  const chartH = height - padT - padB

  const reasons = ['link_loss', 'dying_gasp', 'unknown']
  const colors = { link_loss: '#f43f5e', dying_gasp: '#3b82f6', unknown: '#8b5cf6' }
  const labels = { link_loss: t('Link Loss'), dying_gasp: t('Dying Gasp'), unknown: t('Unknown') }

  const hasData = data.length > 0

  const rawMax = hasData ? Math.max(1, ...data.map((d) => Math.max(d.link_loss, d.dying_gasp, d.unknown))) : 1
  const yStep = 2
  const yMax = Math.max(6, Math.ceil(rawMax / yStep) * yStep)
  const ySteps = []
  for (let v = 0; v <= yMax; v += yStep) ySteps.push(v)

  const groupWidth = hasData ? chartW / data.length : chartW
  const barGap = Math.max(1, groupWidth * 0.1)
  const barArea = groupWidth - barGap
  const barW = Math.min(barArea / 3, 14)

  const toY = (v) => padT + chartH - (v / yMax) * chartH
  const xForIdx = (i) => padL + groupWidth * i + groupWidth / 2

  const totals = { link_loss: 0, dying_gasp: 0, unknown: 0 }
  data.forEach((d) => { totals.link_loss += d.link_loss; totals.dying_gasp += d.dying_gasp; totals.unknown += d.unknown })

  const hovered = hoveredIdx !== null ? data[hoveredIdx] : null
  const hoveredCssLeft = hoveredIdx !== null ? (xForIdx(hoveredIdx) / width) * 100 : 0

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        onMouseLeave={() => setHoveredIdx(null)}
      >
        {/* Grid lines */}
        {ySteps.map((v) => (
          <line key={v} x1={padL} x2={width - padR} y1={toY(v)} y2={toY(v)} stroke="currentColor" strokeWidth="0.5" className="text-slate-200 dark:text-slate-700/50" />
        ))}
        {/* Y-axis labels */}
        {ySteps.map((v) => (
          <text key={v} x={padL - 4} y={toY(v) + 4} textAnchor="end" className="fill-slate-500 dark:fill-slate-400" style={{ fontSize: mobile ? '11px' : '8px', fontWeight: mobile ? 700 : 600 }}>
            {v}
          </text>
        ))}

        {/* Hover highlight column */}
        {hoveredIdx !== null && (
          <rect
            x={padL + groupWidth * hoveredIdx}
            y={padT}
            width={groupWidth}
            height={chartH}
            fill="currentColor"
            className="text-slate-400/8 dark:text-slate-300/8"
            pointerEvents="none"
          />
        )}

        {/* Bars */}
        {data.map((d, i) => {
          const activeReasons = reasons.filter((r) => d[r] > 0)
          if (!activeReasons.length) return null
          const cx = xForIdx(i)
          const totalBarWidth = activeReasons.length * barW + (activeReasons.length - 1) * 1
          const startX = cx - totalBarWidth / 2
          return (
            <g key={`bar-${d.date}`} pointerEvents="none">
              {activeReasons.map((r, ri) => {
                const bx = startX + ri * (barW + 1)
                const bh = Math.max(1, (d[r] / yMax) * chartH)
                return (
                  <rect key={r} x={bx} y={toY(d[r])} width={barW} height={bh} rx="1.5" fill={colors[r]} opacity={hoveredIdx === i ? 1 : 0.9} />
                )
              })}
            </g>
          )
        })}

        {/* Invisible hover areas — full height, all days */}
        {data.map((d, i) => (
          <rect
            key={`ha-${d.date}`}
            x={padL + groupWidth * i}
            y={padT}
            width={groupWidth}
            height={chartH}
            fill="transparent"
            style={{ cursor: 'crosshair' }}
            onMouseEnter={() => setHoveredIdx(i)}
          />
        ))}

        {/* X-axis: dd/mm labels — thin out when dense */}
        {data.map((d, i) => {
          const step = data.length > 20 ? 4 : data.length > 10 ? 2 : 1
          if (i % step !== 0) return null
          return (
            <text key={`day-${d.date}`} x={xForIdx(i)} y={height - 14} textAnchor="middle" className="fill-slate-500 dark:fill-slate-400" style={{ fontSize: mobile ? '10px' : '8px', fontWeight: mobile ? 700 : 600 }}>
              {formatDateLabel(d.date)}
            </text>
          )
        })}
      </svg>

      {/* Hover tooltip */}
      {hovered !== null && (
        <div
          className={`absolute top-0 z-20 pointer-events-none bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700/60 rounded-lg shadow-xl px-2.5 py-2 min-w-[96px] ${hoveredCssLeft > 60 ? '-translate-x-full' : ''}`}
          style={{ left: `${hoveredCssLeft}%` }}
        >
          <p className="text-[10px] font-black text-slate-700 dark:text-slate-200 mb-1 tabular-nums">{formatDateLabel(hovered.date)}</p>
          {hovered.link_loss > 0 && (
            <p className="text-[10px] font-semibold text-rose-600 dark:text-rose-400">{t('Link Loss')}: {hovered.link_loss}</p>
          )}
          {hovered.dying_gasp > 0 && (
            <p className="text-[10px] font-semibold text-blue-600 dark:text-blue-400">{t('Dying Gasp')}: {hovered.dying_gasp}</p>
          )}
          {hovered.unknown > 0 && (
            <p className="text-[10px] font-semibold text-violet-600 dark:text-violet-400">{t('Unknown')}: {hovered.unknown}</p>
          )}
          {hovered.link_loss === 0 && hovered.dying_gasp === 0 && hovered.unknown === 0 && (
            <p className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">—</p>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-3 mt-1.5 mb-2">
        {reasons.map((r) => (
          <div key={r} className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: colors[r], opacity: totals[r] > 0 ? 1 : 0.35 }} />
            <span className={`text-[10px] font-semibold ${totals[r] > 0 ? 'text-slate-500 dark:text-slate-400' : 'text-slate-400 dark:text-slate-500'}`}>{labels[r]}</span>
            <span className={`text-[10px] font-black tabular-nums ${totals[r] > 0 ? 'text-slate-600 dark:text-slate-300' : 'text-slate-400 dark:text-slate-500'}`}>{totals[r]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

const PowerChart = ({ data, lastNDays, t, mobile = false }) => {
  const [hoveredIdx, setHoveredIdx] = useState(null)

  const hasOnuRx = data.some((d) => d.onuRx != null)
  const hasOltRx = data.some((d) => d.oltRx != null)

  const width = 500
  const height = 200
  const padL = 44
  const padR = 12
  const padT = 8
  const padB = 36
  const chartW = width - padL - padR
  const chartH = height - padT - padB

  const allValues = data.flatMap((d) => [d.onuRx, d.oltRx]).filter((v) => v != null)
  const rawMin = allValues.length ? Math.min(...allValues) : -30
  const rawMax = allValues.length ? Math.max(...allValues) : -15
  const yMin = Math.floor(rawMin / 5) * 5
  const yMax = Math.ceil(rawMax / 5) * 5 || yMin + 5
  const yRange = yMax - yMin || 1

  const ySteps = []
  for (let v = yMin; v <= yMax; v += 5) ySteps.push(v)

  // Column-based x-axis — same geometry as DisconnectionChart
  const totalDays = lastNDays.length || 1
  const groupWidth = chartW / totalDays
  const xForDay = (i) => padL + groupWidth * i + groupWidth / 2

  // Day index lookup for mapping timestamps to columns
  const dayIdx = {}
  lastNDays.forEach((key, i) => { dayIdx[key] = i })

  const toX = (ts) => {
    const d = new Date(ts)
    const key = toDateKey(d)
    const col = dayIdx[key]
    if (col === undefined) return padL + chartW
    const dayStart = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
    const frac = (ts - dayStart) / 86400000
    return padL + groupWidth * col + frac * groupWidth
  }
  const toY = (v) => padT + chartH - ((v - yMin) / yRange) * chartH

  const onuColor = '#38bdf8'  // sky-400 — ONU Rx (light blue)
  const oltColor = '#1d4ed8'  // blue-700 — OLT Rx (dark blue)

  const hovered = hoveredIdx !== null ? data[hoveredIdx] : null
  const hoveredCssLeft = hoveredIdx !== null ? (toX(data[hoveredIdx].timestamp) / width) * 100 : 0

  const handleMouseMove = (e) => {
    if (data.length === 0) return
    const svg = e.currentTarget
    const rect = svg.getBoundingClientRect()
    const relX = (e.clientX - rect.left) / rect.width
    const vbX = relX * width
    let best = 0
    let bestDist = Math.abs(toX(data[0].timestamp) - vbX)
    for (let i = 1; i < data.length; i++) {
      const dist = Math.abs(toX(data[i].timestamp) - vbX)
      if (dist < bestDist) { best = i; bestDist = dist }
    }
    setHoveredIdx(best)
  }

  const dayStep = lastNDays.length > 20 ? 4 : lastNDays.length > 10 ? 2 : 1

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        preserveAspectRatio="xMidYMid meet"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoveredIdx(null)}
        style={{ cursor: 'crosshair' }}
      >
        {/* Grid lines */}
        {ySteps.map((v) => (
          <line key={v} x1={padL} x2={width - padR} y1={toY(v)} y2={toY(v)} stroke="currentColor" strokeWidth="0.5" className="text-slate-200 dark:text-slate-700/50" />
        ))}
        {/* Y-axis labels */}
        {ySteps.map((v) => (
          <text key={v} x={padL - 4} y={toY(v) + 4} textAnchor="end" className="fill-slate-500 dark:fill-slate-400" style={{ fontSize: mobile ? '11px' : '8px', fontWeight: mobile ? 700 : 600 }}>
            {v}
          </text>
        ))}

        {/* Vertical cursor */}
        {hoveredIdx !== null && (
          <line
            x1={toX(data[hoveredIdx].timestamp)} x2={toX(data[hoveredIdx].timestamp)}
            y1={padT} y2={padT + chartH}
            stroke="currentColor" strokeWidth="1" strokeDasharray="3,2"
            className="text-slate-400/60 dark:text-slate-500/60"
            pointerEvents="none"
          />
        )}

        {/* ONU Rx line + dots */}
        {hasOnuRx && (() => {
          const lines = []; let prev = null
          for (let i = 0; i < data.length; i++) {
            if (data[i].onuRx == null) { prev = null; continue }
            if (prev !== null) lines.push(
              <line key={`onu-ln-${i}`} x1={toX(prev.ts)} y1={toY(prev.v)} x2={toX(data[i].timestamp)} y2={toY(data[i].onuRx)}
                stroke={onuColor} strokeWidth="1.5" strokeLinecap="round" />
            )
            prev = { ts: data[i].timestamp, v: data[i].onuRx }
          }
          return lines
        })()}
        {hasOnuRx && data.map((d, i) => {
          if (d.onuRx == null) return null
          return <circle key={`onu-d-${i}`} cx={toX(d.timestamp)} cy={toY(d.onuRx)} r={hoveredIdx === i ? 3 : 2} fill={onuColor} />
        })}

        {/* OLT Rx line + dots */}
        {hasOltRx && (() => {
          const lines = []; let prev = null
          for (let i = 0; i < data.length; i++) {
            if (data[i].oltRx == null) { prev = null; continue }
            if (prev !== null) lines.push(
              <line key={`olt-ln-${i}`} x1={toX(prev.ts)} y1={toY(prev.v)} x2={toX(data[i].timestamp)} y2={toY(data[i].oltRx)}
                stroke={oltColor} strokeWidth="1.5" strokeLinecap="round" />
            )
            prev = { ts: data[i].timestamp, v: data[i].oltRx }
          }
          return lines
        })()}
        {hasOltRx && data.map((d, i) => {
          if (d.oltRx == null) return null
          return <circle key={`olt-d-${i}`} cx={toX(d.timestamp)} cy={toY(d.oltRx)} r={hoveredIdx === i ? 3 : 2} fill={oltColor} />
        })}

        {/* X-axis: dd/mm labels at column centers — matches DisconnectionChart */}
        {lastNDays.map((dateKey, i) => {
          if (i % dayStep !== 0) return null
          return (
            <text key={`day-${dateKey}`} x={xForDay(i)} y={height - 14} textAnchor="middle" className="fill-slate-500 dark:fill-slate-400" style={{ fontSize: mobile ? '10px' : '8px', fontWeight: mobile ? 700 : 600 }}>
              {formatDateLabel(dateKey)}
            </text>
          )
        })}
      </svg>

      {/* Hover tooltip */}
      {hovered !== null && (
        <div
          className={`absolute top-0 z-20 pointer-events-none bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700/60 rounded-lg shadow-xl px-2.5 py-2 min-w-[110px] ${hoveredCssLeft > 60 ? '-translate-x-full' : ''}`}
          style={{ left: `${hoveredCssLeft}%` }}
        >
          <p className="text-[10px] font-black text-slate-700 dark:text-slate-200 mb-1 tabular-nums">{formatTimestamp(new Date(hovered.timestamp).toISOString())}</p>
          {hovered.onuRx != null ? (
            <p className="text-[10px] font-semibold"><span className="text-slate-400 dark:text-slate-500">ONU Rx </span><span className="tabular-nums" style={{ color: onuColor }}>{hovered.onuRx.toFixed(2)}</span></p>
          ) : (
            <p className="text-[10px] font-semibold text-slate-300 dark:text-slate-600">ONU Rx —</p>
          )}
          {hovered.oltRx != null ? (
            <p className="text-[10px] font-semibold"><span className="text-slate-400 dark:text-slate-500">OLT Rx </span><span className="tabular-nums" style={{ color: oltColor }}>{hovered.oltRx.toFixed(2)}</span></p>
          ) : (
            <p className="text-[10px] font-semibold text-slate-300 dark:text-slate-600">OLT Rx —</p>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center justify-center gap-3 mt-1.5 mb-2">
        <div className="flex items-center gap-1.5">
          <svg width="16" height="8" viewBox="0 0 16 8" fill="none" className="shrink-0">
            <line x1="1" y1="4" x2="15" y2="4" stroke={onuColor} strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="8" cy="4" r="1.5" fill={onuColor} />
          </svg>
          <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400">ONU Rx</span>
        </div>
        <div className="flex items-center gap-1.5">
          <svg width="16" height="8" viewBox="0 0 16 8" fill="none" className="shrink-0">
            <line x1="1" y1="4" x2="15" y2="4" stroke={oltColor} strokeWidth="1.5" strokeLinecap="round" />
            <circle cx="8" cy="4" r="1.5" fill={oltColor} />
          </svg>
          <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400">OLT Rx</span>
        </div>
      </div>
    </div>
  )
}
