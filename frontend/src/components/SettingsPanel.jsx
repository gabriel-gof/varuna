import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Trash2, RefreshCcw, Check, AlertCircle, CheckCircle2, ChevronDown, Server, Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DEFAULT_THRESHOLDS, getOltThresholds, saveOltThresholds, clearOltThresholds, hasOltOverride } from '../utils/powerThresholds'

const MAX_OLT_NAME = 12

const timeAgo = (dateStr, t) => {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  if (isNaN(date.getTime())) return dateStr
  const seconds = Math.floor((new Date() - date) / 1000)
  if (seconds < 60) return t('just now')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} ${t('min ago')}`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${t('h ago')}`
  const days = Math.floor(hours / 24)
  return `${days} ${t('d ago')}`
}

const buildInitialForm = (vendorProfiles = []) => {
  const firstVendor = vendorProfiles[0]?.vendor || ''
  const firstModel = vendorProfiles.find((item) => item.vendor === firstVendor)
  return {
    name: '',
    ip_address: '',
    vendor: firstVendor,
    vendor_profile: firstModel?.id ? String(firstModel.id) : '',
    snmp_community: 'public',
    snmp_port: '161',
    discovery_interval: '4h',
    polling_interval: '5m',
    power_interval: '5m'
  }
}

const buildEditForm = (olt, vendorProfiles = []) => {
  const vp = vendorProfiles.find((item) => item.id === olt.vendor_profile)
  return {
    name: olt.name || '',
    ip_address: olt.ip_address || '',
    vendor: vp?.vendor || '',
    vendor_profile: olt.vendor_profile ? String(olt.vendor_profile) : '',
    snmp_community: olt.snmp_community || 'public',
    snmp_port: String(olt.snmp_port || 161),
    discovery_interval: formatDuration((olt.discovery_interval_minutes || 240) * 60),
    polling_interval: formatDuration(olt.polling_interval_seconds || 300),
    power_interval: formatDuration(olt.power_interval_seconds || 300)
  }
}

const toPositiveInteger = (value, fallback) => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback
  return Math.round(parsed)
}

/**
 * Parse a Zabbix-style duration string to seconds.
 * Accepts: bare number (seconds), or suffixed: 30s, 5m, 1h, 1d
 */
const parseDuration = (input) => {
  if (!input) return null
  const str = String(input).trim().toLowerCase()
  const match = str.match(/^(\d+(?:\.\d+)?)\s*([smhd])?$/)
  if (!match) return null
  const num = parseFloat(match[1])
  if (!Number.isFinite(num) || num <= 0) return null
  const unit = match[2] || 's'
  const multipliers = { s: 1, m: 60, h: 3600, d: 86400 }
  return Math.round(num * multipliers[unit])
}

/** Format seconds into the most readable Zabbix-style duration */
const formatDuration = (totalSeconds) => {
  const s = Number(totalSeconds)
  if (!Number.isFinite(s) || s <= 0) return ''
  if (s % 86400 === 0) return `${s / 86400}d`
  if (s % 3600 === 0) return `${s / 3600}h`
  if (s % 60 === 0) return `${s / 60}m`
  return `${s}s`
}

