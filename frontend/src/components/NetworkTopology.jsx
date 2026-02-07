import React, { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Server, Cable, Search, Filter, CircuitBoard, Bell, ChevronsUpDown, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getOnuStats } from '../utils/stats'

const pad2 = (value) => String(value).padStart(2, '0')
const asCount = (value) => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : 0
}

const NODE_CARD_STYLE = {
  // Keep all hierarchy levels visually coherent.
  pon: 'w-[180px] h-[56px] rounded-[12px]',
  olt: 'w-[180px] h-[56px] rounded-[12px]',
  slot: 'w-[180px] h-[56px] rounded-[12px]'
}

const NetworkNode = ({ type, label, isOpen, onToggle, active, children, stats, sublabel }) => {
  const isVisualActive = type === 'pon' ? active : isOpen

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
            ? 'border-emerald-500/35 shadow-md shadow-emerald-500/10'
            : 'border-slate-100 dark:border-slate-800 hover:border-slate-200 dark:hover:border-slate-700 shadow-sm'}
        `}
      >
        <div
          className={`
            absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full transition-all duration-300
            ${isVisualActive ? 'bg-emerald-500 scale-y-100' : 'bg-slate-100 dark:bg-slate-800 group-hover/node:bg-slate-300 scale-y-50'}
          `}
        />

        <div
          className={`
            flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-[10px] transition-all duration-300
            ${isVisualActive
              ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/20'
              : 'bg-[#F8FAFB] dark:bg-slate-800 text-slate-400 group-hover/node:text-slate-500'}
          `}
        >
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0 flex flex-col justify-center">
          <p
            className={`text-[10px] font-black uppercase tracking-tighter leading-none mb-0.5 transition-colors whitespace-nowrap overflow-hidden text-ellipsis ${
              isVisualActive ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-900 dark:text-white'
            }`}
          >
            {label}
          </p>

          {stats ? (
            <div className="mt-0.5 flex items-center gap-2.5 w-full pr-0.5">
              {asCount(stats.online) > 0 && <StatusItem color="bg-emerald-500" count={asCount(stats.online)} />}
              {asCount(stats.dyingGasp) > 0 && <StatusItem color="bg-blue-500" count={asCount(stats.dyingGasp)} />}
              {asCount(stats.linkLoss) > 0 && <StatusItem color="bg-rose-500" count={asCount(stats.linkLoss)} />}
              {asCount(stats.unknown) > 0 && <StatusItem color="bg-purple-500" count={asCount(stats.unknown)} />}
            </div>
          ) : (
            <p className="mt-0.5 text-[8px] font-black text-slate-400 uppercase tracking-widest opacity-80 whitespace-nowrap overflow-hidden text-ellipsis">{sublabel}</p>
          )}
        </div>

        {(type === 'olt' || type === 'slot') && (
          <div className={`transition-transform duration-300 ${isOpen ? 'rotate-180 text-emerald-500' : 'text-slate-300 group-hover/node:text-slate-400'}`}>
            <ChevronDown className="w-3 h-3" />
          </div>
        )}
      </div>

      {isOpen && children && (
        <div className="relative mt-2.5 ml-4 pl-8 border-l-[1.5px] border-slate-100 dark:border-slate-800 flex flex-col gap-2.5 animate-in slide-in-from-top-2 duration-300">
          {children}
        </div>
      )}
    </div>
  )
}

const StatusItem = ({ color, count }) => (
  <div className="flex items-center gap-1 min-w-0">
    <div className={`w-2 h-2 rounded-full shrink-0 ${color} shadow-sm shadow-current/20`} />
    <span className="text-[10px] font-bold text-slate-700 dark:text-slate-200 tabular-nums leading-none">{count}</span>
  </div>
)

export const NetworkTopology = ({ olts, loading, error, selectedPonId, onPonSelect }) => {
  const { t } = useTranslation()
  const [searchTerm, setSearchTerm] = useState('')
  const [openNodes, setOpenNodes] = useState({})
  const [oltFilterOpen, setOltFilterOpen] = useState(false)
  const [selectedOltIds, setSelectedOltIds] = useState([])
  const [alarmMenuOpen, setAlarmMenuOpen] = useState(false)
  const [alarmEnabled, setAlarmEnabled] = useState(false)
  const [alarmMinCount, setAlarmMinCount] = useState(1)
  const [alarmReasons, setAlarmReasons] = useState({
    linkLoss: true,
    dyingGasp: true,
    unknown: true,
  })
  const oltFilterInitializedRef = useRef(false)

  useEffect(() => {
    const allIds = olts.map((olt) => String(olt.id))
    setSelectedOltIds((prev) => {
      if (!oltFilterInitializedRef.current) {
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
      const firstSlot = firstOlt?.slots?.[0]
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

  const passesAlarmFilter = (pon) => {
    if (!alarmEnabled) return true
    if (pon?.is_active === false) return false
    if (!activeAlarmReasons.length) return false

    const stats = pon?.stats || getOnuStats(pon.onus || [])
    const counts = {
      linkLoss: asCount(stats.linkLoss),
      dyingGasp: asCount(stats.dyingGasp),
      unknown: asCount(stats.unknown),
    }

    return activeAlarmReasons.some((reason) => counts[reason] >= alarmMinCount)
  }

  const filteredOlts = useMemo(() => {
    const oltVisible = olts.filter((olt) => selectedOltIds.includes(String(olt.id)))
    const term = searchTerm.toLowerCase().trim()
    const searchFiltered = !term
      ? oltVisible
      : oltVisible.filter((olt) => {
          const name = (olt?.name || '').toLowerCase()
          const ip = (olt?.ip_address || '').toLowerCase()
          const vendor = (olt?.vendor_profile_name || '').toLowerCase()
          const matchOnu = (olt?.slots || []).some((slot) =>
            (slot?.pons || []).some((pon) =>
              (pon?.onus || []).some((onu) => {
                const onuName = (onu?.name || '').toLowerCase()
                const onuSerial = (onu?.serial || onu?.serial_number || '').toLowerCase()
                return onuName.includes(term) || onuSerial.includes(term)
              })
            )
          )
          return name.includes(term) || ip.includes(term) || vendor.includes(term) || matchOnu
        })

    if (!alarmEnabled) return searchFiltered

    return searchFiltered
      .map((olt) => {
        const slots = (olt.slots || [])
          .filter((slot) => slot?.is_active !== false)
          .map((slot) => {
            const pons = (slot.pons || []).filter((pon) => pon?.is_active !== false).filter((pon) => passesAlarmFilter(pon))
            return {
              ...slot,
              pons,
              pon_count: pons.length,
            }
          })
          .filter((slot) => (slot.pons || []).length > 0)

        return {
          ...olt,
          slots,
          slot_count: slots.length,
        }
      })
      .filter((olt) => (olt.slots || []).length > 0)
  }, [olts, selectedOltIds, searchTerm, alarmEnabled, alarmMinCount, activeAlarmReasons])

  useEffect(() => {
    if (!alarmEnabled) return
    setOpenNodes((prev) => {
      const next = { ...prev }
      filteredOlts.forEach((olt) => {
        next[`olt-${olt.id}`] = true
        ;(olt.slots || []).forEach((slot) => {
          next[`slot-${slot.id}`] = true
        })
      })
      return next
    })
  }, [alarmEnabled, filteredOlts])

  const renderOlt = (olt) => {
    const oltId = `olt-${olt.id}`
    const slotCount = olt.slot_count ?? olt.slots?.length ?? 0
    return (
      <div key={oltId} className="flex-shrink-0">
        <NetworkNode
          type="olt"
          label={olt.name}
          sublabel={`${slotCount} SLOTS`}
          isOpen={openNodes[oltId]}
          onToggle={() => toggleNode(oltId)}
        >
          {(olt.slots || [])
            .filter((slot) => slot?.is_active !== false)
            .map((slot) => {
              const slotId = `slot-${slot.id}`
              const ponCount = slot.pon_count ?? slot.pons?.length ?? 0
              const slotNumber = slot.slot_number ?? slot.slot_id ?? slot.id
              return (
                <NetworkNode
                  key={slotId}
                  type="slot"
                  label={`SLOT ${pad2(slotNumber)}`}
                  sublabel={`${ponCount} PONS`}
                  isOpen={openNodes[slotId]}
                  onToggle={() => toggleNode(slotId)}
                >
                  {(slot.pons || [])
                    .filter((pon) => pon?.is_active !== false)
                    .map((pon) => {
                      const stats = pon?.stats || getOnuStats(pon.onus || [])
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
      <div className="flex items-center gap-2 mb-6 px-10">
        <div className="relative w-full max-w-[340px]">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-300" />
          <input
            type="text"
            placeholder={t('Search')}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="h-9 w-full bg-[#F4F7FA] dark:bg-slate-900 border border-slate-200/70 dark:border-slate-800 rounded-xl pl-9 pr-3 text-[11px] font-semibold text-slate-600 dark:text-slate-200 shadow-sm transition-all placeholder:text-slate-400/70 focus:border-emerald-500/30 focus:ring-2 focus:ring-emerald-500/10 focus:outline-none"
          />
        </div>

        <div className="relative">
          <button
            title={t('Filter OLTs')}
            onClick={() => {
              setOltFilterOpen((prev) => !prev)
              setAlarmMenuOpen(false)
            }}
            className={`h-9 w-9 flex items-center justify-center border rounded-xl shadow-sm transition-all ${
              selectedOltIds.length < olts.length
                ? 'bg-emerald-50 border-emerald-300 text-emerald-700'
                : 'bg-[#F4F7FA] dark:bg-slate-900 border-slate-200/70 dark:border-slate-800 text-slate-400 hover:text-emerald-600'
            }`}
          >
            <Filter className="w-3.5 h-3.5" />
          </button>

          {oltFilterOpen && (
            <div className="absolute left-0 top-11 z-30 w-[260px] p-3 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xl">
              <p className="text-[11px] font-black uppercase tracking-wider text-slate-700 dark:text-slate-200 mb-3">{t('Filter OLTs')}</p>

              <div className="flex items-center gap-2 mb-3">
                <button
                  onClick={() => setSelectedOltIds(olts.map((olt) => String(olt.id)))}
                  className="px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-[10px] font-black uppercase tracking-wider text-slate-600 dark:text-slate-300"
                >
                  {t('All')}
                </button>
                <button
                  onClick={() => setSelectedOltIds([])}
                  className="px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-[10px] font-black uppercase tracking-wider text-slate-600 dark:text-slate-300"
                >
                  {t('Clear')}
                </button>
              </div>

              <div className="max-h-[220px] overflow-auto space-y-2 pr-1">
                {olts.map((olt) => (
                  <label key={olt.id} className="flex items-center gap-2 text-[11px] text-slate-700 dark:text-slate-200 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selectedOltIds.includes(String(olt.id))}
                      onChange={(e) => {
                        setSelectedOltIds((prev) => {
                          const id = String(olt.id)
                          if (e.target.checked) {
                            return prev.includes(id) ? prev : [...prev, id]
                          }
                          return prev.filter((currentId) => currentId !== id)
                        })
                      }}
                      className="accent-emerald-600"
                    />
                    <span className="truncate">{olt.name}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2 relative">
          <button
            title={t('Collapse')}
            onClick={collapseAllNodes}
            className="h-9 px-3 flex items-center justify-center gap-1.5 bg-[#F4F7FA] dark:bg-slate-900 border border-slate-200/70 dark:border-slate-800 rounded-xl text-slate-500 shadow-sm hover:text-emerald-600 transition-all"
          >
            <ChevronsUpDown className="w-3.5 h-3.5" />
            <span className="text-[10px] font-black uppercase tracking-wider hidden md:block">{t('Collapse')}</span>
          </button>

          <button
            title={t('Alarm')}
            onClick={() => {
              setAlarmMenuOpen((prev) => !prev)
              setOltFilterOpen(false)
            }}
            className={`h-9 px-3 flex items-center justify-center gap-1.5 border rounded-xl shadow-sm transition-all ${
              alarmEnabled
                ? 'bg-emerald-50 border-emerald-300 text-emerald-700'
                : 'bg-[#F4F7FA] dark:bg-slate-900 border-slate-200/70 dark:border-slate-800 text-slate-500 hover:text-emerald-600'
            }`}
          >
            <Bell className="w-3.5 h-3.5" />
            <span className="text-[10px] font-black uppercase tracking-wider hidden md:block">{t('Alarm')}</span>
          </button>

          {alarmMenuOpen && (
            <div className="absolute right-0 top-11 z-30 w-[300px] p-3 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-xl">
              <div className="flex items-center justify-between mb-3">
                <p className="text-[11px] font-black uppercase tracking-wider text-slate-700 dark:text-slate-200">{t('Alarm')}</p>
                <button
                  onClick={() => setAlarmMenuOpen(false)}
                  className="p-1 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <label className="flex items-center gap-2 mb-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={alarmEnabled}
                  onChange={(e) => setAlarmEnabled(e.target.checked)}
                  className="accent-emerald-600"
                />
                <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200">{t('Alarm Mode')}</span>
              </label>

              <p className="text-[10px] font-semibold text-slate-500 mb-3">{t('Only show PONs with offline ONUs')}</p>

              <p className="text-[10px] font-black uppercase tracking-wider text-slate-500 mb-2">{t('Offline reasons')}</p>
              <div className="grid grid-cols-2 gap-2 mb-2">
                <label className="flex items-center gap-2 text-[11px] text-slate-700 dark:text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alarmReasons.linkLoss}
                    onChange={(e) => setAlarmReasons((prev) => ({ ...prev, linkLoss: e.target.checked }))}
                    className="accent-rose-500"
                  />
                  <span>{t('Link Loss')}</span>
                </label>
                <label className="flex items-center gap-2 text-[11px] text-slate-700 dark:text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alarmReasons.dyingGasp}
                    onChange={(e) => setAlarmReasons((prev) => ({ ...prev, dyingGasp: e.target.checked }))}
                    className="accent-blue-500"
                  />
                  <span>{t('Dying Gasp')}</span>
                </label>
                <label className="flex items-center gap-2 text-[11px] text-slate-700 dark:text-slate-200 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alarmReasons.unknown}
                    onChange={(e) => setAlarmReasons((prev) => ({ ...prev, unknown: e.target.checked }))}
                    className="accent-purple-500"
                  />
                  <span>{t('Unknown')}</span>
                </label>
              </div>

              <div className="flex items-center gap-2 mb-3">
                <button
                  onClick={() => setAlarmReasons({ linkLoss: true, dyingGasp: true, unknown: true })}
                  className="px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-[10px] font-black uppercase tracking-wider text-slate-600 dark:text-slate-300"
                >
                  {t('All')}
                </button>
                <button
                  onClick={() => setAlarmReasons({ linkLoss: false, dyingGasp: false, unknown: false })}
                  className="px-2 py-1 rounded-lg bg-slate-100 dark:bg-slate-800 text-[10px] font-black uppercase tracking-wider text-slate-600 dark:text-slate-300"
                >
                  {t('Clear')}
                </button>
              </div>

              <label className="block text-[10px] font-black uppercase tracking-wider text-slate-500 mb-1">
                {t('Minimum ONU count')}
              </label>
              <input
                type="number"
                min={1}
                value={alarmMinCount}
                onChange={(e) => setAlarmMinCount(Math.max(1, Number(e.target.value) || 1))}
                className="h-8 w-full rounded-lg border border-slate-200 dark:border-slate-800 bg-[#F8FAFB] dark:bg-slate-800 px-2 text-[11px] font-semibold text-slate-700 dark:text-slate-200"
              />
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-12 p-10 pt-0 pb-40 animate-in fade-in duration-500">
        {loading && (
          <div className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">Loading live ZTE data...</div>
        )}
        {error && (
          <div className="text-[11px] font-bold text-rose-500 uppercase tracking-widest">{error}</div>
        )}
        {!loading && !error && filteredOlts.map((olt) => renderOlt(olt))}

        {!loading && !error && filteredOlts.length === 0 && (
          <div className="flex flex-col items-center justify-center w-full py-20 text-slate-300">
            <Search className="w-16 h-16 mb-4 opacity-10" />
            <p className="text-[12px] font-black uppercase tracking-[0.2em]">
              {alarmEnabled
                ? t('No PON matches alarm filter')
                : searchTerm
                  ? t('No equipment matches your search')
                  : t('No ZTE OLTs found')}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
