import React, { useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, X, AlertTriangle } from 'lucide-react'

import api from '../services/api'

const normalizeSearch = (value) => String(value || '').toLowerCase().trim()

const formatTimestamp = (value) => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
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
  const timestamp = Date.parse(point?.timestamp || '')
  if (!Number.isFinite(timestamp)) return null
  const onuRx = Number(point?.onu_rx_power)
  const oltRx = Number(point?.olt_rx_power)
  const value = Number.isFinite(onuRx) ? onuRx : (Number.isFinite(oltRx) ? oltRx : null)
  if (!Number.isFinite(value)) return null
  return {
    timestamp,
    value,
  }
}

export const AlarmHistory = () => {
  const { t } = useTranslation()
  const [searchTerm, setSearchTerm] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)
  const [selectedClient, setSelectedClient] = useState(null)
  const [suggestions, setSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [alarms, setAlarms] = useState([])
  const [powerHistory, setPowerHistory] = useState([])
  const [alarmStats, setAlarmStats] = useState({ dyingGasp: 0, linkLoss: 0, total: 0 })
  const searchContainerRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (searchContainerRef.current && !searchContainerRef.current.contains(event.target)) {
        setSearchFocused(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  useEffect(() => {
    if (!searchFocused || selectedClient) return

    const term = normalizeSearch(searchTerm)
    if (!term) {
      setSuggestions([])
      setSuggestionsLoading(false)
      return
    }

    const timeout = setTimeout(async () => {
      setSuggestionsLoading(true)
      try {
        const response = await api.get('/onu/alarm-clients/', {
          params: {
            search: term,
            limit: 7,
          }
        })
        setSuggestions(Array.isArray(response?.data?.results) ? response.data.results : [])
      } catch {
        setSuggestions([])
      } finally {
        setSuggestionsLoading(false)
      }
    }, 180)

    return () => clearTimeout(timeout)
  }, [searchFocused, searchTerm, selectedClient])

  useEffect(() => {
    if (!selectedClient?.id) {
      setAlarms([])
      setPowerHistory([])
      setAlarmStats({ dyingGasp: 0, linkLoss: 0, total: 0 })
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
            alarm_days: 30,
            power_days: 7,
            alarm_limit: 300,
            max_power_points: 240,
          },
        })
        if (cancelled) return

        const payload = response?.data || {}
        const nextAlarms = (payload.alarms || []).map(normalizeAlarm)
        const nextPower = (payload.power_history || []).map(normalizePowerPoint).filter(Boolean)

        const statsPayload = payload.stats || {}
        setAlarms(nextAlarms)
        setPowerHistory(nextPower)
        setAlarmStats({
          dyingGasp: Number(statsPayload.dying_gasp || 0),
          linkLoss: Number(statsPayload.link_loss || 0),
          total: Number(statsPayload.total || 0),
        })
      } catch {
        if (cancelled) return
        setAlarms([])
        setPowerHistory([])
        setAlarmStats({ dyingGasp: 0, linkLoss: 0, total: 0 })
        setDetailError(t('Failed to load OLT data'))
      } finally {
        if (!cancelled) setDetailLoading(false)
      }
    }

    loadDetail()
    return () => {
      cancelled = true
    }
  }, [selectedClient, t])

  const handleSelectSuggestion = (client) => {
    setSelectedClient(client)
    setSearchTerm(client.client_name || client.clientName || '')
    setSearchFocused(false)
  }

  const renderHighlightedText = (value, term) => {
    const source = String(value || '')
    const normalizedTerm = normalizeSearch(term)
    if (!normalizedTerm || !source) return source

    const lowerSource = source.toLowerCase()
    const parts = []
    let cursor = 0
    let key = 0

    while (cursor < source.length) {
      const matchIndex = lowerSource.indexOf(normalizedTerm, cursor)
      if (matchIndex === -1) {
        parts.push(<span key={`p-${key++}`}>{source.slice(cursor)}</span>)
        break
      }
      if (matchIndex > cursor) {
        parts.push(<span key={`p-${key++}`}>{source.slice(cursor, matchIndex)}</span>)
      }
      const matchEnd = matchIndex + normalizedTerm.length
      parts.push(
        <mark key={`m-${key++}`} className="px-[1px] rounded-sm bg-emerald-100 text-emerald-700 dark:bg-emerald-400/20 dark:text-emerald-300">
          {source.slice(matchIndex, matchEnd)}
        </mark>
      )
      cursor = matchEnd
    }

    return parts
  }

  const eventTypeStyle = (type) => {
    if (type === 'dying_gasp') return 'bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400'
    if (type === 'link_loss') return 'bg-rose-50 dark:bg-rose-500/10 text-rose-600 dark:text-rose-400'
    return 'bg-slate-100 dark:bg-slate-800/50 text-slate-600 dark:text-slate-400'
  }

  const eventTypeLabel = (type) => {
    if (type === 'dying_gasp') return t('Dying Gasp')
    if (type === 'link_loss') return t('Link Loss')
    return t('Unknown')
  }

  return (
    <div className="min-h-full bg-slate-100 dark:bg-slate-950">
      <div className="px-3 lg:px-8 py-6">
        <div ref={searchContainerRef} className="relative mb-6">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-300 dark:text-slate-500" />
          <input
            type="text"
            placeholder={t('Search client...')}
            value={searchTerm}
            onFocus={() => setSearchFocused(true)}
            onChange={(event) => {
              setSearchTerm(event.target.value)
              if (!event.target.value) setSelectedClient(null)
              else if (selectedClient) setSelectedClient(null)
            }}
            className="h-11 w-full bg-white dark:bg-slate-800 border border-slate-200/80 dark:border-slate-700 rounded-xl pl-11 pr-10 text-[12px] text-compact font-semibold text-slate-600 dark:text-slate-200 shadow-sm transition-all placeholder:text-slate-400/70 dark:placeholder:text-slate-500 focus:border-emerald-500/30 focus:ring-2 focus:ring-emerald-500/10 focus:outline-none"
          />
          {searchTerm && (
            <button
              type="button"
              onClick={() => {
                setSearchTerm('')
                setSelectedClient(null)
                setSuggestions([])
              }}
              className="absolute right-3 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}

          {searchFocused && normalizeSearch(searchTerm) && !selectedClient && (
            <div className="absolute left-0 top-[calc(100%+4px)] z-30 w-full p-2 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-xl max-h-[280px] overflow-y-auto">
              {suggestionsLoading && (
                <p className="px-2 py-2 text-[11px] font-semibold text-slate-400">{t('Loading live data')}</p>
              )}
              {!suggestionsLoading && suggestions.length === 0 && (
                <p className="px-2 py-2 text-[11px] font-semibold text-slate-400">{t('No clients found')}</p>
              )}
              {!suggestionsLoading && suggestions.map((suggestion) => (
                <button
                  key={suggestion.id}
                  type="button"
                  onClick={() => handleSelectSuggestion(suggestion)}
                  className="w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                >
                  <p className="text-[11px] font-black tracking-tight text-slate-800 dark:text-slate-100 whitespace-nowrap overflow-hidden text-ellipsis">
                    {renderHighlightedText(suggestion.client_name || `ONU ${suggestion.onu_number}`, searchTerm)}
                  </p>
                  <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap overflow-hidden text-ellipsis">
                    {renderHighlightedText(suggestion.serial || '—', searchTerm)}
                    <span className="ml-2 text-slate-400 dark:text-slate-500">{suggestion.olt_name} &middot; {t('Slot')} {suggestion.slot_id} &middot; PON {suggestion.pon_id}</span>
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>

        {!selectedClient && (
          <div className="flex flex-col items-center justify-center py-24">
            <AlertTriangle className="w-10 h-10 text-slate-300 dark:text-slate-600 mb-3" />
            <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('Select a client to view alarm history')}</p>
          </div>
        )}

        {selectedClient && (
          <>
            {detailError && (
              <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50/70 px-3 py-2 text-[11px] font-semibold text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
                {detailError}
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-6">
              <div className="rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm px-4 py-3">
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">{t('Dying Gasp events')}</p>
                <p className="text-2xl font-black tabular-nums text-blue-600 dark:text-blue-400">{alarmStats.dyingGasp}</p>
              </div>
              <div className="rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm px-4 py-3">
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">{t('Link Loss events')}</p>
                <p className="text-2xl font-black tabular-nums text-rose-600 dark:text-rose-400">{alarmStats.linkLoss}</p>
              </div>
              <div className="rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm px-4 py-3">
                <p className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 mb-1">{t('Total events')}</p>
                <p className="text-2xl font-black tabular-nums text-slate-700 dark:text-slate-200">{alarmStats.total}</p>
              </div>
            </div>

            <div className="rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm p-4 mb-6">
              <p className="text-[12px] font-black uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-3">{t('Power history (7 days)')}</p>
              {detailLoading && powerHistory.length === 0 ? (
                <p className="text-[11px] font-semibold text-slate-400">{t('Loading live data')}</p>
              ) : (
                <PowerHistoryChart points={powerHistory} t={t} />
              )}
            </div>

            <div className="hidden lg:flex flex-col w-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
              <div className="shrink-0 overflow-hidden bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col style={{ width: '18%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '18%' }} />
                    <col style={{ width: '20%' }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Event Type')}</th>
                      <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Start')}</th>
                      <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('End')}</th>
                      <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('Duration')}</th>
                      <th className="px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-center">{t('Status')}</th>
                    </tr>
                  </thead>
                </table>
              </div>

              <div className="overflow-y-auto min-h-0 custom-scrollbar">
                <table className="w-full table-fixed text-left border-collapse">
                  <colgroup>
                    <col style={{ width: '18%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '22%' }} />
                    <col style={{ width: '18%' }} />
                    <col style={{ width: '20%' }} />
                  </colgroup>
                  <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                    {!detailLoading && alarms.length === 0 && (
                      <tr>
                        <td colSpan={5} className="px-4 py-12 text-center">
                          <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('No alarm data available')}</p>
                        </td>
                      </tr>
                    )}
                    {alarms.map((alarm) => (
                      <tr key={alarm.id} className="h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/50">
                        <td className="px-2.5 py-0 align-middle">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${eventTypeStyle(alarm.type)}`}>
                            {eventTypeLabel(alarm.type)}
                          </span>
                        </td>
                        <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums">{formatTimestamp(alarm.start)}</td>
                        <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums">{formatTimestamp(alarm.end)}</td>
                        <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums text-center">{formatDuration(alarm.start, alarm.end)}</td>
                        <td className="px-2.5 py-0 align-middle text-center">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${
                            alarm.status === 'active'
                              ? 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400'
                              : 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          }`}>
                            {alarm.status === 'active' ? t('Active') : t('Resolved')}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex lg:hidden flex-col w-full rounded-xl border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
              <div className="overflow-y-auto min-h-0 custom-scrollbar p-2 space-y-1.5">
                {!detailLoading && alarms.length === 0 && (
                  <div className="py-12 text-center">
                    <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">{t('No alarm data available')}</p>
                  </div>
                )}

                {alarms.map((alarm) => (
                  <div key={alarm.id} className="rounded-md border border-slate-200/70 dark:border-slate-700/50 bg-white dark:bg-slate-900 px-3 py-2 flex items-center gap-2">
                    <div className="min-w-0 flex-1 flex flex-col gap-0.5">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase self-start ${eventTypeStyle(alarm.type)}`}>
                        {eventTypeLabel(alarm.type)}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums">
                        {formatTimestamp(alarm.start)} → {formatTimestamp(alarm.end)}
                      </span>
                    </div>
                    <div className="shrink-0 flex flex-col items-end gap-0.5">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${
                        alarm.status === 'active'
                          ? 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400'
                          : 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                      }`}>
                        {alarm.status === 'active' ? t('Active') : t('Resolved')}
                      </span>
                      <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">
                        {formatDuration(alarm.start, alarm.end)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const PowerHistoryChart = ({ points, t }) => {
  if (!points.length) return null

  const width = 600
  const height = 200
  const padL = 50
  const padR = 16
  const padT = 16
  const padB = 32
  const chartW = width - padL - padR
  const chartH = height - padT - padB

  const values = points.map((point) => point.value)
  const minVal = Math.floor(Math.min(...values) - 1)
  const maxVal = Math.ceil(Math.max(...values) + 1)
  const timeMin = points[0].timestamp
  const timeMax = points[points.length - 1].timestamp
  const timeRange = timeMax - timeMin || 1
  const valRange = maxVal - minVal || 1

  const toX = (timestamp) => padL + ((timestamp - timeMin) / timeRange) * chartW
  const toY = (value) => padT + chartH - ((value - minVal) / valRange) * chartH

  const polylinePoints = points.map((point) => `${toX(point.timestamp)},${toY(point.value)}`).join(' ')

  const goodY = toY(Math.min(maxVal, -25))
  const warnY = toY(Math.max(minVal, -28))
  const critY = toY(minVal)

  const yLabels = []
  const step = Math.max(1, Math.ceil(valRange / 4))
  for (let value = minVal; value <= maxVal; value += step) {
    yLabels.push(value)
  }

  const xLabels = []
  const dayMs = 86400000
  const startDay = new Date(timeMin)
  startDay.setHours(0, 0, 0, 0)
  for (let timestamp = startDay.getTime(); timestamp <= timeMax; timestamp += dayMs * 2) {
    if (timestamp >= timeMin) {
      const day = new Date(timestamp)
      xLabels.push({ timestamp, label: `${day.getDate()}/${day.getMonth() + 1}` })
    }
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      {goodY > padT && (
        <rect x={padL} y={padT} width={chartW} height={Math.max(0, goodY - padT)} fill="currentColor" className="text-emerald-50 dark:text-emerald-500/5" />
      )}
      {warnY > goodY && (
        <rect x={padL} y={goodY} width={chartW} height={Math.max(0, warnY - goodY)} fill="currentColor" className="text-amber-50 dark:text-amber-500/5" />
      )}
      {critY > warnY && (
        <rect x={padL} y={warnY} width={chartW} height={Math.max(0, critY - warnY)} fill="currentColor" className="text-rose-50 dark:text-rose-500/5" />
      )}

      {yLabels.map((value) => (
        <line key={value} x1={padL} x2={width - padR} y1={toY(value)} y2={toY(value)} stroke="currentColor" strokeWidth="0.5" className="text-slate-200 dark:text-slate-700/50" />
      ))}

      <line x1={padL} x2={width - padR} y1={toY(-25)} y2={toY(-25)} stroke="currentColor" strokeWidth="1" strokeDasharray="4 2" className="text-emerald-400 dark:text-emerald-600" />
      <line x1={padL} x2={width - padR} y1={toY(-28)} y2={toY(-28)} stroke="currentColor" strokeWidth="1" strokeDasharray="4 2" className="text-rose-400 dark:text-rose-600" />

      <polyline
        points={polylinePoints}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        className="text-emerald-500 dark:text-emerald-400"
      />

      {points.map((point, index) => (
        <circle key={index} cx={toX(point.timestamp)} cy={toY(point.value)} r="2.5" fill="currentColor" className="text-emerald-500 dark:text-emerald-400" />
      ))}

      {yLabels.map((value) => (
        <text key={value} x={padL - 6} y={toY(value) + 3} textAnchor="end" className="fill-slate-400 dark:fill-slate-500" style={{ fontSize: '9px', fontWeight: 600 }}>
          {value}
        </text>
      ))}

      {xLabels.map(({ timestamp, label }) => (
        <text key={timestamp} x={toX(timestamp)} y={height - 6} textAnchor="middle" className="fill-slate-400 dark:fill-slate-500" style={{ fontSize: '9px', fontWeight: 600 }}>
          {label}
        </text>
      ))}

      <text x={4} y={padT + 4} textAnchor="start" className="fill-slate-400 dark:fill-slate-500" style={{ fontSize: '9px', fontWeight: 700 }}>
        {t('dBm')}
      </text>
    </svg>
  )
}