/** Format a timestamp into a human-readable relative time */
const formatRelativeTime = (timestamp, t) => {
  if (!timestamp) return t('Never')
  const ms = Date.parse(timestamp)
  if (!Number.isFinite(ms)) return t('Never')
  const diffMs = Date.now() - ms
  if (diffMs < 60_000) return t('just now')
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 60) return `${mins} ${t('min ago')}`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} ${t('h ago')}`
  const days = Math.floor(hours / 24)
  return `${days} ${t('d ago')}`
}

/** Compare editForm to OLT data to detect unsaved changes */
const isFormDirty = (editForm, olt, vendorProfiles) => {
  if (!editForm || !olt) return false
  const original = buildEditForm(olt, vendorProfiles)
  return Object.keys(original).some((key) => {
    // For duration fields, compare resolved seconds instead of raw strings
    if (['discovery_interval', 'polling_interval', 'power_interval'].includes(key)) {
      return parseDuration(editForm[key]) !== parseDuration(original[key])
    }
    return String(editForm[key] || '') !== String(original[key] || '')
  })
}

/* ─── Shared field components ─── */

const FieldLabel = ({ children }) => (
  <span className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 select-none">
    {children}
  </span>
)

const FieldInput = React.forwardRef(({ className = '', ...props }, ref) => (
  <input
    ref={ref}
    {...props}
    className={`h-8 w-full px-2.5 rounded-[8px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200 placeholder:text-slate-300 dark:placeholder:text-slate-600
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all
      [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none ${className}`}
  />
))

const FieldSelect = ({ className = '', children, ...props }) => (
  <select
    {...props}
    className={`h-8 w-full px-2.5 rounded-[8px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all disabled:opacity-50 ${className}`}
  >
    {children}
  </select>
)

const SectionLabel = ({ children }) => (
  <span className="text-[9px] font-black uppercase tracking-[0.15em] text-slate-300 dark:text-slate-600 select-none">{children}</span>
)

/* ─── OLT Health colors ─── */

const OLT_HEALTH = {
  green: {
    borderActive: 'border-emerald-500/35 shadow-md shadow-emerald-500/10',
    borderIdle: 'border-emerald-300 dark:border-emerald-500/25 hover:border-emerald-400 dark:hover:border-emerald-500/40 shadow-sm',
    accentActive: 'bg-emerald-500 scale-y-100',
    accentIdle: 'bg-emerald-200/60 dark:bg-emerald-500/25 group-hover/node:bg-emerald-300 dark:group-hover/node:bg-emerald-400 scale-y-60',
    iconActive: 'bg-emerald-600 dark:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20',
    iconIdle: 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 ring-1 ring-inset ring-emerald-600/15 dark:ring-emerald-400/25',
    labelActive: 'text-emerald-950 dark:text-emerald-50',
    chevronOpen: 'text-emerald-600 dark:text-emerald-400',
  },
  yellow: {
    borderActive: 'border-yellow-500/40 shadow-md shadow-yellow-500/10',
    borderIdle: 'border-yellow-300 dark:border-yellow-500/20 hover:border-yellow-400 dark:hover:border-yellow-500/40 shadow-sm',
    accentActive: 'bg-yellow-500 scale-y-100',
    accentIdle: 'bg-yellow-200/60 dark:bg-yellow-500/20 group-hover/node:bg-yellow-300 dark:group-hover/node:bg-yellow-400 scale-y-60',
    iconActive: 'bg-yellow-500 text-white shadow-lg shadow-yellow-500/30',
    iconIdle: 'bg-yellow-100 dark:bg-yellow-500/15 text-yellow-800 dark:text-yellow-400 ring-1 ring-inset ring-yellow-600/20 dark:ring-yellow-400/20',
    labelActive: 'text-yellow-950 dark:text-yellow-50',
    chevronOpen: 'text-yellow-600 dark:text-yellow-400',
  },
  red: {
    borderActive: 'border-rose-500/35 shadow-md shadow-rose-500/10',
    borderIdle: 'border-rose-300 dark:border-rose-500/25 hover:border-rose-400 dark:hover:border-rose-500/40 shadow-sm',
    accentActive: 'bg-rose-500 scale-y-100',
    accentIdle: 'bg-rose-200/60 dark:bg-rose-500/25 group-hover/node:bg-rose-300 dark:group-hover/node:bg-rose-400 scale-y-60',
    iconActive: 'bg-rose-600 dark:bg-rose-500 text-white shadow-lg shadow-rose-600/20',
    iconIdle: 'bg-rose-100 dark:bg-rose-500/20 text-rose-700 dark:text-rose-400 ring-1 ring-inset ring-rose-600/15 dark:ring-rose-400/25',
    labelActive: 'text-rose-950 dark:text-rose-50',
    chevronOpen: 'text-rose-600 dark:text-rose-400',
  },
  gray: {
    borderActive: 'border-slate-400/50 shadow-md shadow-slate-400/15',
    borderIdle: 'border-slate-300/80 dark:border-slate-500/40 hover:border-slate-400 dark:hover:border-slate-400/60 shadow-sm',
    accentActive: 'bg-slate-400 scale-y-100',
    accentIdle: 'bg-slate-300/70 dark:bg-slate-500/40 group-hover/node:bg-slate-400/80 dark:group-hover/node:bg-slate-400/50 scale-y-60',
    iconActive: 'bg-slate-500 dark:bg-slate-400 text-white shadow-lg shadow-slate-500/25',
    iconIdle: 'bg-slate-200/80 dark:bg-slate-600/50 text-slate-500 dark:text-slate-400 ring-1 ring-inset ring-slate-400/30 dark:ring-slate-400/25',
    labelActive: 'text-slate-600 dark:text-slate-200',
    chevronOpen: 'text-slate-500 dark:text-slate-400',
  },
  neutral: {
    borderActive: 'border-slate-500/35 shadow-md shadow-slate-500/10',
    borderIdle: 'border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600 shadow-sm',
    accentActive: 'bg-slate-500 scale-y-100',
    accentIdle: 'bg-slate-200 dark:bg-slate-700 group-hover/node:bg-slate-300 dark:group-hover/node:bg-slate-600 scale-y-60',
    iconActive: 'bg-slate-600 dark:bg-slate-500 text-white shadow-lg shadow-slate-600/20',
    iconIdle: 'bg-slate-50 dark:bg-slate-800 text-slate-600 dark:text-slate-400 ring-1 ring-inset ring-slate-600/10 dark:ring-slate-400/20',
    labelActive: 'text-slate-950 dark:text-slate-50',
    chevronOpen: 'text-slate-600 dark:text-slate-400',
  }
}

const getOltHealth = (olt, snmpStatuses, oltHealthById) => {
  const derived = oltHealthById?.[String(olt.id)] || oltHealthById?.[olt.id]
  if (derived?.state && OLT_HEALTH[derived.state]) return OLT_HEALTH[derived.state]
  const st = snmpStatuses?.[olt.id]
  if (st?.status === 'unreachable') return OLT_HEALTH.gray
  if (!st || st.status === 'pending') return OLT_HEALTH.neutral
  return OLT_HEALTH.green
}

const getSnmpBadge = (olt, snmpStatuses, t) => {
  const st = snmpStatuses?.[olt.id]
  if (!st || st.status === 'pending') return { label: t('Checking'), color: 'bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500', dot: 'bg-slate-300 dark:bg-slate-600' }
  if (st.status === 'unreachable') return { label: t('Unreachable'), color: 'bg-rose-50 dark:bg-rose-500/10 text-rose-500 dark:text-rose-400', dot: 'bg-rose-400 dark:bg-rose-500' }
  return { label: t('Reachable'), color: 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-400 dark:bg-emerald-500' }
}

/* ─── OLT Card header ─── */

const OltCard = ({ olt, isSelected, health, onSelect, onDelete, deleteBusy, resolvedVendor, t, children }) => {
  const total = Number(olt.onu_count || 0)
  const online = Number(olt.online_count || 0)
  const offline = Number(olt.offline_count || 0)
  const hasOnus = total > 0

  return (
    <div className={`
      w-full transition-all duration-300 bg-white dark:bg-slate-900
      rounded-[14px] border relative
      ${isSelected ? health.borderActive : health.borderIdle}
    `}>
      <div
        onClick={() => onSelect(isSelected ? null : String(olt.id))}
        className="group/node relative flex items-center gap-2.5 px-3 py-2.5 cursor-pointer select-none"
      >
        <div className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full transition-all duration-300 ${
          isSelected ? health.accentActive : health.accentIdle
        }`} />

        <div className={`flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-[10px] transition-all duration-300 ${
          isSelected ? health.iconActive : health.iconIdle
        }`}>
          <Server className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0 flex flex-col justify-center">
          <p className={`text-[11px] font-black uppercase tracking-tight leading-none mb-0.5 transition-colors ${
            isSelected ? health.labelActive : 'text-slate-900 dark:text-white'
          }`}>
            {olt.name || '\u2014'}
          </p>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{olt.ip_address || '\u2014'}:{olt.snmp_port || '161'}</span>
            <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
            <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">{String(resolvedVendor || 'Unknown').toUpperCase()}</span>
            <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">{olt.model_display || olt.vendor_profile_name || '\u2014'}</span>
          </div>
        </div>

        {/* Header stats */}
        <div className="flex flex-col items-end mr-2 text-right">
           {hasOnus ? (
             <div className="flex items-center gap-1.5 leading-none">
                <span className="text-[10px] font-black text-slate-500 dark:text-slate-400 tabular-nums">{total} <span className="text-[8px] uppercase font-bold text-slate-300 dark:text-slate-600">ONUs</span></span>
                <span className="w-[2px] h-2 bg-slate-200 dark:bg-slate-700" />
                <div className="flex items-center gap-0.5">
                  <span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">{online}</span>
                  <span className="text-[8px] text-slate-300 dark:text-slate-600">/</span>
                  <span className="text-[10px] font-bold text-rose-500 dark:text-rose-400 tabular-nums">{offline}</span>
                </div>
             </div>
           ) : (
             <span className="text-[9px] font-bold text-slate-300 dark:text-slate-600 uppercase tracking-wider">{t('No ONUs')}</span>
           )}
        </div>

        <div className={`transition-transform duration-300 ${
          isSelected ? `rotate-180 ${health.chevronOpen}` : 'text-slate-300 group-hover/node:text-slate-400'
        }`}>
          <ChevronDown className="w-3 h-3" />
        </div>
      </div>

      {/* Subtle delete icon - shown on hover */}
      {isSelected && (
        <div className="absolute top-2 right-2 z-20">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete?.(olt.id) }}
            disabled={deleteBusy}
            className="w-6 h-6 rounded-md flex items-center justify-center text-slate-300 dark:text-slate-600 hover:text-rose-500 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            title={t('Remove OLT')}
          >
            {deleteBusy ? <RefreshCcw className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
          </button>
        </div>
      )}

      {isSelected && children && (
        <div className="px-3 pb-3 animate-in slide-in-from-top-1 duration-200 cursor-default">
          {children}
        </div>
      )}
    </div>
  )
}

