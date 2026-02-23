import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Server, Cable, Search, Filter, CircuitBoard, Bell, X, Check, Minus, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getOnuStats } from '../utils/stats'
import { HEALTH_STYLES, resolveHealthStyle } from '../utils/healthStyles'

const pad2 = (value) => String(value).padStart(2, '0')
const asCount = (value) => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : 0
}
const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))
const isActiveEntity = (entity) => Boolean(entity) && entity.is_active !== false

const normalizeSearch = (value) => String(value || '').toLowerCase().trim()

const onuMatchesSearchTerm = (onu, term) => {
  const normalizedTerm = normalizeSearch(term)
  if (!normalizedTerm) return false
  const onuLogin = normalizeSearch(onu?.client_name || onu?.name || '')
  const onuSerial = normalizeSearch(onu?.serial || onu?.serial_number || '')
  return onuLogin.includes(normalizedTerm) || onuSerial.includes(normalizedTerm)
}

const scoreSearchMatch = (rawValue, term) => {
  const value = normalizeSearch(rawValue)
  if (!value || !term) return -1
  if (value === term) return 1000
  if (value.startsWith(term)) return 700
  const index = value.indexOf(term)
  if (index === -1) return -1
  return Math.max(200 - index, 1)
}

const SEARCH_STATUS_WEIGHT = {
  online: 3,
  offline: 2,
  unknown: 1
}

const getSearchStatusWeight = (status) => SEARCH_STATUS_WEIGHT[normalizeSearch(status)] || 0

const toEpochMillis = (value) => {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

const shouldReplaceSearchSuggestion = (current, candidate) => {
  if (!current) return true
  if (candidate.score !== current.score) return candidate.score > current.score

  const candidateStatusWeight = getSearchStatusWeight(candidate.status)
  const currentStatusWeight = getSearchStatusWeight(current.status)
  if (candidateStatusWeight !== currentStatusWeight) return candidateStatusWeight > currentStatusWeight

  if (candidate.powerReadAtMs !== current.powerReadAtMs) return candidate.powerReadAtMs > current.powerReadAtMs
  return candidate.key < current.key
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
      parts.push(
        <span key={`plain-${key++}`}>
          {source.slice(cursor)}
        </span>
      )
      break
    }

    if (matchIndex > cursor) {
      parts.push(
        <span key={`plain-${key++}`}>
          {source.slice(cursor, matchIndex)}
        </span>
      )
    }

    const matchEnd = matchIndex + normalizedTerm.length
    parts.push(
      <mark
        key={`match-${key++}`}
        className="px-[1px] rounded-sm bg-emerald-100 text-emerald-700 dark:bg-emerald-400/20 dark:text-emerald-300"
      >
        {source.slice(matchIndex, matchEnd)}
      </mark>
    )
    cursor = matchEnd
  }

  return parts
}

const NODE_CARD_STYLE = {
  // Keep all hierarchy levels visually coherent.
  pon: 'w-[180px] h-[56px] rounded-xl',
  olt: 'w-[180px] h-[56px] rounded-xl',
  slot: 'w-[180px] h-[56px] rounded-xl'
}

const resolveNodeHealthStyle = resolveHealthStyle

const ALARM_REASON_STAT_KEY = {
  linkLoss: 'linkLoss',
  dyingGasp: 'dyingGasp',
  unknown: 'unknown'
}

const getSelectedOfflineCount = (stats, selectedReasons = []) => {
     return selectedReasons.reduce((acc, reason) => {
       const key = ALARM_REASON_STAT_KEY[reason]
       return acc + (asCount(stats?.[key]) || 0)
     }, 0)
}

const getPonHealthState = (pon, selectedReasons, minCount) => {
  const stats = pon?.stats || getOnuStats(pon?.onus || [])
  if (!pon || pon?.is_active === false) {
    return {
      state: 'green',
      stats,
      selectedOfflineCount: 0
    }
  }

  const total = asCount(stats.total)
  const online = asCount(stats.online)
  const totalOffline = asCount(stats.offline)
  const offlineCount = getSelectedOfflineCount(stats, selectedReasons)
  const isRed = total > 0 && online === 0 && totalOffline > 0
  const isYellow = !isRed && totalOffline > 0

  return {
    state: isRed ? 'red' : isYellow ? 'yellow' : 'green',
    stats,
    selectedOfflineCount: offlineCount
  }
}

const getSlotHealthState = (slot, selectedReasons, minCount) => {
  const activePons = asList(slot?.pons).filter(isActiveEntity)
  if (!activePons.length) return 'green'

  const ponStates = activePons.map((pon) => getPonHealthState(pon, selectedReasons, minCount).state)
  const redPonCount = ponStates.reduce((count, state) => (
    state === 'red' ? count + 1 : count
  ), 0)

  if (redPonCount === ponStates.length) return 'red'
  if (redPonCount > 0) return 'yellow'
  return 'green'
}

