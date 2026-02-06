import React, { useEffect, useMemo, useState } from 'react'
import { ChevronDown, Server, Cable, Search, Filter, CircuitBoard } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getOnuStats } from '../utils/stats'

const pad2 = (value) => String(value).padStart(2, '0')

const NetworkNode = ({ type, label, isOpen, onToggle, active, children, stats, sublabel }) => {
  const isVisualActive = type === 'pon' ? active : isOpen

  const icons = {
    olt: Server,
    slot: CircuitBoard,
    pon: Cable
  }
  const Icon = icons[type]

  const cardStyle = 'w-[250px] h-[74px] rounded-[18px]'

  return (
    <div className="flex flex-col relative">
      <div
        onClick={onToggle}
        className={`
          relative flex items-center gap-3 p-3 bg-white dark:bg-slate-900 border transition-all duration-300 cursor-pointer group/node shrink-0
          ${cardStyle}
          ${isVisualActive
            ? 'border-emerald-500/30 ring-4 ring-emerald-500/5 shadow-lg shadow-emerald-500/5'
            : 'border-slate-100 dark:border-slate-800 hover:border-slate-200 dark:hover:border-slate-700 shadow-sm'}
        `}
      >
        <div
          className={`
            absolute left-0 top-1/2 -translate-y-1/2 w-1 h-8 rounded-r-full transition-all duration-300
            ${isVisualActive ? 'bg-emerald-500 scale-y-100' : 'bg-slate-100 dark:bg-slate-800 group-hover/node:bg-slate-300 scale-y-50'}
          `}
        />

        <div
          className={`
            flex-shrink-0 w-10 h-10 flex items-center justify-center rounded-[12px] transition-all duration-300
            ${isVisualActive
              ? 'bg-emerald-600 text-white shadow-lg shadow-emerald-600/20'
              : 'bg-[#F8FAFB] dark:bg-slate-800 text-slate-400 group-hover/node:text-slate-500'}
          `}
        >
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0 flex flex-col justify-center">
          <p
            className={`text-[13px] font-black uppercase tracking-tight leading-none mb-1.5 transition-colors ${
              isVisualActive ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-900 dark:text-white'
            }`}
          >
            {label}
          </p>

          {stats ? (
            <div className="flex items-center justify-between gap-1 w-full pr-1">
              <StatusItem color="bg-emerald-500" count={stats.online} />
              <StatusItem color="bg-blue-500" count={stats.dyingGasp} />
              <StatusItem color="bg-rose-500" count={stats.linkLoss} />
              <StatusItem color="bg-purple-500" count={stats.unknown} />
            </div>
          ) : (
            <p className="text-[9px] font-black text-slate-400 uppercase tracking-widest opacity-80">{sublabel}</p>
          )}
        </div>

        {(type === 'olt' || type === 'slot') && (
          <div className={`transition-transform duration-300 ${isOpen ? 'rotate-180 text-emerald-500' : 'text-slate-300 group-hover/node:text-slate-400'}`}>
            <ChevronDown className="w-4 h-4" />
          </div>
        )}
      </div>

      {isOpen && children && (
        <div className="relative mt-4 ml-6 pl-10 border-l-[1.5px] border-slate-100 dark:border-slate-800 flex flex-col gap-5 animate-in slide-in-from-top-2 duration-300">
          {children}
        </div>
      )}
    </div>
  )
}

const StatusItem = ({ color, count }) => (
  <div className="flex items-center gap-1 min-w-0">
    <div className={`w-2 h-2 rounded-full ${color} shadow-sm shadow-current/20 shrink-0`} />
    <span className="text-[12px] font-bold text-slate-700 dark:text-slate-200 tabular-nums leading-none">{count}</span>
  </div>
)

export const NetworkTopology = ({ olts, loading, error, selectedPonId, onPonSelect }) => {
  const { t } = useTranslation()
  const [searchTerm, setSearchTerm] = useState('')
  const [openNodes, setOpenNodes] = useState({})

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

  const filteredOlts = useMemo(() => {
    const term = searchTerm.toLowerCase()
    if (!term) return olts
    return olts.filter((olt) => {
      const name = (olt?.name || '').toLowerCase()
      const ip = (olt?.ip_address || '').toLowerCase()
      const vendor = (olt?.vendor_profile_name || '').toLowerCase()
      return name.includes(term) || ip.includes(term) || vendor.includes(term)
    })
  }, [olts, searchTerm])

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
                      const stats = getOnuStats(pon.onus || [])
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
    <div className="flex flex-col w-full h-full pt-10">
      <div className="flex items-center gap-3 mb-12 px-10">
        <div className="relative w-[480px]">
          <Search className="absolute left-5 top-1/2 -translate-y-1/2 w-[18px] h-[18px] text-slate-300" />
          <input
            type="text"
            placeholder={t('Search')}
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full bg-[#F3F6F9] dark:bg-slate-900 border-none rounded-[18px] py-4 pl-14 pr-6 text-[13px] font-bold text-slate-600 dark:text-slate-200 focus:ring-0 transition-all placeholder:text-slate-400/70"
          />
        </div>

        <button className="w-[52px] h-[52px] flex items-center justify-center bg-[#F3F6F9] dark:bg-slate-900 rounded-[18px] text-slate-400 hover:text-emerald-600 transition-all">
          <Filter className="w-5 h-5" />
        </button>
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
              {searchTerm ? t('No equipment matches your search') : t('No ZTE OLTs found')}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