/* ─── Threshold Control Component ─── */

const ThresholdControl = ({ label, goodKey, badKey, values, onChange, t }) => {
  const good = Number(values[goodKey])
  const bad = Number(values[badKey])

  const handleGoodChange = (e) => {
    const val = Number(e.target.value)
    onChange(goodKey, val)
    // Ensure good is always > bad (at least 1dB diff)
    if (val <= bad) {
       onChange(badKey, val - 1)
    }
  }

  const handleBadChange = (e) => {
    const val = Number(e.target.value)
    onChange(badKey, val)
    // Ensure bad is always < good (at least 1dB diff)
    if (val >= good) {
       onChange(goodKey, val + 1)
    }
  }
  
  return (
    <div className="flex flex-col gap-4">
       <div className="flex items-center justify-between">
          <SectionLabel>{label}</SectionLabel>
       </div>

       {/* Visual Interactive Bar */}
       <div className="relative pt-8 pb-6 px-2">
          {/* Bar Background: Green -> Yellow -> Red */}
          <div className="h-2 w-full rounded-full flex overflow-hidden">
             {/* Normal Zone (>= good) */}
             <div className="flex-1 bg-emerald-400 relative group/bar-normal">
               <div className="absolute inset-0 bg-emerald-300 opacity-0 group-hover/bar-normal:opacity-100 transition-opacity" />
             </div>
             
             {/* Warning Zone */}
             <div className="flex-1 bg-yellow-400 relative group/bar-warn">
               <div className="absolute inset-0 bg-yellow-300 opacity-0 group-hover/bar-warn:opacity-100 transition-opacity" />
             </div>

             {/* Critical Zone (< bad) */}
             <div className="flex-1 bg-rose-400 relative group/bar-crit">
               <div className="absolute inset-0 bg-rose-300 opacity-0 group-hover/bar-crit:opacity-100 transition-opacity" />
             </div>
          </div>

          {/* Labels BELOW the bar */}
          <div className="absolute top-12 left-0 w-full flex text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500">
             <div className="flex-1 text-center text-emerald-600 dark:text-emerald-400">{t('Good')}</div>
             <div className="flex-1 text-center text-yellow-600 dark:text-yellow-400">{t('Warning')}</div>
             <div className="flex-1 text-center text-rose-500 dark:text-rose-400">{t('Critical')}</div>
          </div>

          {/* Marker 1: The "Good" Threshold (Separates Green/Yellow) */}
          <div className="absolute top-0 bottom-6 left-1/3 -ml-px flex flex-col justify-end items-center z-10 pointer-events-none transform -translate-x-1/2">
             {/* Label Value Floating Above - Centered on tick */}
             <div className="mb-1 text-[10px] font-black text-emerald-600 dark:text-emerald-400 bg-white dark:bg-slate-900 px-1.5 py-px rounded shadow-sm border border-emerald-100 dark:border-emerald-500/30">
                {good}
             </div>
             {/* The Tick Line */}
             <div className="w-0.5 h-3 bg-slate-300 dark:bg-slate-600 z-10 rounded-full"></div>
          </div>

          {/* Marker 2: The "Bad" Threshold (Separates Yellow/Red) */}
          <div className="absolute top-0 bottom-6 left-2/3 -ml-px flex flex-col justify-end items-center z-10 pointer-events-none transform -translate-x-1/2">
             {/* Label Value Floating Above - Centered on tick */}
             <div className="mb-1 text-[10px] font-black text-rose-500 dark:text-rose-400 bg-white dark:bg-slate-900 px-1.5 py-px rounded shadow-sm border border-rose-100 dark:border-rose-500/30">
                {bad}
             </div>
             {/* The Tick Line */}
             <div className="w-0.5 h-3 bg-slate-300 dark:bg-slate-600 z-10 rounded-full"></div>
          </div>
       </div>

       {/* Input Controls - Smaller Pills */}
       <div className="flex items-center justify-center gap-6">
          {/* Normal Input */}
          <div className="relative group w-28">
             <label className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase mb-1 block flex items-center gap-1.5 whitespace-nowrap">
                <div className="w-1.5 h-1.5 rounded-full bg-emerald-400"></div>
                {t('Normal limit')}
             </label>
             <div className="relative">
                <input
                   type="number"
                   step="0.5"
                   value={good}
                   onChange={handleGoodChange}
                   className="w-full h-8 pl-8 pr-8 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 
                              text-[11px] font-bold text-slate-700 dark:text-slate-200
                              focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 outline-none transition-all
                              [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                   placeholder="-25"
                />
                <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[13px] font-black text-emerald-500">&ge;</span>
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] font-bold text-slate-400 pointer-events-none">dBm</span>
             </div>
          </div>

          {/* Critical Input */}
          <div className="relative group w-28">
             <label className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase mb-1 block flex items-center gap-1.5 whitespace-nowrap">
                <div className="w-1.5 h-1.5 rounded-full bg-rose-400"></div>
                {t('Critical limit')}
             </label>
             <div className="relative">
                <input
                   type="number"
                   step="0.5"
                   value={bad}
                   onChange={handleBadChange}
                   className="w-full h-8 pl-8 pr-8 rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40
                              text-[11px] font-bold text-slate-700 dark:text-slate-200 
                              focus:bg-white dark:focus:bg-slate-800 focus:border-rose-400 focus:ring-2 focus:ring-rose-500/20 outline-none transition-all
                              [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                   placeholder="-28"
                />
                <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[13px] font-black text-rose-500">&lt;</span>
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] font-bold text-slate-400 pointer-events-none">dBm</span>
             </div>
          </div>
       </div>
    </div>
  )
}