const getOltHealthState = (olt, selectedReasons, minCount) => {
  const activeSlots = asList(olt?.slots).filter(isActiveEntity)
  // No topology data at all → SNMP likely unreachable → gray
  if (!activeSlots.length && !asList(olt?.slots).length) return 'gray'
  if (!activeSlots.length) return 'green'

  const slotStates = activeSlots.map((slot) => getSlotHealthState(slot, selectedReasons, minCount))
  const redSlotCount = slotStates.reduce((count, state) => (
    state === 'red' ? count + 1 : count
  ), 0)
  const totalSlotCount = slotStates.length

  if (redSlotCount === slotStates.length) return 'red'
  if (redSlotCount > 0 && redSlotCount < totalSlotCount) return 'yellow'
  return 'green'
}


const NetworkNode = ({ type, label, isOpen, onToggle, active, children, stats, sublabel, healthState = 'green' }) => {
  const isVisualActive = type === 'pon' ? active : isOpen
  const healthStyle = resolveNodeHealthStyle(healthState)

  const icons = {
    olt: Server,
    slot: CircuitBoard,
    pon: Cable
  }
  const Icon = icons[type]

  const cardStyle = NODE_CARD_STYLE[type] || NODE_CARD_STYLE.pon

  return (
    <div className="flex flex-col relative">
      <div
        onClick={onToggle}
        className={`
          relative flex items-center gap-1.5 px-2.5 py-1.5 bg-white dark:bg-slate-900 border transition-all duration-300 cursor-pointer group/node shrink-0
          ${cardStyle}
          ${isVisualActive
            ? healthStyle.borderActive
            : healthStyle.borderIdle}
        `}
      >
        <div
          className={`
            absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full transition-all duration-300
            ${isVisualActive ? healthStyle.accentActive : healthStyle.accentIdle}
          `}
        />

        <div
          className={`
            flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-300
            ${isVisualActive
              ? healthStyle.iconActive
              : healthStyle.iconIdle}
          `}
        >
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0 flex flex-col justify-center">
          <p
          className={`text-[11px] font-black uppercase tracking-tight leading-none mb-0.5 transition-colors whitespace-nowrap overflow-hidden text-ellipsis ${
            isVisualActive ? healthStyle.labelActive : 'text-slate-900 dark:text-white'
          }`}
        >
          {label}
        </p>

          {stats ? (
            <div className="mt-0.5 flex items-center gap-2.5 w-full pr-0.5">
              {asCount(stats.online) > 0 && <StatusItem color="bg-emerald-500" count={asCount(stats.online)} />}
              {asCount(stats.linkLoss) > 0 && <StatusItem color="bg-rose-500" count={asCount(stats.linkLoss)} />}
              {asCount(stats.dyingGasp) > 0 && <StatusItem color="bg-blue-500" count={asCount(stats.dyingGasp)} />}
              {asCount(stats.unknown) > 0 && <StatusItem color="bg-purple-500" count={asCount(stats.unknown)} />}
            </div>
          ) : (
            <p className="mt-0.5 text-[9px] font-black text-slate-400 uppercase tracking-widest opacity-80 whitespace-nowrap overflow-hidden text-ellipsis">{sublabel}</p>
          )}
        </div>

        {(type === 'olt' || type === 'slot') && (
          <div className={`transition-transform duration-300 ${isOpen ? `rotate-180 ${healthStyle.chevronOpen}` : 'text-slate-300 group-hover/node:text-slate-400'}`}>
            <ChevronDown className="w-3 h-3" />
          </div>
        )}
      </div>

      {isOpen && children && (
        <div className="relative mt-2.5 ml-4 pl-8 border-l-[1.5px] border-slate-100 dark:border-slate-700/50 flex flex-col gap-2.5 animate-in slide-in-from-top-2 duration-300">
          {children}
        </div>
      )}
    </div>
  )
}

const StatusItem = ({ color, count }) => (
  <div className="flex items-center gap-1 min-w-0">
    <div className={`w-2 h-2 rounded-full shrink-0 ${color} shadow-sm shadow-current/20`} />
    <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200 tabular-nums leading-none">{count}</span>
  </div>
)

