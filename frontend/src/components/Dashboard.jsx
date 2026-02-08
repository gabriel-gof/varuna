import React, { useState } from 'react'
import {
  Users,
  Wifi,
  WifiOff,
  AlertTriangle,
  Zap,
  HelpCircle,
  Server,
  ChevronDown,
  RefreshCcw
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatePresence, motion } from 'motion/react'

const StatCard = ({ title, value, icon: Icon, accentBg, accentText, subtext }) => (
  <div className="bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 p-6 rounded-2xl shadow-sm hover:shadow-lg hover:-translate-y-1 transition-all duration-300 group cursor-default">
    <div className="flex items-center justify-between mb-4">
      <div className={`w-12 h-12 rounded-xl ${accentBg} flex items-center justify-center ${accentText} group-hover:scale-110 transition-transform`}>
        <Icon className="w-6 h-6" />
      </div>
      {subtext && <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">{subtext}</span>}
    </div>
    <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-1">{title}</h4>
    <p className="text-3xl font-black text-slate-900 dark:text-white">{value.toLocaleString()}</p>
  </div>
)

const StatColumn = ({ label, value, color }) => (
  <div className="flex flex-col items-center min-w-[60px]">
    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest mb-1">{label}</span>
    <span className={`text-[16px] font-black ${color} tabular-nums`}>{value.toLocaleString()}</span>
  </div>
)

const ExpandableOltRow = ({ name, authorized, online, offline, dyingGasp, linkLoss, unknown, onlineLabel, offlineLabel, offlineTrendLabel }) => {
  const [isExpanded, setIsExpanded] = useState(false)

  return (
    <div className="flex flex-col gap-4 w-full">
      <div
        onClick={() => setIsExpanded(!isExpanded)}
        className={`
          flex items-center px-6 py-5 bg-white dark:bg-slate-900 rounded-[28px] border-2 transition-all duration-300 cursor-pointer shadow-sm min-h-[88px]
          ${isExpanded ? 'border-emerald-500 ring-4 ring-emerald-500/5 shadow-md' : 'border-emerald-500/10 hover:border-emerald-500/30 hover:shadow-md'}
        `}
      >
        <div className="flex items-center gap-4 flex-1">
          <div className="w-10 h-10 bg-emerald-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-emerald-500/20 flex-shrink-0">
            <Server className="w-5 h-5" />
          </div>
          <div className="flex flex-col">
            <span className="text-[13px] font-black text-slate-900 dark:text-white uppercase tracking-tight">{name}</span>
            <span className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Active Infrastructure</span>
          </div>
        </div>

        <div className="items-center gap-6 lg:gap-10 mr-8 hidden md:flex">
          <StatColumn label="AUTH" value={authorized} color="text-slate-900 dark:text-white" />
          <StatColumn label={onlineLabel} value={online} color="text-emerald-500" />
          <StatColumn label={offlineLabel} value={offline} color="text-rose-500" />
          <StatColumn label="GASP" value={dyingGasp} color="text-blue-500" />
          <StatColumn label="LOSS" value={linkLoss} color="text-rose-400" />
          <StatColumn label="UNK" value={unknown} color="text-purple-500" />
        </div>

        <div className={`transition-transform duration-300 ${isExpanded ? 'rotate-180' : ''}`}>
          <ChevronDown className="w-6 h-6 text-slate-300" />
        </div>
      </div>

      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ opacity: 0, y: -20, height: 0 }}
            animate={{ opacity: 1, y: 0, height: 'auto' }}
            exit={{ opacity: 0, y: -20, height: 0 }}
            className="overflow-hidden"
          >
            <div className="p-6 bg-[#F8FAFB] dark:bg-slate-800/20 rounded-[32px] border border-slate-100 dark:border-slate-800 flex flex-col xl:flex-row gap-6">
              <div className="flex-1 bg-white dark:bg-slate-900 p-8 rounded-[28px] shadow-sm border border-slate-50 dark:border-slate-800">
                <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-10 flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-rose-500" />
                  {offlineTrendLabel}
                </h4>
                <div className="h-[240px] w-full flex items-center justify-center text-[11px] font-black text-slate-300 uppercase tracking-[0.2em]">
                  No history data
                </div>
              </div>

              <div className="flex-1 bg-white dark:bg-slate-900 p-8 rounded-[28px] shadow-sm border border-slate-50 dark:border-slate-800">
                <h4 className="text-[11px] font-black text-slate-400 uppercase tracking-widest mb-10">ALERTS BY CATEGORY</h4>
                <div className="h-[240px] w-full flex items-center justify-center text-[11px] font-black text-slate-300 uppercase tracking-[0.2em]">
                  No history data
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export const Dashboard = ({ stats, oltStats, loading, error, onRefresh, isRefreshing }) => {
  const { t } = useTranslation()
  const [historyInterval, setHistoryInterval] = useState('6h')
  const intervals = ['5m', '1h', '6h', '24h', '7d', '30d']

  const safeStats = {
    authorized: stats?.total || 0,
    online: stats?.online || 0,
    offline: stats?.offline || 0,
    dyingGasp: stats?.dyingGasp || 0,
    linkLoss: stats?.linkLoss || 0,
    unknown: stats?.unknown || 0
  }

  return (
    <div className="w-full max-w-7xl mx-auto p-6 lg:p-10 space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 pb-8 border-b border-slate-100 dark:border-slate-800">
        <div className="flex flex-col gap-1">
          <h2 className="text-3xl font-black text-slate-900 dark:text-white uppercase tracking-tight">System Overview</h2>
          <p className="text-[12px] font-bold text-slate-400 uppercase tracking-widest">Global Network health monitoring</p>
        </div>

        <div className="flex flex-wrap items-center gap-4">
          <div className="flex bg-slate-100 dark:bg-slate-800 p-1 rounded-xl">
            {intervals.map((int) => (
              <button
                key={int}
                onClick={() => setHistoryInterval(int)}
                className={`px-4 py-2 text-[10px] font-black uppercase rounded-lg transition-all ${
                  historyInterval === int
                    ? 'bg-white dark:bg-slate-700 text-emerald-600 shadow-sm'
                    : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'
                }`}
              >
                {int}
              </button>
            ))}
          </div>

          <div className="h-8 w-px bg-slate-100 dark:bg-slate-800 mx-2" />

          <button
            onClick={onRefresh}
            className={`p-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl shadow-lg shadow-emerald-600/20 transition-all ${isRefreshing ? 'rotate-180' : ''}`}
          >
            <RefreshCcw className={`w-[18px] h-[18px] ${isRefreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading && (
        <div className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">Loading live ZTE data...</div>
      )}
      {error && (
        <div className="text-[11px] font-bold text-rose-500 uppercase tracking-widest">{error}</div>
      )}
      {!loading && !error && oltStats.length === 0 && (
        <div className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">{t('No ZTE OLTs found')}</div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-8">
        <StatCard title="Total Authorized" value={safeStats.authorized} icon={Users} accentBg="bg-slate-500/10" accentText="text-slate-600" subtext="Units" />
        <StatCard title={`Total ${t('Online')}`} value={safeStats.online} icon={Wifi} accentBg="bg-emerald-500/10" accentText="text-emerald-500" subtext={t('Online')} />
        <StatCard title={`Total ${t('Offline')}`} value={safeStats.offline} icon={WifiOff} accentBg="bg-rose-500/10" accentText="text-rose-500" subtext={t('Offline')} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-8">
        <div className="bg-blue-50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-blue-500 text-white flex items-center justify-center shadow-lg shadow-blue-500/20">
            <Zap className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-blue-400 uppercase tracking-widest">{t('Dying Gasp')}</h4>
            <p className="text-2xl font-black text-blue-700 dark:text-blue-300">{safeStats.dyingGasp}</p>
          </div>
        </div>

        <div className="bg-rose-50 dark:bg-rose-900/10 border border-rose-100 dark:border-rose-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-rose-400 text-white flex items-center justify-center shadow-lg shadow-rose-400/20">
            <AlertTriangle className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-rose-400 uppercase tracking-widest">{t('Link Loss')}</h4>
            <p className="text-2xl font-black text-rose-700 dark:text-rose-300">{safeStats.linkLoss}</p>
          </div>
        </div>

        <div className="bg-purple-50 dark:bg-purple-900/10 border border-purple-100 dark:border-purple-800 p-6 rounded-[24px] flex items-center gap-5 hover:shadow-md transition-all duration-300">
          <div className="w-12 h-12 rounded-xl bg-purple-500 text-white flex items-center justify-center shadow-lg shadow-purple-500/20">
            <HelpCircle className="w-6 h-6" />
          </div>
          <div>
            <h4 className="text-[11px] font-black text-purple-400 uppercase tracking-widest">{t('Unknown')}</h4>
            <p className="text-2xl font-black text-purple-700 dark:text-purple-300">{safeStats.unknown}</p>
          </div>
        </div>
      </div>

      <div className="space-y-8 pt-6">
        <h3 className="text-2xl font-black text-slate-800 dark:text-white uppercase tracking-tight">Per OLT Breakdown</h3>
        <div className="flex flex-col gap-6">
          {oltStats.map((olt) => (
            <ExpandableOltRow
              key={olt.id}
              {...olt}
              onlineLabel={t('Online')}
              offlineLabel={t('Offline')}
              offlineTrendLabel={t('Offline trend analysis')}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
