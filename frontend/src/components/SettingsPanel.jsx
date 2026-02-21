import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Trash2, RefreshCcw, Check, AlertCircle, CheckCircle2, ChevronDown, Server, Clock } from 'lucide-react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useTranslation } from 'react-i18next'
import { DEFAULT_THRESHOLDS, getOltThresholds, saveOltThresholds, clearOltThresholds, hasOltOverride } from '../utils/powerThresholds'
import { HEALTH_STYLES } from '../utils/healthStyles'

const MAX_OLT_NAME = 12
const SELECTED_OLT_STORAGE_KEY = 'varuna.settings.selectedOltId'

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
    discovery_interval: '5h',
    polling_interval: '5m',
    power_interval: '1d'
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
    className={`h-8 w-full px-2.5 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200 placeholder:text-slate-300 dark:placeholder:text-slate-600
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all
      [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none ${className}`}
  />
))

const FieldSelect = ({ value, onChange, options = [], disabled, className = '' }) => {
  const triggerRef = useRef(null)
  const [triggerWidth, setTriggerWidth] = useState(0)

  const selectedLabel = options.find((o) => String(o.value) === String(value))?.label || ''

  return (
    <DropdownMenu.Root onOpenChange={(open) => {
      if (open && triggerRef.current) setTriggerWidth(triggerRef.current.offsetWidth)
    }}>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button
          ref={triggerRef}
          className={`group h-8 w-full px-2.5 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
            text-[11px] font-semibold text-slate-800 dark:text-slate-200
            focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all
            disabled:opacity-50 disabled:cursor-not-allowed
            flex items-center justify-center relative ${className}`}
        >
          <span className="truncate text-center flex-1">{selectedLabel}</span>
          <ChevronDown className="w-3 h-3 shrink-0 ml-1 transition-transform duration-200 group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          className="bg-white dark:bg-slate-900 rounded-xl p-1 shadow-xl border border-slate-200 dark:border-slate-700/50 z-[220] animate-in fade-in zoom-in-95 duration-150"
          sideOffset={4}
          style={triggerWidth ? { width: triggerWidth } : undefined}
        >
          {options.map((option) => (
            <DropdownMenu.Item
              key={option.value}
              onSelect={() => onChange(String(option.value))}
              className={`
                relative flex items-center justify-center px-2 py-1.5 rounded-lg outline-none cursor-pointer transition-colors
                ${String(value) === String(option.value)
                  ? 'bg-slate-50 dark:bg-slate-800/60'
                  : 'hover:bg-slate-50 dark:hover:bg-slate-800/40'}
              `}
            >
              <span className="absolute left-2 h-4 w-4 flex items-center justify-center">
                {String(value) === String(option.value) && (
                  <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                )}
              </span>
              <span
                className={`
                  text-[10px] font-black uppercase tracking-[0.04em] text-center
                  ${String(value) === String(option.value) ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-700 dark:text-slate-200'}
                `}
              >
                {option.label}
              </span>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const SectionLabel = ({ children }) => (
  <span className="text-[9px] font-black uppercase tracking-[0.15em] text-slate-300 dark:text-slate-600 select-none">{children}</span>
)

const getOltHealth = (olt, snmpStatuses, oltHealthById) => {
  const derived = oltHealthById?.[String(olt.id)] || oltHealthById?.[olt.id]
  if (derived?.state && HEALTH_STYLES[derived.state]) return HEALTH_STYLES[derived.state]
  const st = snmpStatuses?.[olt.id]
  if (st?.status === 'unreachable') return HEALTH_STYLES.gray
  if (!st || st.status === 'pending') return HEALTH_STYLES.neutral
  return HEALTH_STYLES.green
}

const getSnmpBadge = (olt, snmpStatuses, t) => {
  const st = snmpStatuses?.[olt.id]
  if (!st || st.status === 'pending') return { label: t('Checking'), color: 'bg-slate-100 dark:bg-slate-800 text-slate-400 dark:text-slate-500', dot: 'bg-slate-300 dark:bg-slate-600' }
  if (st.status === 'unreachable') return { label: t('Unreachable'), color: 'bg-rose-50 dark:bg-rose-500/10 text-rose-500 dark:text-rose-400', dot: 'bg-rose-400 dark:bg-rose-500' }
  return { label: t('Reachable'), color: 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400', dot: 'bg-emerald-400 dark:bg-emerald-500' }
}

/* ─── OLT Card header ─── */

const OltCard = ({ olt, isSelected, health, onSelect, onDeleteClick, deleteBusy, resolvedVendor, t, children }) => {
  const total = Number(olt.onu_count || 0)
  const online = Number(olt.online_count || 0)
  const offline = Number(olt.offline_count || 0)
  const hasOnus = total > 0

  return (
    <div className={`
      w-full transition-all duration-300 bg-white dark:bg-slate-900
      rounded-xl border relative
      ${isSelected ? health.borderActive : health.borderIdle}
    `}>
      <div
        onClick={() => onSelect(isSelected ? null : String(olt.id))}
        className="group/node relative flex items-center gap-2.5 px-3 py-2.5 cursor-pointer select-none"
      >
        <div className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full transition-all duration-300 ${
          isSelected ? health.accentActive : health.accentIdle
        }`} />

        <div className={`flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-300 ${
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
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-black text-slate-700 dark:text-slate-300 tabular-nums">{total} <span className="text-[9px] uppercase font-bold text-slate-400 dark:text-slate-500">ONUs</span></span>
            <span className="w-px h-3 bg-slate-200 dark:bg-slate-700" />
            <div className="flex items-center gap-1">
              <span className="text-[11px] font-black text-emerald-600 dark:text-emerald-400 tabular-nums">{online}</span>
              <span className="text-[9px] font-bold text-slate-300 dark:text-slate-600">/</span>
              <span className="text-[11px] font-black text-rose-500 dark:text-rose-400 tabular-nums">{offline}</span>
            </div>
          </div>
        </div>

        <div className="mr-2" />

        {isSelected && onDeleteClick && (
          <button
            onClick={(e) => { e.stopPropagation(); onDeleteClick() }}
            disabled={deleteBusy}
            className="w-7 h-7 rounded-lg flex items-center justify-center text-rose-400 dark:text-rose-400 hover:text-rose-600 dark:hover:text-rose-300 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-all disabled:opacity-50 active:scale-95"
            title={t('Remove OLT')}
          >
            {deleteBusy ? <RefreshCcw className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
          </button>
        )}

        <div className={`transition-transform duration-300 ${
          isSelected ? `rotate-180 ${health.chevronOpen}` : 'text-slate-300 group-hover/node:text-slate-400'
        }`}>
          <ChevronDown className="w-3 h-3" />
        </div>
      </div>

      {isSelected && children && (
        <div className="border-t border-slate-100 dark:border-slate-700/40">
          <div className="px-4 pb-4 animate-in slide-in-from-top-1 duration-200 cursor-default">
            {children}
          </div>
        </div>
      )}
    </div>
  )
}

/* ─── Threshold Control Component ─── */

const THRESHOLD_GAP = 3 // dB gap between Normal→Warning and Warning→Critical

const ThresholdControl = ({ label, goodKey, badKey, values, onChange, t }) => {
  const goodRaw = values[goodKey]
  const goodNum = typeof goodRaw === 'number' ? goodRaw : parseFloat(goodRaw)
  const goodValid = Number.isFinite(goodNum)

  // Auto-derived zones
  const warningAt = goodValid ? goodNum - THRESHOLD_GAP : null
  const criticalAt = goodValid ? goodNum - (THRESHOLD_GAP * 2) : null

  const goodDisplay = typeof goodRaw === 'string' ? goodRaw : (goodValid ? String(goodNum) : '')

  const handleChange = (e) => {
    const raw = e.target.value
    if (raw === '' || raw === '-' || raw === '-.' || raw === '.') {
      onChange(goodKey, raw)
      return
    }
    const num = parseFloat(raw)
    if (Number.isFinite(num)) {
      onChange(goodKey, num)
      // Auto-set bad = normal - THRESHOLD_GAP
      onChange(badKey, num - THRESHOLD_GAP)
    } else {
      onChange(goodKey, raw)
    }
  }

  const handleBlur = () => {
    if (!goodValid) {
      onChange(goodKey, -25)
      onChange(badKey, -25 - THRESHOLD_GAP)
    }
  }
  
  return (
    <div className="flex flex-col gap-3">
       <div className="flex items-center justify-center">
          <SectionLabel>{label}</SectionLabel>
       </div>

       {/* Visual Power Bar */}
       <div className="relative pt-8 pb-4 px-2 select-none">
          {/* Bar */}
          <div className="h-2 w-full rounded-full flex overflow-hidden ring-1 ring-slate-200 dark:ring-slate-700">
             <div className="flex-1 bg-gradient-to-r from-emerald-400 to-emerald-500 shadow-inner" />
             <div className="flex-1 bg-gradient-to-r from-amber-300 to-amber-400 shadow-inner" />
             <div className="flex-1 bg-gradient-to-r from-rose-400 to-rose-500 shadow-inner" />
          </div>

          {/* Zone labels below bar */}
          <div className="absolute top-12 left-0 w-full flex text-[9px] font-black uppercase tracking-widest opacity-80">
             <div className="flex-1 text-center text-emerald-600 dark:text-emerald-400">{t('Good')}</div>
             <div className="flex-1 text-center text-amber-600 dark:text-amber-400">{t('Warning')}</div>
             <div className="flex-1 text-center text-rose-500 dark:text-rose-400">{t('Critical')}</div>
          </div>

          {/* Marker: Normal → Warning boundary */}
          <div className="absolute top-0 bottom-6 left-1/3 flex flex-col justify-end items-center z-10 pointer-events-none -translate-x-1/2">
             <div className="mb-0.5 text-[10px] font-black text-emerald-600 dark:text-emerald-400 bg-white dark:bg-slate-900 px-1.5 py-0.5 rounded shadow-sm border border-emerald-100 dark:border-emerald-500/30 tabular-nums">
                {goodValid ? goodNum : '—'}
             </div>
             <div className="w-[2px] h-3 bg-slate-800 dark:bg-white rounded-full opacity-20" />
          </div>

          {/* Marker: Warning → Critical boundary */}
          <div className="absolute top-0 bottom-6 left-2/3 flex flex-col justify-end items-center z-10 pointer-events-none -translate-x-1/2">
             <div className="mb-0.5 text-[10px] font-black text-rose-500 dark:text-rose-400 bg-white dark:bg-slate-900 px-1.5 py-0.5 rounded shadow-sm border border-rose-100 dark:border-rose-500/30 tabular-nums">
                {warningAt !== null ? warningAt : '—'}
             </div>
             <div className="w-[2px] h-3 bg-slate-800 dark:bg-white rounded-full opacity-20" />
          </div>
       </div>

       {/* Single Input: Normal Limit */}
       <div className="flex items-center justify-center mt-1">
          <div className="relative w-24">
             <div className="relative group">
                <input
                   type="text"
                   inputMode="decimal"
                   value={goodDisplay}
                   onChange={handleChange}
                   onBlur={handleBlur}
                   className="w-full h-7 pl-6 pr-8 rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 
                              text-[12px] font-black text-slate-700 dark:text-slate-200 text-center tracking-tight shadow-sm
                              focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-500/10 outline-none transition-all"
                   placeholder="-25"
                />
                <span className="absolute left-2.5 top-1/2 -translate-y-[45%] text-[12px] font-black text-emerald-500 group-focus-within:scale-110 transition-transform">&ge;</span>
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[9px] font-bold text-slate-400 pointer-events-none">dBm</span>
             </div>
             <label className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wide mt-1.5 flex items-center justify-center gap-1.5">
                {t('Normal limit')}
             </label>
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
  const [selectedOltId, setSelectedOltId] = useState(() => {
    try {
      if (typeof window === 'undefined') return null
      const saved = window.localStorage.getItem(SELECTED_OLT_STORAGE_KEY)
      return saved ? String(saved) : null
    } catch (_err) {
      return null
    }
  })
  const [form, setForm] = useState(() => buildInitialForm(vendorProfiles))
  const [editForm, setEditForm] = useState(null)
  const [localError, setLocalError] = useState('')
  const addNameRef = useRef(null)
  const [createCardTab, setCreateCardTab] = useState('device')
  const [editCardTab, setEditCardTab] = useState('device')
  const [thresholdForm, setThresholdForm] = useState(null)
  const [originalThresholds, setOriginalThresholds] = useState(null)
  const [createThresholdForm, setCreateThresholdForm] = useState(DEFAULT_THRESHOLDS)

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
  const formDirty = useMemo(() => isFormDirty(editForm, selectedOlt, vendorProfiles), [editForm, selectedOlt, vendorProfiles])

  const thresholdDirty = useMemo(() => {
    if (!thresholdForm || !originalThresholds) return false
    return ['onu_rx_good', 'onu_rx_bad', 'olt_rx_good', 'olt_rx_bad'].some(
      (k) => thresholdForm[k] !== originalThresholds[k]
    )
  }, [thresholdForm, originalThresholds])

  const dirty = formDirty || thresholdDirty

  // Reset card tab + threshold form when selection changes
  useEffect(() => {
    setEditCardTab('device')
    if (!selectedOltId) { setThresholdForm(null); setOriginalThresholds(null); return }
    const loaded = getOltThresholds(selectedOltId)
    setThresholdForm(loaded)
    setOriginalThresholds(loaded)
  }, [selectedOltId])

  // Clear selection when the selected OLT disappears (e.g. deleted)
  useEffect(() => {
    if (!selectedOltId) return
    if (!olts.length) { setSelectedOltId(null); return }
    const exists = olts.some((item) => String(item.id) === String(selectedOltId))
    if (!exists) setSelectedOltId(null)
  }, [olts, selectedOltId])

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      if (selectedOltId) {
        window.localStorage.setItem(SELECTED_OLT_STORAGE_KEY, String(selectedOltId))
      } else {
        window.localStorage.removeItem(SELECTED_OLT_STORAGE_KEY)
      }
    } catch (_err) {
      // noop
    }
  }, [selectedOltId])

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
      setCreateThresholdForm(DEFAULT_THRESHOLDS)
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
  
  const setCreateThresholdField = (key, rawValue) => {
    const numValue = rawValue === '' || rawValue === '-' ? rawValue : parseFloat(rawValue)
    setCreateThresholdForm((prev) => ({ 
      ...prev, 
      [key]: typeof numValue === 'number' && Number.isFinite(numValue) ? numValue : rawValue 
    }))
  }

  const handleVendorChange = (nextVendor) => {
    if (nextVendor === form.vendor) return
    const nextModel = (vendorProfiles || []).find((item) => item.vendor === nextVendor)
    setForm((prev) => ({
      ...prev,
      vendor: nextVendor,
      vendor_profile: nextModel?.id ? String(nextModel.id) : ''
    }))
  }

  const handleEditVendorChange = (nextVendor) => {
    if (nextVendor === editForm?.vendor) return
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
      discovery_interval_minutes: Math.round((parseDuration(form.discovery_interval) || 18000) / 60),
      polling_interval_seconds: parseDuration(form.polling_interval) || 300,
      power_interval_seconds: parseDuration(form.power_interval) || 86400
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalError(t('Required fields are missing'))
      setTimeout(() => setLocalError(''), 5000)
      return
    }

    setLocalError('')
    const created = await onCreateOlt?.(payload)
    if (created?.id) {
      if (createThresholdForm) {
        saveOltThresholds(created.id, createThresholdForm)
      }
      setShowAddForm(false)
      setForm(buildInitialForm(vendorProfiles))
      setCreateThresholdForm(DEFAULT_THRESHOLDS)
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
      discovery_interval_minutes: Math.round((parseDuration(editForm.discovery_interval) || 18000) / 60),
      polling_interval_seconds: parseDuration(editForm.polling_interval) || 300,
      power_interval_seconds: parseDuration(editForm.power_interval) || 86400,
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalError(t('Required fields are missing'))
      setTimeout(() => setLocalError(''), 5000)
      return
    }

    setLocalError('')
    await onUpdateOlt?.(selectedOltId, payload)
    // Persist thresholds to localStorage
    if (thresholdForm && selectedOltId) {
      const allValid = ['onu_rx_good', 'onu_rx_bad', 'olt_rx_good', 'olt_rx_bad'].every(
        (k) => typeof thresholdForm[k] === 'number' && Number.isFinite(thresholdForm[k])
      )
      if (allValid) {
        saveOltThresholds(selectedOltId, thresholdForm)
        setOriginalThresholds({ ...thresholdForm })
      }
    }
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
    if (originalThresholds) setThresholdForm({ ...originalThresholds })
  }

  const setThresholdField = (key, rawValue) => {
    const numValue = rawValue === '' || rawValue === '-' ? rawValue : parseFloat(rawValue)
    setThresholdForm((prev) => {
      if (!prev) return prev
      return { ...prev, [key]: typeof numValue === 'number' && Number.isFinite(numValue) ? numValue : rawValue }
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

        {/* Header row */}
        <div className="w-full flex items-center justify-between">
          <p className="text-[11px] font-black text-slate-300 dark:text-slate-600 uppercase tracking-widest select-none">
            {t('Add OLT')}
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
            className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-all"
            title={t('Add OLT')}
          >
            <Plus className="w-5 h-5" />
          </button>
        </div>

        {/* ─── Create OLT form ─── */}
        {showAddForm && (
          <div className="animate-in fade-in slide-in-from-top-3 duration-300">
             {/* We use the exact same card structure for creating */}
             <div className={`
                w-full bg-white dark:bg-slate-900
                rounded-xl border border-slate-200 dark:border-slate-700 shadow-sm
             `}>
                {/* Header Preview */}
                <div className="relative flex items-center gap-2.5 px-3 py-2.5 select-none border-b border-transparent">
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-emerald-500 transition-all duration-300" />
                    <div className="flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-lg bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 ring-1 ring-inset ring-emerald-600/15 dark:ring-emerald-400/25">
                       <Plus className="w-5 h-5" />
                    </div>
                    <div className="flex-1 min-w-0 flex flex-col justify-center">
                       <p className="text-[11px] font-black uppercase tracking-tight leading-none mb-0.5 text-slate-900 dark:text-white transition-all">
                          {form.name || t('New OLT')}
                       </p>
                       <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">
                              {form.ip_address || '0.0.0.0'}:{form.snmp_port || '161'}
                          </span>
                          <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
                          <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">
                              {form.vendor ? String(form.vendor).toUpperCase() : '\u2014'}
                          </span>
                          <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
                          <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">
                              {modelOptions.find(m => String(m.id) === String(form.vendor_profile))?.model_name || '\u2014'}
                          </span>
                       </div>
                    </div>
                    {/* Chevron (static open) */}
                    <div className="text-emerald-600 dark:text-emerald-400 rotate-180 transition-transform">
                       <ChevronDown className="w-3 h-3" />
                    </div>
                </div>

                {/* Body Content - Exact Same Tabs */}
                <div className="border-t border-slate-100 dark:border-slate-700/40">
                  <div className="px-4 pb-4 pt-3 space-y-4">
                    
                    {/* Tab Navigation */}
                    <div className="flex justify-center mb-5">
                      <div className="inline-flex rounded-full bg-slate-100/80 p-0.5 border border-slate-200/50 dark:bg-slate-800 dark:border-slate-700/50">
                        {['device', 'intervals', 'thresholds'].map((tab) => (
                          <button
                            key={tab}
                            type="button"
                            onClick={() => setCreateCardTab(tab)}
                            className={`min-w-[96px] px-4 py-1 rounded-full text-[9px] font-black uppercase tracking-widest transition-all text-center ${
                              createCardTab === tab
                                ? 'bg-white dark:bg-slate-700 text-emerald-600 dark:text-emerald-400 shadow-sm ring-1 ring-emerald-500/10'
                                : 'text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-white/50 dark:hover:bg-slate-700/30'
                            }`}
                          >
                            {t(tab === 'device' ? 'General' : tab === 'intervals' ? 'Intervals' : 'Thresholds')}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="min-h-[170px] w-full overflow-y-auto custom-scrollbar relative">
                    
                    {/* TAB: General (Create) */}
                    {createCardTab === 'device' && (
                        <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-200 px-2 pt-2 pb-1">
                            <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-4 w-full max-w-xl">
                               {/* Row 1 */}
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('OLT name')}</FieldLabel>
                                  <FieldInput
                                    className="text-center"
                                    ref={addNameRef}
                                    value={form.name}
                                    onChange={(e) => setField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                                    maxLength={MAX_OLT_NAME}
                                    placeholder="OLT-01"
                                  />
                               </div>
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('Vendor')}</FieldLabel>
                                  <FieldSelect
                                    value={form.vendor}
                                    onChange={handleVendorChange}
                                    options={vendorOptions.map((v) => ({ value: v, label: String(v).toUpperCase() }))}
                                    disabled={vendorLoading || !vendorOptions.length}
                                  />
                               </div>
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('Model')}</FieldLabel>
                                  <FieldSelect
                                    value={form.vendor_profile}
                                    onChange={(val) => setField('vendor_profile', val)}
                                    options={modelOptions.map((item) => ({ value: String(item.id), label: item.model_name }))}
                                    disabled={vendorLoading || !modelOptions.length}
                                  />
                               </div>

                               {/* Row 2 */}
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('IP')}</FieldLabel>
                                  <FieldInput
                                    className="text-center"
                                    value={form.ip_address}
                                    onChange={(e) => setField('ip_address', e.target.value)}
                                    placeholder="10.0.0.1"
                                  />
                               </div>
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('SNMP community')}</FieldLabel>
                                  <FieldInput
                                    className="text-center"
                                    value={form.snmp_community}
                                    onChange={(e) => setField('snmp_community', e.target.value)}
                                    placeholder="public"
                                  />
                               </div>
                               <div className="flex flex-col gap-1.5">
                                  <FieldLabel>{t('Port')}</FieldLabel>
                                  <FieldInput
                                    className="text-center"
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
                    )}

                    {/* TAB: Intervals (Create) */}
                    {createCardTab === 'intervals' && (
                        <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-300 px-2 pt-4 pb-2">
                            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                              {/* Item 1 */}
                              <div className="group flex flex-col items-center gap-2.5">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('ONU discovery')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                    value={form.discovery_interval}
                                    onChange={(e) => setField('discovery_interval', e.target.value)}
                                    placeholder="5h"
                                  />
                                   <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                                   {/* Disabled run button for create mode */}
                                  <button disabled className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-300 dark:text-slate-600 cursor-not-allowed flex items-center gap-1.5">
                                    <span>{t('Run')}</span>
                                  </button>
                                </div>
                              </div>

                              {/* Item 2 */}
                              <div className="group flex flex-col items-center gap-2.5">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('Status collection')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                    value={form.polling_interval}
                                    onChange={(e) => setField('polling_interval', e.target.value)}
                                    placeholder="5m"
                                  />
                                   <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                                  <button disabled className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-300 dark:text-slate-600 cursor-not-allowed flex items-center gap-1.5">
                                    <span>{t('Run')}</span>
                                  </button>
                                </div>
                              </div>

                              {/* Item 3 */}
                              <div className="group flex flex-col items-center gap-2.5 col-span-2 lg:col-span-1">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('Power collection')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                    value={form.power_interval}
                                    onChange={(e) => setField('power_interval', e.target.value)}
                                    placeholder="1d"
                                  />
                                   <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                                  <button disabled className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-300 dark:text-slate-600 cursor-not-allowed flex items-center gap-1.5">
                                    <span>{t('Run')}</span>
                                  </button>
                                </div>
                              </div>
                            </div>
                        </div>
                    )}

                    {/* TAB: Thresholds (Create) */}
                    {createCardTab === 'thresholds' && (
                        <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-200">
                             {/* Two columns: ONU RX | OLT RX */}
                             <div className="grid grid-cols-2 gap-4 relative">
                                {/* Vertical Divider */}
                                <div className="absolute left-1/2 top-4 bottom-0 w-px bg-slate-100 dark:bg-slate-800/50 -translate-x-1/2"></div>
                                
                                <ThresholdControl 
                                    label="ONU RX"
                                    goodKey="onu_rx_good"
                                    badKey="onu_rx_bad"
                                    values={createThresholdForm}
                                    onChange={(key, val) => setCreateThresholdField(key, val)}
                                    t={t}
                                />
                                <ThresholdControl 
                                    label="OLT RX"
                                    goodKey="olt_rx_good"
                                    badKey="olt_rx_bad"
                                    values={createThresholdForm}
                                    onChange={(key, val) => setCreateThresholdField(key, val)}
                                    t={t}
                                />
                             </div>
                        </div>
                    )}
                    {/* Overlay messages inside content area */}
                    {(localError || actionError) && (
                      <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <AlertCircle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-rose-500 dark:text-rose-400 uppercase tracking-wider">{localError || actionError}</p>
                      </div>
                    )}

                    {actionMessage && !(localError || actionError) && (
                      <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">{actionMessage}</p>
                      </div>
                    )}

                    </div>

                    {/* Footer Actions */}
                    <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-100 dark:border-slate-700/40 mt-4">
                      <button
                        type="button"
                        onClick={() => setShowAddForm(false)}
                        className="h-7 px-3 rounded-lg border border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 text-[9px] font-black uppercase tracking-wider transition-all active:scale-95 whitespace-nowrap"
                      >
                        {t('Cancel')}
                      </button>
                      <button
                        type="button"
                        onClick={handleCreate}
                        disabled={createBusy}
                        className="h-7 px-3.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/20 text-[9px] font-black uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-95 whitespace-nowrap"
                      >
                        {createBusy ? <RefreshCcw className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                        {t('Save')}
                      </button>
                    </div>

                  </div>
                </div>
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
            const deleteBusy = Boolean(actionBusy?.[`delete:${olt.id}`])
            const localUpdateBusy = Boolean(actionBusy?.[`update:${olt.id}`])
            const snmpBadge = getSnmpBadge(olt, snmpStatus, t)

            return (
              <div key={olt.id}>
                <OltCard
                  olt={olt}
                  isSelected={isSelected}
                  health={health}
                  onSelect={setSelectedOltId}
                  onDeleteClick={() => handleDelete(olt.id)}
                  deleteBusy={deleteBusy}
                  resolvedVendor={resolvedVendor}
                  t={t}
                >
                {isSelected && editForm && (
                  <div className="pt-3 space-y-4">

                    {/* ── Tab bar (Segmented Toggle) ── */}
                    <div className="flex justify-center mb-5">
                      <div className="inline-flex rounded-full bg-slate-100/80 p-0.5 border border-slate-200/50 dark:bg-slate-800 dark:border-slate-700/50">
                        {['device', 'intervals', 'thresholds'].map((tab) => (
                          <button
                            key={tab}
                            type="button"
                            onClick={() => setEditCardTab(tab)}
                            className={`min-w-[96px] px-4 py-1 rounded-full text-[9px] font-black uppercase tracking-widest transition-all text-center ${
                              editCardTab === tab
                                ? 'bg-white dark:bg-slate-700 text-emerald-600 dark:text-emerald-400 shadow-sm ring-1 ring-emerald-500/10'
                                : 'text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-white/50 dark:hover:bg-slate-700/30'
                            }`}
                          >
                            {t(tab === 'device' ? 'General' : tab === 'intervals' ? 'Intervals' : 'Thresholds')}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* ── Fixed height content area ── */}
                    <div className="min-h-[170px] w-full overflow-y-auto custom-scrollbar">

                    {/* ── TAB: Device + Connection ── */}
                    {editCardTab === 'device' && (
                      <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-200 px-2 pt-2 pb-1">
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-4 w-full max-w-xl">
                           
                           {/* Row 1 */}
                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('OLT name')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={editForm.name}
                                onChange={(e) => setEditField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                                maxLength={MAX_OLT_NAME}
                                placeholder="OLT-01"
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('Vendor')}</FieldLabel>
                              <FieldSelect
                                value={editForm.vendor}
                                onChange={handleEditVendorChange}
                                options={vendorOptions.map((v) => ({ value: v, label: String(v).toUpperCase() }))}
                                disabled={vendorLoading || !vendorOptions.length}
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('Model')}</FieldLabel>
                              <FieldSelect
                                value={editForm.vendor_profile}
                                onChange={(val) => setEditField('vendor_profile', val)}
                                options={editModelOptions.map((item) => ({ value: String(item.id), label: item.model_name }))}
                                disabled={vendorLoading || !editModelOptions.length}
                              />
                           </div>

                           {/* Row 2 */}
                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('IP')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={editForm.ip_address}
                                onChange={(e) => setEditField('ip_address', e.target.value)}
                                placeholder="10.0.0.1"
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('SNMP community')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={editForm.snmp_community}
                                onChange={(e) => setEditField('snmp_community', e.target.value)}
                                placeholder="public"
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('Port')}</FieldLabel>
                              <FieldInput
                                className="text-center px-1"
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
                    )}

                    {/* ── TAB: Intervals ── */}
                    {editCardTab === 'intervals' && (
                      <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-300 px-2 pt-4 pb-2">
                        
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                          {/* Item 1: Discovery */}
                          <div className="group flex flex-col items-center gap-2.5">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('ONU discovery')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={editForm.discovery_interval}
                                onChange={(e) => setEditField('discovery_interval', e.target.value)}
                                placeholder="5h"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => handleDiscovery(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>

                          {/* Item 2: Status */}
                          <div className="group flex flex-col items-center gap-2.5">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('Status collection')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={editForm.polling_interval}
                                onChange={(e) => setEditField('polling_interval', e.target.value)}
                                placeholder="5m"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => onRunPolling?.(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>

                          {/* Item 3: Power */}
                          <div className="group flex flex-col items-center gap-2.5 col-span-2 lg:col-span-1">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('Power collection')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={editForm.power_interval}
                                onChange={(e) => setEditField('power_interval', e.target.value)}
                                placeholder="1d"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => onRefreshPower?.(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── TAB: Thresholds ── */}
                    {editCardTab === 'thresholds' && thresholdForm && (
                      <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-200">
                         {/* Two columns: ONU RX | OLT RX */}
                         <div className="grid grid-cols-2 gap-4 relative">
                            {/* Vertical Divider */}
                            <div className="absolute left-1/2 top-4 bottom-0 w-px bg-slate-100 dark:bg-slate-800/50 -translate-x-1/2"></div>
                            
                            <ThresholdControl 
                                label="ONU RX"
                                goodKey="onu_rx_good"
                                badKey="onu_rx_bad"
                                values={thresholdForm}
                                onChange={setThresholdField}
                                t={t}
                            />
                            <ThresholdControl 
                                label="OLT RX"
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

                    {/* Notification messages between content and action bar */}
                    {(localError || actionError) && (
                      <div className="flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <AlertCircle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-rose-500 dark:text-rose-400 uppercase tracking-wider">{localError || actionError}</p>
                      </div>
                    )}

                    {actionMessage && !(localError || actionError) && (
                      <div className="flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">{actionMessage}</p>
                      </div>
                    )}

                    {/* ── Action bar ── */}
                    <div className="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-700/30">
                      {/* Left: info */}
                      <div className="flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-slate-300 dark:text-slate-600" />
                        <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500">
                          {t('Last discovery')}: {formatRelativeTime(olt.last_discovery_at, t)}
                        </span>
                      </div>

                      {/* Right: save actions */}
                      <div className="flex items-center gap-2">
                          <div className="flex items-center gap-1.5 animate-in fade-in duration-200">
                            <button
                              type="button"
                              onClick={handleDiscard}
                              // Only disable if not dirty to prevent accidental clears? User asked for always present. 
                              // Usually cancel resets to original state. If not dirty, it does nothing essentially.
                              disabled={!dirty}
                              className="h-7 px-3 rounded-lg border border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 text-[9px] font-black uppercase tracking-wider transition-all active:scale-95 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {t('Cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={handleUpdate}
                              disabled={localUpdateBusy || !dirty}
                              className="h-7 px-3.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white shadow-md shadow-emerald-600/20 text-[9px] font-black uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none transition-all active:scale-95 whitespace-nowrap"
                            >
                              {localUpdateBusy ? <RefreshCcw className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                              {t('Save')}
                            </button>
                          </div>
                      </div>
                    </div>

                  </div>
                )}
              </OltCard>
             </div>
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