export const NetworkTopology = ({
  olts,
  loading,
  error,
  selectedPonId,
  selectedSearchMatch,
  onPonSelect,
  onSearchMatchSelect,
  onAlarmModeChange,
  snmpStatus = {},
  oltHealthById = {}
}) => {
  const { t } = useTranslation()
  const [searchTerm, setSearchTerm] = useState(() => selectedSearchMatch?.searchTerm || '')
  const [searchFocused, setSearchFocused] = useState(false)
  const [openNodes, setOpenNodes] = useState(() => {
    if (!selectedSearchMatch?.oltId) return {}
    const initial = { [`olt-${selectedSearchMatch.oltId}`]: true }
    if (selectedSearchMatch.slotId) initial[`slot-${selectedSearchMatch.slotId}`] = true
    return initial
  })
  const [oltFilterOpen, setOltFilterOpen] = useState(false)
  const [selectedOltIds, setSelectedOltIds] = useState([])
  const [alarmMenuOpen, setAlarmMenuOpen] = useState(false)
  const [alarmEnabled, setAlarmEnabled] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('varuna.alarmConfig'))
      return saved?.enabled ?? true
    } catch { return true }
  })
  const [alarmMinCountInput, setAlarmMinCountInput] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('varuna.alarmConfig'))
      return saved?.minCount != null ? String(saved.minCount) : '4'
    } catch { return '4' }
  })
  const [alarmReasons, setAlarmReasons] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('varuna.alarmConfig'))
      if (saved?.reasons && typeof saved.reasons === 'object') return saved.reasons
    } catch { /* use defaults */ }
    return { linkLoss: true, dyingGasp: false, unknown: false }
  })
  const searchContainerRef = useRef(null)
  const oltFilterContainerRef = useRef(null)
  const alarmMenuContainerRef = useRef(null)
  const oltFilterInitializedRef = useRef(false)
  const normalizedSearchTerm = normalizeSearch(searchTerm)

  useEffect(() => {
    const allIds = olts.map((olt) => String(olt.id))
    setSelectedOltIds((prev) => {
      if (!oltFilterInitializedRef.current) {
        if (!allIds.length) return prev
        oltFilterInitializedRef.current = true
        return allIds
      }
      return prev.filter((id) => allIds.includes(id))
    })
  }, [olts])

  useEffect(() => {
    if (!olts.length) return
    setOpenNodes((prev) => {
      if (Object.keys(prev).length) return prev
      const firstOlt = olts[0]
      const firstSlot = asList(firstOlt?.slots)[0]
      const initial = {}
      if (firstOlt?.id) {
        initial[`olt-${firstOlt.id}`] = true
      }
      if (firstSlot?.id) {
        initial[`slot-${firstSlot.id}`] = true
      }
      return initial
    })
  }, [olts])

  useEffect(() => {
    const handlePointerDown = (event) => {
      const target = event.target
      if (searchContainerRef.current && !searchContainerRef.current.contains(target)) {
        setSearchFocused(false)
      }
      if (oltFilterContainerRef.current && !oltFilterContainerRef.current.contains(target)) {
        setOltFilterOpen(false)
      }
      if (alarmMenuContainerRef.current && !alarmMenuContainerRef.current.contains(target)) {
        setAlarmMenuOpen(false)
      }
    }

    const handleKeyDown = (event) => {
      if (event.key !== 'Escape') return
      setSearchFocused(false)
      setOltFilterOpen(false)
      setAlarmMenuOpen(false)
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [])

  const toggleNode = (id) => {
    setOpenNodes((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const collapseAllNodes = () => {
    setOpenNodes({})
  }

  const activeAlarmReasons = useMemo(
    () => Object.entries(alarmReasons).filter(([, enabled]) => enabled).map(([reason]) => reason),
    [alarmReasons]
  )

  const clampAlarmMinCount = (value) => {
    const numeric = Number(value)
    if (!Number.isFinite(numeric)) return 1
    return Math.min(128, Math.max(1, Math.trunc(numeric)))
  }

  const effectiveAlarmMinCount = useMemo(() => {
    return clampAlarmMinCount(alarmMinCountInput)
  }, [alarmMinCountInput])

  useEffect(() => {
    onAlarmModeChange?.({
      enabled: alarmEnabled,
      reasons: activeAlarmReasons,
      minCount: effectiveAlarmMinCount
    })
  }, [alarmEnabled, activeAlarmReasons, effectiveAlarmMinCount, onAlarmModeChange])

  useEffect(() => {
    try {
      localStorage.setItem('varuna.alarmConfig', JSON.stringify({
        enabled: alarmEnabled,
        reasons: alarmReasons,
        minCount: effectiveAlarmMinCount
      }))
    } catch { /* storage full or unavailable */ }
  }, [alarmEnabled, alarmReasons, effectiveAlarmMinCount])

  const toggleAlarmReason = (reasonKey) => {
    setAlarmReasons((prev) => {
      const selectedCount = Object.values(prev).filter(Boolean).length
      if (prev[reasonKey] && selectedCount === 1) {
        return prev
      }
      return { ...prev, [reasonKey]: !prev[reasonKey] }
    })
  }

  const stepAlarmMinCount = (delta) => {
    setAlarmMinCountInput((prev) => {
      const base = Number(prev)
      const safeBase = Number.isFinite(base) ? base : 1
      return String(clampAlarmMinCount(safeBase + delta))
    })
  }

  const passesAlarmFilter = (pon) => {
    if (!alarmEnabled) return true
    if (pon?.is_active === false) return false
    const { selectedOfflineCount } = getPonHealthState(pon, activeAlarmReasons, effectiveAlarmMinCount)
    return selectedOfflineCount >= effectiveAlarmMinCount
  }

  const searchSuggestions = useMemo(() => {
    if (!normalizedSearchTerm) return []

    const dedupedSuggestions = new Map()
    olts.forEach((olt) => {
      asList(olt?.slots).forEach((slot) => {
        asList(slot?.pons).forEach((pon) => {
          asList(pon?.onus).forEach((onu) => {
            const clientName = onu?.client_name || onu?.name || ''
            const serial = onu?.serial || onu?.serial_number || ''
            const rawOnuId = asCount(onu?.onu_number ?? onu?.onu_id)
            const onuId = rawOnuId >= 1 && rawOnuId <= 128 ? rawOnuId : '-'
            const loginScore = scoreSearchMatch(clientName, normalizedSearchTerm)
            const serialScore = scoreSearchMatch(serial, normalizedSearchTerm)
            const bestScore = Math.max(loginScore, serialScore)
            if (bestScore < 0) return

            const slotNumber = slot.slot_number ?? slot.slot_id ?? slot.id
            const ponNumber = pon.pon_number ?? pon.pon_id ?? pon.id
            const key = `${olt.id}-${slot.id}-${pon.id}-${onu?.id || serial || clientName}`
            const normalizedSerial = normalizeSearch(serial)
            const dedupeKey = normalizedSerial || `path:${key}`
            const suggestion = {
              key,
              clientName: clientName || `ONU ${onuId}`,
              serial: serial || '-',
              oltId: olt.id,
              oltName: olt.name,
              slotId: slot.id,
              slotNumber,
              ponId: pon.id,
              ponNumber,
              onuId,
              status: onu?.status,
              powerReadAt: onu?.power_read_at,
              powerReadAtMs: toEpochMillis(onu?.power_read_at),
              matchType: serialScore > loginScore ? 'serial' : 'login',
              score: bestScore + (serialScore > loginScore ? 10 : 0),
            }

            const current = dedupedSuggestions.get(dedupeKey)
            if (shouldReplaceSearchSuggestion(current, suggestion)) {
              dedupedSuggestions.set(dedupeKey, suggestion)
            }
          })
        })
      })
    })

    return Array.from(dedupedSuggestions.values())
      .sort((a, b) => b.score - a.score || a.clientName.localeCompare(b.clientName) || a.serial.localeCompare(b.serial))
      .slice(0, 7)
  }, [olts, normalizedSearchTerm])

  const handleSearchSuggestionSelect = (suggestion) => {
    setSearchTerm(suggestion.matchType === 'serial' ? suggestion.serial : suggestion.clientName)
    setSearchFocused(false)
    setSelectedOltIds((prev) => (prev.includes(String(suggestion.oltId)) ? prev : [...prev, String(suggestion.oltId)]))
    setOpenNodes((prev) => ({
      ...prev,
      [`olt-${suggestion.oltId}`]: true,
      [`slot-${suggestion.slotId}`]: true,
    }))
    onSearchMatchSelect?.({
      ponId: suggestion.ponId,
      onuId: suggestion.onuId,
      serial: suggestion.serial,
      clientName: suggestion.clientName,
      oltId: suggestion.oltId,
      slotId: suggestion.slotId,
      searchTerm: suggestion.matchType === 'serial' ? suggestion.serial : suggestion.clientName,
    })
    onPonSelect(suggestion.ponId, { force: true })
  }

  const searchedOlts = useMemo(() => {
    const oltVisible = olts.filter((olt) => selectedOltIds.includes(String(olt.id)))
    const term = normalizedSearchTerm
    if (!term) return oltVisible

    const hasPinnedSearchPath = Boolean(
      selectedSearchMatch?.ponId &&
      selectedSearchMatch?.oltId &&
      normalizeSearch(selectedSearchMatch?.searchTerm) === term
    )
    const hasPinnedSlotId = selectedSearchMatch?.slotId != null
    return oltVisible
      .map((olt) => {
        const isPinnedOlt = hasPinnedSearchPath && String(olt.id) === String(selectedSearchMatch.oltId)
        if (hasPinnedSearchPath && !isPinnedOlt) {
          return {
            ...olt,
            slots: [],
            slot_count: 0,
          }
        }

        const slots = asList(olt?.slots)
          .filter(isActiveEntity)
          .map((slot) => {
            const isPinnedSlot = hasPinnedSearchPath
              ? (!hasPinnedSlotId || String(slot?.id) === String(selectedSearchMatch?.slotId))
              : true
            if (!isPinnedSlot) {
              return {
                ...slot,
                pons: [],
                pon_count: 0,
              }
            }

            const pons = asList(slot?.pons)
              .filter(isActiveEntity)
              .filter((pon) => {
                const matchesSearch = asList(pon?.onus).some((onu) => onuMatchesSearchTerm(onu, term))
                if (!matchesSearch) return false
                if (!hasPinnedSearchPath) return true
                return String(pon?.id) === String(selectedSearchMatch?.ponId)
              })
            return {
              ...slot,
              pons,
              pon_count: pons.length,
            }
          })
          .filter((slot) => asList(slot?.pons).length > 0)

        return {
          ...olt,
          slots,
          slot_count: slots.length,
        }
      })
      .filter((olt) => asList(olt?.slots).length > 0)
  }, [olts, selectedOltIds, normalizedSearchTerm, selectedSearchMatch])

  const filteredOlts = useMemo(() => {
    if (normalizedSearchTerm) return searchedOlts
    if (!alarmEnabled) return searchedOlts

    return searchedOlts
      .map((olt) => {
        const slots = asList(olt?.slots)
          .filter(isActiveEntity)
          .map((slot) => {
            const pons = asList(slot?.pons).filter(isActiveEntity).filter((pon) => passesAlarmFilter(pon))
            return {
              ...slot,
              pons,
              pon_count: pons.length,
            }
          })
          .filter((slot) => asList(slot?.pons).length > 0)

        return {
          ...olt,
          slots,
          slot_count: slots.length,
        }
      })
      .filter((olt) => asList(olt?.slots).length > 0)
  }, [searchedOlts, alarmEnabled, normalizedSearchTerm, effectiveAlarmMinCount, activeAlarmReasons])

  const searchedOltMap = useMemo(() => {
    return new Map(searchedOlts.map((olt) => [String(olt.id), olt]))
  }, [searchedOlts])

  useEffect(() => {
    if (!alarmEnabled) return
    setOpenNodes((prev) => {
      const next = { ...prev }
      filteredOlts.forEach((olt) => {
        next[`olt-${olt.id}`] = true
        asList(olt?.slots).filter(Boolean).forEach((slot) => {
          if (!slot?.id) return
          next[`slot-${slot.id}`] = true
        })
      })
      return next
    })
  }, [alarmEnabled, filteredOlts])

  const renderOlt = (olt) => {
    const sourceOlt = searchedOltMap.get(String(olt.id)) || olt
    const oltId = `olt-${olt.id}`
    const derivedHealth = oltHealthById?.[String(olt.id)] || oltHealthById?.[olt.id]
    const snmpSt = snmpStatus?.[olt.id]
    const fallbackUnreachable = snmpSt?.status === 'unreachable'
    const oltHealthState = derivedHealth?.state || (fallbackUnreachable ? 'gray' : getOltHealthState(sourceOlt, activeAlarmReasons, effectiveAlarmMinCount))
    const isGrayTree = oltHealthState === 'gray'
    const slotCount = olt.slot_count ?? olt.slots?.length ?? 0
    const sourceSlotMap = new Map(
      asList(sourceOlt?.slots)
        .filter(Boolean)
        .map((slot) => [String(slot.id), slot])
    )
    return (
      <div key={oltId} className="flex-shrink-0">
        <NetworkNode
          type="olt"
          label={olt.name}
          sublabel={`${slotCount} ${t('SLOTS')}`}
          isOpen={openNodes[oltId]}
          onToggle={() => toggleNode(oltId)}
          healthState={oltHealthState}
        >
          {asList(olt?.slots)
            .filter(isActiveEntity)
            .map((slot) => {
              const slotId = `slot-${slot.id}`
              const sourceSlot = sourceSlotMap.get(String(slot.id)) || slot
              const sourcePonMap = new Map(
                asList(sourceSlot?.pons)
                  .filter(Boolean)
                  .map((pon) => [String(pon.id), pon])
              )
              const slotHealthState = isGrayTree ? 'gray' : getSlotHealthState(sourceSlot, activeAlarmReasons, effectiveAlarmMinCount)
              const ponCount = slot.pon_count ?? slot.pons?.length ?? 0
              const slotNumber = slot.slot_number ?? slot.slot_id ?? slot.id
              return (
                <NetworkNode
                  key={slotId}
                  type="slot"
                  label={`${t('SLOT')} ${pad2(slotNumber)}`}
                  sublabel={`${ponCount} ${t('PONS')}`}
                  isOpen={openNodes[slotId]}
                  onToggle={() => toggleNode(slotId)}
                  healthState={slotHealthState}
                >
                  {asList(slot?.pons)
                    .filter(isActiveEntity)
                    .map((pon) => {
                      const sourcePon = sourcePonMap.get(String(pon.id)) || pon
                      const ponHealth = isGrayTree
                        ? { state: 'gray', stats: { online: 0, dyingGasp: 0, linkLoss: 0, unknown: 0 }, selectedOfflineCount: 0 }
                        : getPonHealthState(sourcePon, activeAlarmReasons, effectiveAlarmMinCount)
                      const stats = ponHealth.stats
                      const ponId = pon.id
                      const ponNumber = pon.pon_number ?? pon.pon_id ?? pon.id
                      return (
                        <NetworkNode
                          key={ponId}
                          type="pon"
                          label={`PON ${pad2(ponNumber)}`}
                          stats={{
                            online: stats.online,
                            dyingGasp: stats.dyingGasp,
                            linkLoss: stats.linkLoss,
                            unknown: stats.unknown
                          }}
                          active={String(selectedPonId) === String(ponId)}
                          onToggle={() => onPonSelect(ponId)}
                          healthState={ponHealth.state}
                        />
                      )
                    })}
                </NetworkNode>
              )
            })}
        </NetworkNode>
      </div>
    )
  }

  return (
    <div className="flex flex-col w-full h-full pt-8">
      <div className="flex items-center gap-1.5 lg:gap-2 mb-8 px-4 lg:px-10">
        <div ref={oltFilterContainerRef} className="relative shrink-0">
          <button
            title={t('Filter OLTs')}
            onClick={() => {
              setOltFilterOpen((prev) => !prev)
              setAlarmMenuOpen(false)
            }}
            className={`h-9 w-9 flex items-center justify-center border rounded-xl shadow-sm transition-all ${
              selectedOltIds.length < olts.length
                ? 'bg-emerald-50 border-emerald-300 text-emerald-700'
                : 'bg-slate-50 dark:bg-slate-900 border-slate-200/70 dark:border-slate-700/50 text-slate-400 hover:text-emerald-600'
            }`}
          >
            <Filter className="w-3.5 h-3.5" />
          </button>

          {oltFilterOpen && (
            <div className="absolute left-0 top-11 z-30 w-[272px] p-3 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-xl">
              <div className="flex items-center justify-between mb-3">
                <p className="text-[11px] font-black uppercase tracking-wider text-slate-700 dark:text-slate-200">{t('Filter OLTs')}</p>
                <button
                  onClick={() => setOltFilterOpen(false)}
                  className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
                  aria-label={t('Close')}
                  title={t('Close')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <div className="flex items-center gap-1.5 mb-2.5">
                <button
                  onClick={() => setSelectedOltIds(olts.map((olt) => String(olt.id)))}
                  className="px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-[9px] font-black uppercase tracking-wide text-slate-600 dark:text-slate-300"
                >
                  {t('All')}
                </button>
                <button
                  onClick={() => setSelectedOltIds([])}
                  className="px-2 py-0.5 rounded-md bg-slate-100 dark:bg-slate-800 text-[9px] font-black uppercase tracking-wide text-slate-600 dark:text-slate-300"
                >
                  {t('Clear')}
                </button>
              </div>

              <div className="max-h-[220px] overflow-auto space-y-1.5 pr-1">
                {olts.map((olt) => {
                  const isChecked = selectedOltIds.includes(String(olt.id))
                  return (
                    <label
                      key={olt.id}
                      className={`
                        flex items-center gap-2.5 px-2.5 py-2 rounded-lg cursor-pointer transition-colors
                        ${isChecked
                          ? 'bg-slate-50/70 dark:bg-slate-800/40'
                          : 'hover:bg-slate-50 dark:hover:bg-slate-800/30'}
                      `}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={(e) => {
                          setSelectedOltIds((prev) => {
                            const id = String(olt.id)
                            if (e.target.checked) {
                              return prev.includes(id) ? prev : [...prev, id]
                            }
                            return prev.filter((currentId) => currentId !== id)
                          })
                        }}
                        className="sr-only"
                      />
                      <span className="h-4 w-4 flex items-center justify-center">
                        {isChecked ? (
                          <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-500" strokeWidth={3} />
                        ) : (
                          <span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" />
                        )}
                      </span>
                      <span className="truncate text-[12px] font-semibold text-slate-700 dark:text-slate-200">
                        {olt.name}
                      </span>
                    </label>
                  )
                })}
              </div>

              <p className="mt-2.5 text-[10px] font-semibold text-slate-400 dark:text-slate-500 leading-snug">
                {t('Filter OLTs help')}
              </p>
            </div>
          )}
        </div>

        <div ref={searchContainerRef} className="relative flex-1 min-w-0 max-w-[260px] lg:max-w-[268px]">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-300" />
          <input
            type="text"
            placeholder={t('Search')}
            value={searchTerm}
            onFocus={() => setSearchFocused(true)}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="h-9 w-full bg-slate-50 dark:bg-slate-900 border border-slate-200/70 dark:border-slate-700/50 rounded-xl pl-9 pr-8 text-base md:text-[11px] font-semibold text-slate-600 dark:text-slate-200 shadow-sm transition-all placeholder:text-slate-400/70 focus:border-emerald-500/30 focus:ring-2 focus:ring-emerald-500/10 focus:outline-none"
          />

          {searchTerm && (
            <button
              type="button"
              onClick={() => {
                setSearchTerm('')
                setSearchFocused(false)
                onSearchMatchSelect?.(null)
              }}
              className="absolute right-2 top-1/2 -translate-y-1/2 h-5 w-5 flex items-center justify-center rounded-md text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
              aria-label={t('Clear')}
              title={t('Clear')}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}

          {searchFocused && normalizedSearchTerm && (
            <div className="absolute left-0 top-11 z-30 w-full p-2 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-xl">
              {searchSuggestions.length === 0 && (
                <p className="px-2 py-2 text-[11px] font-semibold text-slate-400">{t('No clients found')}</p>
              )}
              {searchSuggestions.map((suggestion) => (
                <button
                  key={suggestion.key}
                  type="button"
                  onClick={() => handleSearchSuggestionSelect(suggestion)}
                  className="w-full text-left px-2.5 py-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
                >
                  <p className="text-[11px] font-black tracking-tight text-slate-800 dark:text-slate-100 whitespace-nowrap overflow-hidden text-ellipsis">
                    {renderHighlightedText(suggestion.clientName, normalizedSearchTerm)}
                  </p>
                  <p className="text-[10px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap overflow-hidden text-ellipsis">
                    {renderHighlightedText(suggestion.serial, normalizedSearchTerm)}
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-1.5 lg:gap-2">
          <button
            title={t('Collapse')}
            onClick={collapseAllNodes}
            className="h-9 w-9 lg:w-auto lg:px-3 flex items-center justify-center gap-1.5 bg-slate-50 dark:bg-slate-900 border border-slate-200/70 dark:border-slate-700/50 rounded-xl text-slate-500 shadow-sm hover:text-emerald-600 transition-all shrink-0"
          >
            <svg className="w-4 h-4 shrink-0" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="currentColor"><path d="M9 9H4v1h5V9z"/><path fillRule="evenodd" clipRule="evenodd" d="M5 3l1-1h7l1 1v7l-1 1h-2v2l-1 1H3l-1-1V6l1-1h2V3zm1 2h4l1 1v4h2V3H6v2zm4 1H3v7h7V6z"/></svg>
            <span className="hidden lg:inline text-[10px] font-black uppercase tracking-wider">{t('Collapse')}</span>
          </button>

          <div ref={alarmMenuContainerRef} className="relative">
            <button
              title={t('Alarm')}
              onClick={() => {
                setAlarmMenuOpen((prev) => !prev)
                setOltFilterOpen(false)
              }}
              className={`h-9 w-9 lg:w-auto lg:px-3 flex items-center justify-center gap-1.5 border rounded-xl shadow-sm transition-all shrink-0 ${
                alarmEnabled
                  ? 'bg-rose-50 dark:bg-rose-500/15 border-rose-300 dark:border-rose-500/50 text-rose-700 dark:text-rose-400'
                  : 'bg-slate-50 dark:bg-slate-900 border-slate-200/70 dark:border-slate-700/50 text-slate-500 hover:text-rose-600'
              }`}
            >
              <Bell className="w-3.5 h-3.5" />
              <span className="hidden lg:inline text-[10px] font-black uppercase tracking-wider">{t('Alarm')}</span>
            </button>

            {alarmMenuOpen && (
              <div className="absolute right-0 top-11 z-30 w-[272px] max-w-[calc(100vw-1.5rem)] p-3 rounded-xl border border-slate-200 dark:border-slate-700/50 bg-white dark:bg-slate-900 shadow-xl">
              <div className="flex items-center justify-between mb-3.5">
                <p className="text-[12px] font-black uppercase tracking-wide text-slate-700 dark:text-slate-200">{t('Alarm settings')}</p>
                <button
                  onClick={() => setAlarmMenuOpen(false)}
                  className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <p className="mb-3 text-[11px] leading-snug text-slate-400 dark:text-slate-500">
                {t('Alarm settings intro')}
              </p>

              <p className="text-[10px] font-black uppercase tracking-wide text-slate-500 mb-2">{t('Offline reasons')}</p>
              <div className="space-y-1.5 mb-3.5">
                {[
                  { key: 'linkLoss', label: t('Link Loss'), bulletClass: 'bg-rose-500' },
                  { key: 'dyingGasp', label: t('Dying Gasp'), bulletClass: 'bg-blue-500' },
                  { key: 'unknown', label: t('Unknown'), bulletClass: 'bg-purple-500' },
                ].map((reason) => {
                  const isSelected = alarmReasons[reason.key]
                  return (
                    <button
                      key={reason.key}
                      type="button"
                      onClick={() => toggleAlarmReason(reason.key)}
                      className={`
                        w-full flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-left transition-colors
                        ${isSelected
                          ? 'bg-slate-50/80 border-slate-200 dark:bg-slate-800 dark:border-slate-700'
                          : 'bg-white border-slate-200 hover:bg-slate-50/70 hover:border-slate-300 dark:bg-slate-900/40 dark:border-slate-700/50 dark:hover:bg-slate-800 dark:hover:border-slate-700'}
                      `}
                    >
                      <span className="h-4 w-4 shrink-0 flex items-center justify-center">
                        {isSelected ? (
                          <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-500" strokeWidth={3} />
                        ) : (
                          <span className="w-2 h-2 rounded-full bg-slate-300 dark:bg-slate-600" />
                        )}
                      </span>
                      <span className={`h-2 w-2 shrink-0 rounded-full ${reason.bulletClass}`} />
                      <span className="text-[10px] font-black uppercase tracking-wide text-slate-700 dark:text-slate-200">
                        {reason.label}
                      </span>
                    </button>
                  )
                })}
              </div>

              <div className="h-px bg-slate-200/80 dark:bg-slate-700/80 mb-3.5" />

              <div className="grid grid-cols-2 items-end gap-3">
                <div>
                  <label className="flex min-h-[24px] items-end text-[10px] leading-[1.1] font-black uppercase tracking-wide text-slate-500 mb-2">
                    {t('Minimum ONU count')}
                  </label>
                  <div className="h-9 flex items-center gap-1.5">
                    <input
                      type="number"
                      min={1}
                      max={128}
                      step={1}
                      value={alarmMinCountInput}
                      onChange={(e) => {
                        const nextValue = e.target.value
                        if (!/^\d{0,3}$/.test(nextValue)) return
                        setAlarmMinCountInput(nextValue)
                      }}
                      onBlur={() => {
                        setAlarmMinCountInput(String(clampAlarmMinCount(alarmMinCountInput)))
                      }}
                      className="h-9 w-14 rounded-lg border border-slate-200 dark:border-slate-700/50 bg-slate-50 dark:bg-slate-800 px-0 text-center [text-align-last:center] [appearance:textfield] [-moz-appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none tabular-nums text-[12px] font-bold leading-none text-slate-700 dark:text-slate-200"
                    />
                    <div className="h-9 w-8 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 overflow-hidden flex flex-col">
                      <button
                        type="button"
                        onClick={() => stepAlarmMinCount(1)}
                        disabled={effectiveAlarmMinCount >= 128}
                        className={`
                          h-1/2 w-full flex items-center justify-center transition-colors
                          ${effectiveAlarmMinCount >= 128
                            ? 'text-slate-300 cursor-not-allowed'
                            : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700 dark:hover:bg-slate-800'}
                        `}
                        aria-label="+1"
                        title="+1"
                      >
                        <Plus className="w-3 h-3" />
                      </button>
                      <div className="h-px w-full bg-slate-200 dark:bg-slate-700" />
                      <button
                        type="button"
                        onClick={() => stepAlarmMinCount(-1)}
                        disabled={effectiveAlarmMinCount <= 1}
                        className={`
                          h-1/2 w-full flex items-center justify-center transition-colors
                          ${effectiveAlarmMinCount <= 1
                            ? 'text-slate-300 cursor-not-allowed'
                            : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700 dark:hover:bg-slate-800'}
                        `}
                        aria-label="-1"
                        title="-1"
                      >
                        <Minus className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                </div>

                <div className="flex flex-col items-end">
                  <p className="flex min-h-[24px] items-end text-right text-[10px] leading-[1.1] font-black uppercase tracking-wide text-slate-500 mb-2">
                    {t('Alarm Mode')}
                  </p>
                  <div className="h-9 flex items-center justify-end gap-2.5">
                    <span className={`text-[12px] font-black leading-none tabular-nums ${alarmEnabled ? 'text-rose-600' : 'text-slate-400'}`}>
                      {alarmEnabled ? t('Enabled') : t('Disabled')}
                    </span>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={alarmEnabled}
                      onClick={() => setAlarmEnabled((prev) => !prev)}
                      className={`
                        relative h-6 w-11 rounded-full border transition-colors shrink-0
                        ${alarmEnabled
                          ? 'bg-rose-500 border-rose-500'
                          : 'bg-slate-200 dark:bg-slate-700 border-slate-300 dark:border-slate-600'}
                      `}
                    >
                      <span
                        className={`
                          absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform
                          ${alarmEnabled ? 'translate-x-5' : 'translate-x-0'}
                        `}
                      />
                    </button>
                  </div>
                </div>
              </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-12 px-4 lg:px-10 pb-40 animate-in fade-in duration-500">
        {loading && (
              <div className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">Loading live data...</div>
        )}
        {error && (
          <div className="text-[12px] font-bold text-rose-500 uppercase tracking-widest">{error}</div>
        )}
        {!loading && !error && filteredOlts.map((olt) => renderOlt(olt))}

        {!loading && !error && filteredOlts.length === 0 && (
          <div className="flex flex-col items-center justify-center w-full py-20 text-slate-300">
            <Search className="w-16 h-16 mb-4 opacity-10" />
            <p className="text-[12px] font-black uppercase tracking-[0.2em]">
              {normalizedSearchTerm
                ? t('No equipment matches your search')
                : alarmEnabled
                  ? t('No PON matches alarm filter')
                  : t('No OLTs found')}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