/* ─── Main panel ─── */

export const SettingsPanel = ({
  olts,
  vendorProfiles,
  loading,
  vendorLoading,
  error,
  vendorError,
  actionError,
  actionMessage,
  onCreateOlt,
  onUpdateOlt,
  onDeleteOlt,
  onRunDiscovery,
  onRunPolling,
  onRefreshPower,
  actionBusy,
  snmpStatus = {},
  oltHealthById = {}
}) => {
  const { t } = useTranslation()
  const [showAddForm, setShowAddForm] = useState(false)
  const [selectedOltId, setSelectedOltId] = useState(null)
  const [form, setForm] = useState(() => buildInitialForm(vendorProfiles))
  const [editForm, setEditForm] = useState(null)
  const [localError, setLocalError] = useState('')
  const addNameRef = useRef(null)
  const [cardTab, setCardTab] = useState('device')
  const [thresholdForm, setThresholdForm] = useState(null)

  const vendorOptions = useMemo(() => {
    return [...new Set((vendorProfiles || []).map((item) => item?.vendor).filter(Boolean))]
  }, [vendorProfiles])

  const modelOptionsForVendor = useCallback((vendor) => {
    if (!vendor) return []
    return (vendorProfiles || []).filter((item) => item.vendor === vendor)
  }, [vendorProfiles])

  const modelOptions = useMemo(() => modelOptionsForVendor(form.vendor), [modelOptionsForVendor, form.vendor])
  const editModelOptions = useMemo(() => modelOptionsForVendor(editForm?.vendor), [modelOptionsForVendor, editForm?.vendor])

  // Selected OLT object (for dirty detection)
  const selectedOlt = useMemo(() => {
    if (!selectedOltId) return null
    return olts.find((item) => String(item.id) === String(selectedOltId)) || null
  }, [olts, selectedOltId])

  // Dirty detection — Save only matters when something changed
  const dirty = useMemo(() => isFormDirty(editForm, selectedOlt, vendorProfiles), [editForm, selectedOlt, vendorProfiles])

  // Reset card tab + threshold form when selection changes
  useEffect(() => {
    setCardTab('device')
    if (!selectedOltId) { setThresholdForm(null); return }
    setThresholdForm(getOltThresholds(selectedOltId))
  }, [selectedOltId])

  // Clear selection when OLTs disappear
  useEffect(() => {
    if (!olts.length) { setSelectedOltId(null); return }
    if (selectedOltId) {
      const exists = olts.some((item) => String(item.id) === String(selectedOltId))
      if (exists) return
    }
  }, [olts, selectedOltId])

  // Sync edit form when selection changes
  useEffect(() => {
    if (!selectedOltId) { setEditForm(null); return }
    const olt = olts.find((item) => String(item.id) === String(selectedOltId))
    if (olt) setEditForm(buildEditForm(olt, vendorProfiles))
    else setEditForm(null)
  }, [selectedOltId, olts, vendorProfiles])

  // Create form syncs
  useEffect(() => {
    if (!showAddForm) {
      setForm(buildInitialForm(vendorProfiles))
      setLocalError('')
      return
    }
    if (!form.vendor && vendorOptions.length > 0) {
      setForm((prev) => ({ ...prev, vendor: vendorOptions[0] }))
    }
  }, [showAddForm, vendorProfiles, vendorOptions, form.vendor])

  useEffect(() => {
    if (!showAddForm) return
    if (!modelOptions.length) {
      setForm((prev) => ({ ...prev, vendor_profile: '' }))
      return
    }
    const exists = modelOptions.some((item) => String(item.id) === String(form.vendor_profile))
    if (!exists) {
      setForm((prev) => ({ ...prev, vendor_profile: String(modelOptions[0].id) }))
    }
  }, [showAddForm, modelOptions, form.vendor_profile])

  // Edit form model sync
  useEffect(() => {
    if (!editForm) return
    if (!editModelOptions.length) {
      setEditForm((prev) => prev ? { ...prev, vendor_profile: '' } : prev)
      return
    }
    const exists = editModelOptions.some((item) => String(item.id) === String(editForm.vendor_profile))
    if (!exists) {
      setEditForm((prev) => prev ? { ...prev, vendor_profile: String(editModelOptions[0].id) } : prev)
    }
  }, [editModelOptions, editForm?.vendor_profile])

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))
  const setEditField = (key, value) => setEditForm((prev) => prev ? ({ ...prev, [key]: value }) : prev)

  const handleVendorChange = (nextVendor) => {
    const nextModel = (vendorProfiles || []).find((item) => item.vendor === nextVendor)
    setForm((prev) => ({
      ...prev,
      vendor: nextVendor,
      vendor_profile: nextModel?.id ? String(nextModel.id) : ''
    }))
  }

  const handleEditVendorChange = (nextVendor) => {
    const nextModel = (vendorProfiles || []).find((item) => item.vendor === nextVendor)
    setEditForm((prev) => prev ? ({
      ...prev,
      vendor: nextVendor,
      vendor_profile: nextModel?.id ? String(nextModel.id) : ''
    }) : prev)
  }

  const handleCreate = async () => {
    const payload = {
      name: String(form.name || '').trim().slice(0, MAX_OLT_NAME),
      ip_address: String(form.ip_address || '').trim(),
      vendor_profile: Number(form.vendor_profile),
      protocol: 'snmp',
      snmp_community: String(form.snmp_community || '').trim(),
      snmp_port: Number(form.snmp_port || 161),
      snmp_version: 'v2c',
      discovery_enabled: true,
      polling_enabled: true,
      discovery_interval_minutes: Math.round((parseDuration(form.discovery_interval) || 14400) / 60),
      polling_interval_seconds: parseDuration(form.polling_interval) || 300,
      power_interval_seconds: parseDuration(form.power_interval) || 300
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalError(t('Required fields are missing'))
      return
    }

    setLocalError('')
    const created = await onCreateOlt?.(payload)
    if (created?.id) {
      setShowAddForm(false)
      setForm(buildInitialForm(vendorProfiles))
    }
  }

  const handleUpdate = async () => {
    if (!selectedOltId || !editForm) return

    const payload = {
      name: String(editForm.name || '').trim().slice(0, MAX_OLT_NAME),
      ip_address: String(editForm.ip_address || '').trim(),
      vendor_profile: Number(editForm.vendor_profile),
      snmp_community: String(editForm.snmp_community || '').trim(),
      snmp_port: Number(editForm.snmp_port || 161),
      discovery_interval_minutes: Math.round((parseDuration(editForm.discovery_interval) || 14400) / 60),
      polling_interval_seconds: parseDuration(editForm.polling_interval) || 300,
      power_interval_seconds: parseDuration(editForm.power_interval) || 300,
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalError(t('Required fields are missing'))
      return
    }

    setLocalError('')
    await onUpdateOlt?.(selectedOltId, payload)
  }

  const handleDelete = async (oltId) => {
    const confirmed = window.confirm(t('Do you want to remove this OLT?'))
    if (!confirmed) return
    const removed = await onDeleteOlt?.(oltId)
    if (removed) setSelectedOltId(null)
  }

  const handleDiscovery = async (oltId) => {
    await onRunDiscovery?.(oltId)
  }

  const handleDiscard = () => {
    if (!selectedOlt) return
    setEditForm(buildEditForm(selectedOlt, vendorProfiles))
  }

  const setThresholdField = (key, rawValue) => {
    const numValue = rawValue === '' || rawValue === '-' ? rawValue : parseFloat(rawValue)
    setThresholdForm((prev) => {
      if (!prev) return prev
      const next = { ...prev, [key]: typeof numValue === 'number' && Number.isFinite(numValue) ? numValue : rawValue }
      // Persist only when all values are valid numbers
      const allValid = ['onu_rx_good', 'onu_rx_bad', 'olt_rx_good', 'olt_rx_bad'].every(
        (k) => typeof next[k] === 'number' && Number.isFinite(next[k])
      )
      if (allValid && selectedOltId) saveOltThresholds(selectedOltId, next)
      return next
    })
  }

  const resetThresholds = () => {
    if (!selectedOltId) return
    clearOltThresholds(selectedOltId)
    setThresholdForm({ ...DEFAULT_THRESHOLDS })
  }

  const isOverride = selectedOltId ? hasOltOverride(selectedOltId) : false

  const createBusy = Boolean(actionBusy?.create)
  const updateBusy = Boolean(actionBusy?.[`update:${selectedOltId}`])
  const anyError = error || vendorError || actionError || localError

  return (
    <div className="w-full h-full overflow-y-auto custom-scrollbar">
      <div className="max-w-2xl mx-auto px-6 lg:px-10 py-8 space-y-6 animate-in fade-in duration-500">

        {anyError && (
          <div className="flex items-center gap-2.5 px-3.5 py-2 animate-in fade-in duration-500">
            <AlertCircle className="w-3.5 h-3.5 text-rose-400 dark:text-rose-500 flex-shrink-0" />
            <p className="text-[10px] font-bold text-rose-400 dark:text-rose-500 uppercase tracking-wider">{anyError}</p>
          </div>
        )}

        {actionMessage && (
          <div className="flex items-center gap-2.5 px-3.5 py-2 animate-in fade-in duration-500">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 dark:text-emerald-500 flex-shrink-0" />
            <p className="text-[10px] font-bold text-emerald-400 dark:text-emerald-500 uppercase tracking-wider">{actionMessage}</p>
          </div>
        )}

        {/* Header row */}
        <div className="w-full flex items-center justify-between">
          <p className="text-[11px] font-medium text-slate-300 dark:text-slate-600 uppercase tracking-widest select-none">
            {t('Add an OLT to start')}
          </p>
          <button
            type="button"
            onClick={() => {
              if (showAddForm) {
                addNameRef.current?.focus()
              } else {
                setShowAddForm(true)
              }
            }}
            className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20 hover:shadow-emerald-600/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            title={t('Add OLT')}
          >
            <Plus className="w-5 h-5" />
          </button>
        </div>

        {/* ─── Create OLT form ─── */}
        {showAddForm && (
          <div className="w-full rounded-[14px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-sm px-4 py-4 space-y-4 animate-in fade-in slide-in-from-top-3 duration-300">
            <div className="space-y-2.5">
              <SectionLabel>{t('Device')}</SectionLabel>
              <div className="grid grid-cols-8 gap-3">
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('OLT name')}</FieldLabel>
                  <FieldInput
                    ref={addNameRef}
                    value={form.name}
                    onChange={(e) => setField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                    maxLength={MAX_OLT_NAME}
                    placeholder="OLT-01"
                  />
                </div>
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('Vendor')}</FieldLabel>
                  <FieldSelect
                    value={form.vendor}
                    onChange={(e) => handleVendorChange(e.target.value)}
                    disabled={vendorLoading || !vendorOptions.length}
                  >
                    {vendorOptions.map((vendor) => (
                      <option key={vendor} value={vendor}>{String(vendor).toUpperCase()}</option>
                    ))}
                  </FieldSelect>
                </div>
                <div className="col-span-2 flex flex-col gap-1.5">
                  <FieldLabel>{t('Model')}</FieldLabel>
                  <FieldSelect
                    value={form.vendor_profile}
                    onChange={(e) => setField('vendor_profile', e.target.value)}
                    disabled={vendorLoading || !modelOptions.length}
                  >
                    {modelOptions.map((item) => (
                      <option key={item.id} value={item.id}>{item.model_name}</option>
                    ))}
                  </FieldSelect>
                </div>
              </div>
            </div>

            <div className="space-y-2.5">
              <SectionLabel>{t('Connection')}</SectionLabel>
              <div className="grid grid-cols-8 gap-3">
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('IP')}</FieldLabel>
                  <FieldInput
                    value={form.ip_address}
                    onChange={(e) => setField('ip_address', e.target.value)}
                    placeholder="10.0.0.1"
                  />
                </div>
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('SNMP community')}</FieldLabel>
                  <FieldInput
                    value={form.snmp_community}
                    onChange={(e) => setField('snmp_community', e.target.value)}
                    placeholder="public"
                  />
                </div>
                <div className="col-span-2 flex flex-col gap-1.5">
                  <FieldLabel>{t('SNMP port')}</FieldLabel>
                  <FieldInput
                    type="number"
                    min={1}
                    max={65535}
                    value={form.snmp_port}
                    onChange={(e) => setField('snmp_port', e.target.value)}
                    placeholder="161"
                  />
                </div>
              </div>
            </div>

            <div className="space-y-2.5">
              <SectionLabel>{t('Intervals')}</SectionLabel>
              <div className="grid grid-cols-8 gap-3">
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('ONU discovery')}</FieldLabel>
                  <FieldInput value={form.discovery_interval} onChange={(e) => setField('discovery_interval', e.target.value)} placeholder="4h" />
                </div>
                <div className="col-span-3 flex flex-col gap-1.5">
                  <FieldLabel>{t('Status collection')}</FieldLabel>
                  <FieldInput value={form.polling_interval} onChange={(e) => setField('polling_interval', e.target.value)} placeholder="5m" />
                </div>
                <div className="col-span-2 flex flex-col gap-1.5">
                  <FieldLabel>{t('Power collection')}</FieldLabel>
                  <FieldInput value={form.power_interval} onChange={(e) => setField('power_interval', e.target.value)} placeholder="5m" />
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowAddForm(false)}
                className="h-8 px-3.5 rounded-[8px] border border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 text-[10px] font-black uppercase tracking-wider transition-all whitespace-nowrap"
              >
                {t('Cancel')}
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={createBusy}
                className="h-8 px-4 rounded-[8px] border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 text-[10px] font-black uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-all whitespace-nowrap"
              >
                {createBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                {t('Save')}
              </button>
            </div>
          </div>
        )}

        {/* ─── OLT list ─── */}
        <div className="space-y-2.5">
          {loading && !olts.length && (
            <div className="flex items-center justify-center py-20">
              <RefreshCcw className="w-5 h-5 text-slate-300 dark:text-slate-600 animate-spin" />
            </div>
          )}

          {olts.map((olt) => {
            const isSelected = String(selectedOltId) === String(olt.id)
            const health = getOltHealth(olt, snmpStatus, oltHealthById)
            const vp = vendorProfiles?.find(p => String(p.id) === String(olt.vendor_profile))
            const resolvedVendor = olt.vendor || olt.vendor_display || vp?.vendor || 'Unknown'
            const discoveryBusy = Boolean(actionBusy?.[`discovery:${olt.id}`])
            const pollingBusy = Boolean(actionBusy?.[`polling:${olt.id}`])
            const powerBusy = Boolean(actionBusy?.[`power:${olt.id}`])
            const deleteBusy = Boolean(actionBusy?.[`delete:${olt.id}`])
            const localUpdateBusy = Boolean(actionBusy?.[`update:${olt.id}`])
            const snmpBadge = getSnmpBadge(olt, snmpStatus, t)

            return (
              <OltCard
                key={olt.id}
                olt={olt}
                isSelected={isSelected}
                health={health}
                onSelect={setSelectedOltId}
                onDelete={handleDelete}
                deleteBusy={deleteBusy}
                resolvedVendor={resolvedVendor}
                t={t}
              >
                {isSelected && editForm && (
                  <div className="pt-3 space-y-4 border-t border-slate-100 dark:border-slate-800/50">

                    {/* ── Tab bar (Segmented Toggle) ── */}
                    <div className="flex justify-start mb-4">
                      <div className="inline-flex rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
                        {['device', 'intervals', 'thresholds'].map((tab) => (
                          <button
                            key={tab}
                            type="button"
                            onClick={() => setCardTab(tab)}
                            className={`px-4 py-1.5 rounded-md text-[10px] font-black uppercase tracking-wider transition-all ${
                              cardTab === tab
                                ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm'
                                : 'text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300'
                            }`}
                          >
                            {t(tab === 'device' ? 'Device' : tab === 'intervals' ? 'Intervals' : 'Thresholds')}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* ── Fixed height content area ── */}
                    <div className="h-[170px] w-full overflow-y-auto custom-scrollbar relative">

                    {/* ── TAB: Device + Connection ── */}
                    {cardTab === 'device' && (
                      <div className="space-y-3 animate-in fade-in slide-in-from-left-1 duration-200">
                        <div className="space-y-1.5">
                          <SectionLabel>{t('Device')}</SectionLabel>
                          <div className="grid grid-cols-6 gap-3">
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('OLT name')}</FieldLabel>
                              <FieldInput
                                value={editForm.name}
                                onChange={(e) => setEditField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                                maxLength={MAX_OLT_NAME}
                                placeholder="OLT-01"
                              />
                            </div>
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('Vendor')}</FieldLabel>
                              <FieldSelect
                                value={editForm.vendor}
                                onChange={(e) => handleEditVendorChange(e.target.value)}
                                disabled={vendorLoading || !vendorOptions.length}
                              >
                                {vendorOptions.map((vendor) => (
                                  <option key={vendor} value={vendor}>{String(vendor).toUpperCase()}</option>
                                ))}
                              </FieldSelect>
                            </div>
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('Model')}</FieldLabel>
                              <FieldSelect
                                value={editForm.vendor_profile}
                                onChange={(e) => setEditField('vendor_profile', e.target.value)}
                                disabled={vendorLoading || !editModelOptions.length}
                              >
                                {editModelOptions.map((item) => (
                                  <option key={item.id} value={item.id}>{item.model_name}</option>
                                ))}
                              </FieldSelect>
                            </div>
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <SectionLabel>{t('Connection')}</SectionLabel>
                          <div className="grid grid-cols-6 gap-3">
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('IP')}</FieldLabel>
                              <FieldInput
                                value={editForm.ip_address}
                                onChange={(e) => setEditField('ip_address', e.target.value)}
                                placeholder="10.0.0.1"
                              />
                            </div>
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('SNMP community')}</FieldLabel>
                              <FieldInput
                                value={editForm.snmp_community}
                                onChange={(e) => setEditField('snmp_community', e.target.value)}
                                placeholder="public"
                              />
                            </div>
                            <div className="col-span-2 flex flex-col gap-1">
                              <FieldLabel>{t('SNMP port')}</FieldLabel>
                              <FieldInput
                                type="number"
                                min={1}
                                max={65535}
                                value={editForm.snmp_port}
                                onChange={(e) => setEditField('snmp_port', e.target.value)}
                                placeholder="161"
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── TAB: Intervals ── */}
                    {cardTab === 'intervals' && (
                      <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-300 px-1 pt-2">
                        <div className="space-y-3">
                          <div className="flex justify-center">
                            <SectionLabel>{t('Timers')}</SectionLabel>
                          </div>
                          <div className="grid grid-cols-3 gap-3">
                            <div className="group flex flex-col items-center gap-1.5">
                              <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase overflow-hidden text-ellipsis whitespace-nowrap transition-colors group-focus-within:text-emerald-500/80 text-center w-full">
                                {t('ONU discovery')}
                              </label>
                              <FieldInput
                                className="text-center h-8 !w-20 text-xs font-bold bg-slate-50 dark:bg-slate-800/40 border-slate-100 dark:border-slate-700/50 focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-500/10 transition-all rounded-lg shadow-sm placeholder:font-medium placeholder:text-slate-300"
                                value={editForm.discovery_interval}
                                onChange={(e) => setEditField('discovery_interval', e.target.value)}
                                placeholder="4h"
                              />
                            </div>
                            <div className="group flex flex-col items-center gap-1.5">
                              <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase overflow-hidden text-ellipsis whitespace-nowrap transition-colors group-focus-within:text-emerald-500/80 text-center w-full">
                                {t('Status collection')}
                              </label>
                              <FieldInput
                                className="text-center h-8 !w-20 text-xs font-bold bg-slate-50 dark:bg-slate-800/40 border-slate-100 dark:border-slate-700/50 focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-500/10 transition-all rounded-lg shadow-sm placeholder:font-medium placeholder:text-slate-300"
                                value={editForm.polling_interval}
                                onChange={(e) => setEditField('polling_interval', e.target.value)}
                                placeholder="5m"
                              />
                            </div>
                            <div className="group flex flex-col items-center gap-1.5">
                              <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase overflow-hidden text-ellipsis whitespace-nowrap transition-colors group-focus-within:text-emerald-500/80 text-center w-full">
                                {t('Power collection')}
                              </label>
                              <FieldInput
                                className="text-center h-8 !w-20 text-xs font-bold bg-slate-50 dark:bg-slate-800/40 border-slate-100 dark:border-slate-700/50 focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-500/10 transition-all rounded-lg shadow-sm placeholder:font-medium placeholder:text-slate-300"
                                value={editForm.power_interval}
                                onChange={(e) => setEditField('power_interval', e.target.value)}
                                placeholder="5m"
                              />
                            </div>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-3 pt-1">
                          <div className="flex justify-center">
                            <button
                              type="button"
                              onClick={() => handleDiscovery(olt.id)}
                              disabled={discoveryBusy}
                              className="h-8 w-24 rounded-lg border border-slate-200/60 dark:border-slate-700/60 bg-white dark:bg-slate-800/40 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 hover:border-emerald-200 dark:hover:border-emerald-500/30 text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 flex items-center justify-center text-[9px] font-black uppercase tracking-[0.10em] disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm hover:shadow-emerald-500/10 active:scale-[0.98]"
                            >
                              {discoveryBusy ? <RefreshCcw className="w-3 h-3 animate-spin text-emerald-500" /> : <span>{t('Execute')}</span>}
                            </button>
                          </div>
                          <div className="flex justify-center">
                            <button
                              type="button"
                              onClick={() => onRunPolling?.(olt.id)}
                              disabled={pollingBusy}
                              className="h-8 w-24 rounded-lg border border-slate-200/60 dark:border-slate-700/60 bg-white dark:bg-slate-800/40 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 hover:border-emerald-200 dark:hover:border-emerald-500/30 text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 flex items-center justify-center text-[9px] font-black uppercase tracking-[0.10em] disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm hover:shadow-emerald-500/10 active:scale-[0.98]"
                            >
                              {pollingBusy ? <RefreshCcw className="w-3 h-3 animate-spin text-emerald-500" /> : <span>{t('Execute')}</span>}
                            </button>
                          </div>
                          <div className="flex justify-center">
                            <button
                              type="button"
                              onClick={() => onRefreshPower?.(olt.id)}
                              disabled={powerBusy}
                              className="h-8 w-24 rounded-lg border border-slate-200/60 dark:border-slate-700/60 bg-white dark:bg-slate-800/40 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 hover:border-emerald-200 dark:hover:border-emerald-500/30 text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 flex items-center justify-center text-[9px] font-black uppercase tracking-[0.10em] disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm hover:shadow-emerald-500/10 active:scale-[0.98]"
                            >
                              {powerBusy ? <RefreshCcw className="w-3 h-3 animate-spin text-emerald-500" /> : <span>{t('Execute')}</span>}
                            </button>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── TAB: Thresholds ── */}
                    {cardTab === 'thresholds' && thresholdForm && (
                      <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-200">
                         {/* Two columns: ONU RX | OLT RX */}
                         <div className="grid grid-cols-2 gap-4 relative">
                            {/* Vertical Divider */}
                            <div className="absolute left-1/2 top-4 bottom-0 w-px bg-slate-100 dark:bg-slate-800/50 -translate-x-1/2"></div>
                            
                            <ThresholdControl 
                                label={t('ONU RX Power')}
                                goodKey="onu_rx_good"
                                badKey="onu_rx_bad"
                                values={thresholdForm}
                                onChange={setThresholdField}
                                t={t}
                            />
                            <ThresholdControl 
                                label={t('OLT RX Power')}
                                goodKey="olt_rx_good"
                                badKey="olt_rx_bad"
                                values={thresholdForm}
                                onChange={setThresholdField}
                                t={t}
                            />
                         </div>

                        {/* Reset & Instructions (optional, keeps it clean) */}
                        {isOverride && null}
                      </div>
                    )}
                    </div>{/* End fixed height */}

                    {/* ── Action bar ── */}
                    <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-800/30">
                      {/* Left: info */}
                      <div className="flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-slate-300 dark:text-slate-600" />
                        <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500">
                          {t('Last discovery')}: {formatRelativeTime(olt.last_discovery_at, t)}
                        </span>
                      </div>

                      {/* Right: save actions */}
                      <div className="flex items-center gap-2">
                        {/* Save — only visible when form is dirty */}
                        {dirty && (
                          <div className="flex items-center gap-1.5 animate-in fade-in duration-200">
                            <button
                              type="button"
                              onClick={handleDiscard}
                              className="h-8 px-3 rounded-[8px] border border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 text-[10px] font-black uppercase tracking-wider transition-all whitespace-nowrap"
                            >
                              {t('Cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={handleUpdate}
                              disabled={localUpdateBusy}
                              className="h-8 px-4 rounded-[8px] bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/20 text-[10px] font-black uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-all whitespace-nowrap"
                            >
                              {localUpdateBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                              {t('Save')}
                            </button>
                          </div>
                        )}
                      </div>
                    </div>

                  </div>
                )}
              </OltCard>
            )
          })}

          {!olts.length && !loading && (
            <div className="flex flex-col items-center justify-center py-20 gap-2">
              <p className="text-[12px] font-black uppercase tracking-[0.2em] text-slate-300 dark:text-slate-600">
                {t('No OLTs registered')}
              </p>
            </div>
          )}
        </div>

      </div>
    </div>
  )
}
