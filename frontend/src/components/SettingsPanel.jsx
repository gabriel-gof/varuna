import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Trash2, RefreshCcw, Check, AlertCircle, CheckCircle2, ChevronDown, Server, Clock } from 'lucide-react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { useTranslation } from 'react-i18next'
import { DEFAULT_THRESHOLDS, getOltThresholds, saveOltThresholds } from '../utils/powerThresholds'
import { HEALTH_STYLES } from '../utils/healthStyles'

const MAX_OLT_NAME = 12
const EXPANDED_OLTS_STORAGE_KEY = 'varuna.settings.expandedOltIds'
const OLD_SELECTED_OLT_STORAGE_KEY = 'varuna.settings.selectedOltId'
const isFiberhomeVendor = (vendor) => String(vendor || '').toUpperCase() === 'FIBERHOME'

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
    power_interval: '1d',
    history_days: '7d',
    unm_enabled: false,
    unm_host: '',
    unm_port: '3306',
    unm_username: 'unm2000',
    unm_password: '',
    unm_mneid: '',
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
    power_interval: formatDuration(olt.power_interval_seconds || 300),
    history_days: `${olt.history_days || 7}d`,
    unm_enabled: Boolean(olt.unm_enabled),
    unm_host: olt.unm_host || '',
    unm_port: String(olt.unm_port || 3306),
    unm_username: olt.unm_username || '',
    unm_password: '',
    unm_mneid: olt.unm_mneid ? String(olt.unm_mneid) : '',
  }
}

const normalizeThresholdForm = (value) => {
  const candidate = (value && typeof value === 'object') ? value : {}
  return {
    onu_rx_good: Number.isFinite(Number(candidate.onu_rx_good)) ? Number(candidate.onu_rx_good) : DEFAULT_THRESHOLDS.onu_rx_good,
    onu_rx_bad: Number.isFinite(Number(candidate.onu_rx_bad)) ? Number(candidate.onu_rx_bad) : DEFAULT_THRESHOLDS.onu_rx_bad,
    olt_rx_good: Number.isFinite(Number(candidate.olt_rx_good)) ? Number(candidate.olt_rx_good) : DEFAULT_THRESHOLDS.olt_rx_good,
    olt_rx_bad: Number.isFinite(Number(candidate.olt_rx_bad)) ? Number(candidate.olt_rx_bad) : DEFAULT_THRESHOLDS.olt_rx_bad,
  }
}

const parseHistoryDays = (input, fallback = 7) => {
  const parsed = Number.parseInt(String(input ?? '').trim(), 10)
  if (!Number.isFinite(parsed)) return fallback
  if (parsed < 7) return 7
  if (parsed > 30) return 30
  return parsed
}

const formatHistoryDays = (input, fallback = 7) => `${parseHistoryDays(input, fallback)}d`

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
    if (key === 'unm_password') {
      return String(editForm[key] || '') !== ''
    }
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
      text-[11px] text-compact font-semibold text-slate-800 dark:text-slate-200 placeholder:text-slate-300 dark:placeholder:text-slate-600
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
            text-[11px] text-compact font-semibold text-slate-800 dark:text-slate-200
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

const UnmToggle = ({ enabled, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={enabled}
    onClick={onChange}
    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 ${
      enabled
        ? 'bg-emerald-500'
        : 'bg-slate-200 dark:bg-slate-700'
    }`}
  >
    <span className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform duration-200 ${
      enabled ? 'translate-x-[18px]' : 'translate-x-[3px]'
    }`} />
  </button>
)

const getOltHealth = (olt, oltHealthById) => {
  const derived = oltHealthById?.[String(olt.id)] || oltHealthById?.[olt.id]
  if (derived?.state && HEALTH_STYLES[derived.state]) return HEALTH_STYLES[derived.state]
  return HEALTH_STYLES.neutral
}

const getModelDisplayLabel = ({ vendor, model_name }, language, t) => {
  const vendorName = String(vendor || '').trim().toUpperCase()
  const isUnifiedVendor = vendorName === 'FIBERHOME' || vendorName === 'HUAWEI'
  const isPortuguese = String(language || '').toLowerCase().startsWith('pt')
  if (isUnifiedVendor && isPortuguese) {
    return t('Unified model')
  }
  return model_name || ''
}

const hasDisplayValue = (value) => value !== null && value !== undefined && String(value).trim() !== ''

/* ─── OLT Card header ─── */

const OltCard = ({ olt, modelLabel, isSelected, health, onSelect, onDeleteClick, deleteBusy, t, children }) => {
  const meta = [olt.ip_address, olt.vendor_display, modelLabel || olt.vendor_profile_name].filter(Boolean).join('  ·  ')

  return (
    <div className={`
      w-full transition-all duration-300 bg-white dark:bg-slate-900
      rounded-xl border relative
      ${isSelected ? health.borderActive : health.borderIdle}
    `}>
      <div
        onClick={() => onSelect(String(olt.id))}
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
          <p className="text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wide leading-none mt-0.5 whitespace-nowrap overflow-hidden text-ellipsis">
            {meta || '\u2014'}
          </p>
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
    const fallbackGood = DEFAULT_THRESHOLDS[goodKey]
    const fallbackBad = DEFAULT_THRESHOLDS[badKey]
    if (!goodValid) {
      onChange(goodKey, Number.isFinite(fallbackGood) ? fallbackGood : -27)
      onChange(badKey, Number.isFinite(fallbackBad) ? fallbackBad : -30)
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
                              text-[12px] text-compact font-black text-slate-700 dark:text-slate-200 text-center tracking-tight shadow-sm
                              focus:bg-white dark:focus:bg-slate-800 focus:border-emerald-400 focus:ring-4 focus:ring-emerald-500/10 outline-none transition-all"
                   placeholder={String(DEFAULT_THRESHOLDS[goodKey] ?? -27)}
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
  actionError,
  actionMessage,
  onCreateOlt,
  onUpdateOlt,
  onDeleteOlt,
  onRunDiscovery,
  onRunPolling,
  onRefreshPower,
  actionBusy,
  oltHealthById = {},
}) => {
  const { t, i18n } = useTranslation()
  const [showAddForm, setShowAddForm] = useState(false)
  const [expandedIds, setExpandedIds] = useState(() => {
    try {
      if (typeof window === 'undefined') return {}
      // Migrate from old single-ID key
      const oldSaved = window.localStorage.getItem(OLD_SELECTED_OLT_STORAGE_KEY)
      if (oldSaved) {
        window.localStorage.removeItem(OLD_SELECTED_OLT_STORAGE_KEY)
        const migrated = { [String(oldSaved)]: true }
        window.localStorage.setItem(EXPANDED_OLTS_STORAGE_KEY, JSON.stringify([String(oldSaved)]))
        return migrated
      }
      const saved = window.localStorage.getItem(EXPANDED_OLTS_STORAGE_KEY)
      if (saved) {
        const arr = JSON.parse(saved)
        if (Array.isArray(arr)) {
          const obj = {}
          arr.forEach((id) => { obj[String(id)] = true })
          return obj
        }
      }
      return {}
    } catch {
      return {}
    }
  })
  const [form, setForm] = useState(() => buildInitialForm(vendorProfiles))
  const [editForms, setEditForms] = useState({})
  const [localError, setLocalError] = useState('')
  const [localErrors, setLocalErrors] = useState({})
  const addNameRef = useRef(null)
  const [createCardTab, setCreateCardTab] = useState('device')
  const [editCardTabs, setEditCardTabs] = useState({})
  const [thresholdForms, setThresholdForms] = useState({})
  const [originalThresholdsMap, setOriginalThresholdsMap] = useState({})
  const [createThresholdForm, setCreateThresholdForm] = useState(DEFAULT_THRESHOLDS)

  const vendorOptions = useMemo(() => {
    return [...new Set((vendorProfiles || []).map((item) => item?.vendor).filter(Boolean))]
  }, [vendorProfiles])

  const modelOptionsForVendor = useCallback((vendor) => {
    if (!vendor) return []
    return (vendorProfiles || []).filter((item) => item.vendor === vendor)
  }, [vendorProfiles])

  const modelOptions = useMemo(() => modelOptionsForVendor(form.vendor), [modelOptionsForVendor, form.vendor])
  const vendorProfileById = useMemo(
    () => new Map((vendorProfiles || []).map((profile) => [String(profile.id), profile])),
    [vendorProfiles],
  )
  const modelLabelForProfile = useCallback((profile) => {
    if (!profile) return ''
    return getModelDisplayLabel(profile, i18n.language, t)
  }, [i18n.language, t])

  const createSelectedProfile = vendorProfileById.get(String(form.vendor_profile || ''))
  const createSupportsOltRxPower = typeof createSelectedProfile?.supports_olt_rx_power === 'boolean'
    ? createSelectedProfile.supports_olt_rx_power
    : true

  // Toggle an OLT card open/closed
  const toggleCard = useCallback((oltId) => {
    setExpandedIds((prev) => {
      const next = { ...prev }
      if (next[oltId]) {
        // Collapsing — clean up per-card state
        delete next[oltId]
        setEditForms((p) => { const n = { ...p }; delete n[oltId]; return n })
        setEditCardTabs((p) => { const n = { ...p }; delete n[oltId]; return n })
        setThresholdForms((p) => { const n = { ...p }; delete n[oltId]; return n })
        setOriginalThresholdsMap((p) => { const n = { ...p }; delete n[oltId]; return n })
        setLocalErrors((p) => { const n = { ...p }; delete n[oltId]; return n })
      } else {
        // Expanding — initialize per-card state
        next[oltId] = true
        const olt = olts.find((item) => String(item.id) === oltId)
        if (olt) {
          setEditForms((p) => ({ ...p, [oltId]: buildEditForm(olt, vendorProfiles) }))
          setEditCardTabs((p) => ({ ...p, [oltId]: 'device' }))
          const loaded = normalizeThresholdForm(getOltThresholds(oltId))
          setThresholdForms((p) => ({ ...p, [oltId]: loaded }))
          setOriginalThresholdsMap((p) => ({ ...p, [oltId]: loaded }))
        }
      }
      return next
    })
  }, [olts, vendorProfiles])

  // Persist expanded IDs to localStorage
  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      const ids = Object.keys(expandedIds)
      if (ids.length) {
        window.localStorage.setItem(EXPANDED_OLTS_STORAGE_KEY, JSON.stringify(ids))
      } else {
        window.localStorage.removeItem(EXPANDED_OLTS_STORAGE_KEY)
      }
    } catch {
      // noop
    }
  }, [expandedIds])

  // Stale cleanup: remove expanded IDs whose OLTs no longer exist
  useEffect(() => {
    const expandedKeys = Object.keys(expandedIds)
    if (!expandedKeys.length) return
    const oltIdSet = new Set(olts.map((o) => String(o.id)))
    const stale = expandedKeys.filter((id) => !oltIdSet.has(id))
    if (!stale.length) return
    setExpandedIds((prev) => {
      const next = { ...prev }
      stale.forEach((id) => delete next[id])
      return next
    })
    stale.forEach((id) => {
      setEditForms((p) => { const n = { ...p }; delete n[id]; return n })
      setEditCardTabs((p) => { const n = { ...p }; delete n[id]; return n })
      setThresholdForms((p) => { const n = { ...p }; delete n[id]; return n })
      setOriginalThresholdsMap((p) => { const n = { ...p }; delete n[id]; return n })
      setLocalErrors((p) => { const n = { ...p }; delete n[id]; return n })
    })
  }, [olts, expandedIds])

  // Background sync: re-sync editForms from fresh OLT data for non-dirty forms
  useEffect(() => {
    const expandedKeys = Object.keys(expandedIds)
    if (!expandedKeys.length) return
    setEditForms((prev) => {
      let changed = false
      const next = { ...prev }
      expandedKeys.forEach((oltId) => {
        const olt = olts.find((item) => String(item.id) === oltId)
        if (!olt) return
        // Only sync if form exists and is NOT dirty
        if (prev[oltId] && !isFormDirty(prev[oltId], olt, vendorProfiles)) {
          const fresh = buildEditForm(olt, vendorProfiles)
          // Avoid object identity churn if nothing changed
          const prevForm = prev[oltId]
          const isDifferent = Object.keys(fresh).some((k) => String(fresh[k] || '') !== String(prevForm[k] || ''))
          if (isDifferent) {
            next[oltId] = fresh
            changed = true
          }
        } else if (!prev[oltId]) {
          next[oltId] = buildEditForm(olt, vendorProfiles)
          changed = true
        }
      })
      return changed ? next : prev
    })
  }, [olts, vendorProfiles, expandedIds])

  // Ensure threshold state always exists for expanded cards (including restored cards).
  useEffect(() => {
    const expandedKeys = Object.keys(expandedIds)
    if (!expandedKeys.length) return

    setThresholdForms((prev) => {
      let changed = false
      const next = { ...prev }
      expandedKeys.forEach((oltId) => {
        if (next[oltId]) return
        next[oltId] = normalizeThresholdForm(getOltThresholds(oltId))
        changed = true
      })
      return changed ? next : prev
    })

    setOriginalThresholdsMap((prev) => {
      let changed = false
      const next = { ...prev }
      expandedKeys.forEach((oltId) => {
        if (next[oltId]) return
        next[oltId] = normalizeThresholdForm(getOltThresholds(oltId))
        changed = true
      })
      return changed ? next : prev
    })
  }, [expandedIds])

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

  // Edit form model sync — iterate over all editForms
  useEffect(() => {
    setEditForms((prev) => {
      let changed = false
      const next = { ...prev }
      Object.keys(prev).forEach((oltId) => {
        const cardForm = prev[oltId]
        if (!cardForm) return
        const cardModelOptions = modelOptionsForVendor(cardForm.vendor)
        if (!cardModelOptions.length) {
          if (cardForm.vendor_profile !== '') {
            next[oltId] = { ...cardForm, vendor_profile: '' }
            changed = true
          }
          return
        }
        const exists = cardModelOptions.some((item) => String(item.id) === String(cardForm.vendor_profile))
        if (!exists) {
          next[oltId] = { ...cardForm, vendor_profile: String(cardModelOptions[0].id) }
          changed = true
        }
      })
      return changed ? next : prev
    })
  }, [vendorProfiles, modelOptionsForVendor])

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))
  const setEditField = (oltId, key, value) => setEditForms((prev) => {
    const cardForm = prev[oltId]
    if (!cardForm) return prev
    return { ...prev, [oltId]: { ...cardForm, [key]: value } }
  })
  const setHistoryDaysField = (value) => setField('history_days', String(value || ''))
  const normalizeCreateHistoryDays = () => setField('history_days', formatHistoryDays(form.history_days, 7))
  const setEditHistoryDaysField = (oltId, value) => setEditField(oltId, 'history_days', String(value || ''))
  const normalizeEditHistoryDays = (oltId) => {
    setEditForms((prev) => {
      const cardForm = prev[oltId]
      if (!cardForm) return prev
      const normalized = formatHistoryDays(cardForm.history_days, 7)
      if (String(cardForm.history_days || '') === normalized) return prev
      return { ...prev, [oltId]: { ...cardForm, history_days: normalized } }
    })
  }

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
    const isFiberhome = isFiberhomeVendor(nextVendor)
    setForm((prev) => ({
      ...prev,
      vendor: nextVendor,
      vendor_profile: nextModel?.id ? String(nextModel.id) : '',
      ...(!isFiberhome && { unm_enabled: false }),
    }))
  }

  const handleEditVendorChange = (oltId, nextVendor) => {
    setEditForms((prev) => {
      const cardForm = prev[oltId]
      if (!cardForm || nextVendor === cardForm.vendor) return prev
      const isFiberhome = isFiberhomeVendor(nextVendor)
      const nextModel = (vendorProfiles || []).find((item) => item.vendor === nextVendor)
      return { ...prev, [oltId]: { ...cardForm, vendor: nextVendor, vendor_profile: nextModel?.id ? String(nextModel.id) : '', ...(!isFiberhome && { unm_enabled: false }) } }
    })
  }

  const handleCreate = async () => {
    const unmEnabled = Boolean(form.unm_enabled)
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
      power_interval_seconds: parseDuration(form.power_interval) || 86400,
      history_days: parseHistoryDays(form.history_days, 7),
      unm_enabled: unmEnabled,
      unm_host: String(form.unm_host || '').trim() || null,
      unm_port: Number(form.unm_port || 3306),
      unm_username: String(form.unm_username || '').trim(),
      unm_password: String(form.unm_password || ''),
      unm_mneid: String(form.unm_mneid || '').trim() ? Number(form.unm_mneid) : null,
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

  const handleUpdate = async (oltId) => {
    const cardEditForm = editForms[oltId]
    if (!oltId || !cardEditForm) return
    const unmEnabled = Boolean(cardEditForm.unm_enabled)

    const payload = {
      name: String(cardEditForm.name || '').trim().slice(0, MAX_OLT_NAME),
      ip_address: String(cardEditForm.ip_address || '').trim(),
      vendor_profile: Number(cardEditForm.vendor_profile),
      snmp_community: String(cardEditForm.snmp_community || '').trim(),
      snmp_port: Number(cardEditForm.snmp_port || 161),
      discovery_interval_minutes: Math.round((parseDuration(cardEditForm.discovery_interval) || 18000) / 60),
      polling_interval_seconds: parseDuration(cardEditForm.polling_interval) || 300,
      power_interval_seconds: parseDuration(cardEditForm.power_interval) || 86400,
      history_days: parseHistoryDays(cardEditForm.history_days, 7),
      unm_enabled: unmEnabled,
      unm_host: String(cardEditForm.unm_host || '').trim() || null,
      unm_port: Number(cardEditForm.unm_port || 3306),
      unm_username: String(cardEditForm.unm_username || '').trim(),
      unm_password: String(cardEditForm.unm_password || ''),
      unm_mneid: String(cardEditForm.unm_mneid || '').trim() ? Number(cardEditForm.unm_mneid) : null,
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalErrors((prev) => ({ ...prev, [oltId]: t('Required fields are missing') }))
      setTimeout(() => setLocalErrors((prev) => { const n = { ...prev }; delete n[oltId]; return n }), 5000)
      return
    }

    setLocalErrors((prev) => { const n = { ...prev }; delete n[oltId]; return n })
    await onUpdateOlt?.(oltId, payload)
    // Persist thresholds to localStorage
    const cardThresholdForm = thresholdForms[oltId]
    if (cardThresholdForm && oltId) {
      const cardProfile = vendorProfileById.get(String(cardEditForm.vendor_profile || ''))
      const cardSupportsOltRx = typeof cardProfile?.supports_olt_rx_power === 'boolean'
        ? cardProfile.supports_olt_rx_power
        : true
      const cardKeys = cardSupportsOltRx
        ? ['onu_rx_good', 'onu_rx_bad', 'olt_rx_good', 'olt_rx_bad']
        : ['onu_rx_good', 'onu_rx_bad']
      const allValid = cardKeys.every(
        (k) => typeof cardThresholdForm[k] === 'number' && Number.isFinite(cardThresholdForm[k])
      )
      if (allValid) {
        saveOltThresholds(oltId, cardThresholdForm)
        setOriginalThresholdsMap((prev) => ({ ...prev, [oltId]: { ...cardThresholdForm } }))
      }
    }
  }

  const handleDelete = async (oltId) => {
    const confirmed = window.confirm(t('Do you want to remove this OLT?'))
    if (!confirmed) return
    await onDeleteOlt?.(oltId)
    // Stale cleanup effect handles removing expanded state
  }

  const handleDiscovery = async (oltId) => {
    await onRunDiscovery?.(oltId)
  }

  const handleDiscard = (oltId) => {
    const olt = olts.find((item) => String(item.id) === oltId)
    if (!olt) return
    setEditForms((prev) => ({ ...prev, [oltId]: buildEditForm(olt, vendorProfiles) }))
    const origThresholds = originalThresholdsMap[oltId]
    if (origThresholds) setThresholdForms((prev) => ({ ...prev, [oltId]: { ...origThresholds } }))
  }

  const setThresholdField = (oltId, key, rawValue) => {
    const numValue = rawValue === '' || rawValue === '-' ? rawValue : parseFloat(rawValue)
    setThresholdForms((prev) => {
      const cardForm = prev[oltId] || normalizeThresholdForm(getOltThresholds(oltId))
      return { ...prev, [oltId]: { ...cardForm, [key]: typeof numValue === 'number' && Number.isFinite(numValue) ? numValue : rawValue } }
    })
  }

  const createBusy = Boolean(actionBusy?.create)

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
                              {modelLabelForProfile(modelOptions.find(m => String(m.id) === String(form.vendor_profile))) || '\u2014'}
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
                                    options={modelOptions.map((item) => ({ value: String(item.id), label: modelLabelForProfile(item) }))}
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

                        {/* UNM Integration — FiberHome only */}
                        {isFiberhomeVendor(form.vendor) && (
                        <div className="w-full max-w-xl mt-5">
                          <div className={`rounded-lg transition-colors duration-200 ${
                            form.unm_enabled
                              ? 'border border-emerald-200/60 bg-emerald-50/30 dark:border-emerald-500/20 dark:bg-emerald-500/5'
                              : 'bg-slate-50/80 dark:bg-slate-800/20'
                          }`}>
                            <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                              <div className="flex items-center gap-2">
                                <SectionLabel>{t('UNM integration')}</SectionLabel>
                              </div>
                              <UnmToggle
                                enabled={form.unm_enabled}
                                onChange={() => setField('unm_enabled', !form.unm_enabled)}
                              />
                            </div>

                            <div
                              className={`grid transition-all duration-200 ease-in-out ${
                                form.unm_enabled
                                  ? 'grid-rows-[1fr] opacity-100'
                                  : 'grid-rows-[0fr] opacity-0'
                              }`}
                              {...(!form.unm_enabled && { inert: '' })}
                            >
                              <div className="overflow-hidden">
                                <div className="px-3.5 pb-3 pt-0.5 space-y-2.5">
                                  <div className="grid grid-cols-3 gap-x-3 gap-y-2.5">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Host')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_host}
                                        onChange={(e) => setField('unm_host', e.target.value)}
                                        placeholder="192.168.30.101"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Port')}</FieldLabel>
                                      <FieldInput
                                        className="text-center px-1"
                                        type="number"
                                        min={1}
                                        max={65535}
                                        value={form.unm_port}
                                        onChange={(e) => setField('unm_port', e.target.value)}
                                        placeholder="3306"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('MNEID')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_mneid}
                                        onChange={(e) => setField('unm_mneid', e.target.value)}
                                        placeholder="13172740"
                                      />
                                    </div>
                                  </div>

                                  <div className="grid grid-cols-2 gap-x-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Username')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={form.unm_username}
                                        onChange={(e) => setField('unm_username', e.target.value)}
                                        placeholder="unm2000"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Password')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        type="password"
                                        autoComplete="off"
                                        value={form.unm_password}
                                        onChange={(e) => setField('unm_password', e.target.value)}
                                        placeholder="••••••••"
                                      />
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                        )}
                        </div>
                    )}

                    {/* TAB: Intervals (Create) */}
                    {createCardTab === 'intervals' && (
                        <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-300 px-2 pt-4 pb-2">
                            <div className="grid grid-cols-2 gap-4">
                              {/* Item 1: Discovery */}
                              <div className="group flex flex-col items-center gap-2.5 order-1">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('ONU discovery')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
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

                              {/* Item 2: Status — row 2, right */}
                              <div className="group flex flex-col items-center gap-2.5 order-4">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('Status collection')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
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

                              {/* Item 3: Power — row 2, left */}
                              <div className="group flex flex-col items-center gap-2.5 order-3">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('Power collection')}
                                </label>
                                <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
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

                              {/* Item 4: History — row 1, right */}
                              <div className="group flex flex-col items-center gap-2.5 order-2">
                                <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                                  {t('History retention')}
                                </label>
                                <div className="flex items-center justify-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                                  <input
                                    className="w-[132px] h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                    value={form.history_days}
                                    onChange={(e) => setHistoryDaysField(e.target.value)}
                                    onBlur={normalizeCreateHistoryDays}
                                    placeholder="7d"
                                  />
                                </div>
                              </div>
                            </div>
                        </div>
                    )}

                    {/* TAB: Thresholds (Create) */}
                    {createCardTab === 'thresholds' && (
                        <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-200">
                             <div className={`grid gap-4 relative ${createSupportsOltRxPower ? 'grid-cols-2' : 'grid-cols-1'}`}>
                                {createSupportsOltRxPower && (
                                  <div className="absolute left-1/2 top-4 bottom-0 w-px bg-slate-100 dark:bg-slate-800/50 -translate-x-1/2"></div>
                                )}
                                
                                <ThresholdControl 
                                    label="ONU RX"
                                    goodKey="onu_rx_good"
                                    badKey="onu_rx_bad"
                                    values={createThresholdForm}
                                    onChange={(key, val) => setCreateThresholdField(key, val)}
                                    t={t}
                                />
                                {createSupportsOltRxPower && (
                                  <ThresholdControl 
                                      label="OLT RX"
                                      goodKey="olt_rx_good"
                                      badKey="olt_rx_bad"
                                      values={createThresholdForm}
                                      onChange={(key, val) => setCreateThresholdField(key, val)}
                                      t={t}
                                  />
                                )}
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

                    {actionMessage && actionMessage.oltId == null && !(localError || actionError) && (
                      <div className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">{actionMessage.message}</p>
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
            const oltId = String(olt.id)
            const isSelected = Boolean(expandedIds[oltId])
            const health = getOltHealth(olt, oltHealthById)
            const deleteBusy = Boolean(actionBusy?.[`delete:${olt.id}`])
            const localUpdateBusy = Boolean(actionBusy?.[`update:${olt.id}`])

            // Per-card state
            const cardEditForm = editForms[oltId]
            const cardTab = editCardTabs[oltId] || 'device'
            const cardThresholdForm = thresholdForms[oltId]
            const cardThresholdFormForRender = cardThresholdForm || normalizeThresholdForm(getOltThresholds(oltId))
            const cardOriginalThresholds = originalThresholdsMap[oltId]
            const cardError = localErrors[oltId]

            // Per-card derived values
            const cardEditModelOptions = cardEditForm ? modelOptionsForVendor(cardEditForm.vendor) : []
            const cardEditSelectedProfile = cardEditForm ? vendorProfileById.get(String(cardEditForm.vendor_profile || '')) : null
            const cardEditSupportsOltRxPower = typeof cardEditSelectedProfile?.supports_olt_rx_power === 'boolean'
              ? cardEditSelectedProfile.supports_olt_rx_power
              : (typeof olt?.supports_olt_rx_power === 'boolean' ? olt.supports_olt_rx_power : true)
            const cardThresholdKeys = cardEditSupportsOltRxPower
              ? ['onu_rx_good', 'onu_rx_bad', 'olt_rx_good', 'olt_rx_bad']
              : ['onu_rx_good', 'onu_rx_bad']
            const cardFormDirty = isFormDirty(cardEditForm, olt, vendorProfiles)
            const cardThresholdDirty = cardThresholdForm && cardOriginalThresholds
              ? cardThresholdKeys.some((k) => cardThresholdForm[k] !== cardOriginalThresholds[k])
              : false
            const cardDirty = cardFormDirty || cardThresholdDirty

            return (
              <div key={olt.id}>
                <OltCard
                  olt={olt}
                  modelLabel={modelLabelForProfile({ vendor: olt.vendor_display, model_name: olt.vendor_profile_name })}
                  isSelected={isSelected}
                  health={health}
                  onSelect={toggleCard}
                  onDeleteClick={() => handleDelete(olt.id)}
                  deleteBusy={deleteBusy}
                  t={t}
                >
                {isSelected && cardEditForm && (
                  <div className="pt-3 space-y-4">

                    {/* ── Tab bar (Segmented Toggle) ── */}
                    <div className="flex justify-center mb-5">
                      <div className="inline-flex rounded-full bg-slate-100/80 p-0.5 border border-slate-200/50 dark:bg-slate-800 dark:border-slate-700/50">
                        {['device', 'intervals', 'thresholds'].map((tab) => (
                          <button
                            key={tab}
                            type="button"
                            onClick={() => setEditCardTabs((prev) => ({ ...prev, [oltId]: tab }))}
                            className={`min-w-[96px] px-4 py-1 rounded-full text-[9px] font-black uppercase tracking-widest transition-all text-center ${
                              cardTab === tab
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
                    {cardTab === 'device' && (
                      <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-200 px-2 pt-2 pb-1">
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-4 w-full max-w-xl">

                           {/* Row 1 */}
                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('OLT name')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={cardEditForm.name}
                                onChange={(e) => setEditField(oltId, 'name', e.target.value.slice(0, MAX_OLT_NAME))}
                                maxLength={MAX_OLT_NAME}
                                placeholder="OLT-01"
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('Vendor')}</FieldLabel>
                              <FieldSelect
                                value={cardEditForm.vendor}
                                onChange={(v) => handleEditVendorChange(oltId, v)}
                                options={vendorOptions.map((v) => ({ value: v, label: String(v).toUpperCase() }))}
                                disabled={vendorLoading || !vendorOptions.length}
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('Model')}</FieldLabel>
                              <FieldSelect
                                value={cardEditForm.vendor_profile}
                                onChange={(val) => setEditField(oltId, 'vendor_profile', val)}
                                options={cardEditModelOptions.map((item) => ({ value: String(item.id), label: modelLabelForProfile(item) }))}
                                disabled={vendorLoading || !cardEditModelOptions.length}
                              />
                           </div>

                           {/* Row 2 */}
                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('IP')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={cardEditForm.ip_address}
                                onChange={(e) => setEditField(oltId, 'ip_address', e.target.value)}
                                placeholder="10.0.0.1"
                              />
                           </div>

                           <div className="flex flex-col gap-1.5">
                              <FieldLabel>{t('SNMP community')}</FieldLabel>
                              <FieldInput
                                className="text-center"
                                value={cardEditForm.snmp_community}
                                onChange={(e) => setEditField(oltId, 'snmp_community', e.target.value)}
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
                                value={cardEditForm.snmp_port}
                                onChange={(e) => setEditField(oltId, 'snmp_port', e.target.value)}
                                placeholder="161"
                              />
                           </div>

                        </div>

                        {/* UNM Integration — FiberHome only */}
                        {isFiberhomeVendor(cardEditForm.vendor) && (
                        <div className="w-full max-w-xl mt-5">
                          <div className={`rounded-lg transition-colors duration-200 ${
                            cardEditForm.unm_enabled
                              ? 'border border-emerald-200/60 bg-emerald-50/30 dark:border-emerald-500/20 dark:bg-emerald-500/5'
                              : 'bg-slate-50/80 dark:bg-slate-800/20'
                          }`}>
                            <div className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                              <div className="flex items-center gap-2">
                                <SectionLabel>{t('UNM integration')}</SectionLabel>
                                {cardEditForm.unm_enabled && hasDisplayValue(olt.unm_mneid) && (
                                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                )}
                              </div>
                              <UnmToggle
                                enabled={cardEditForm.unm_enabled}
                                onChange={() => setEditField(oltId, 'unm_enabled', !cardEditForm.unm_enabled)}
                              />
                            </div>

                            <div
                              className={`grid transition-all duration-200 ease-in-out ${
                                cardEditForm.unm_enabled
                                  ? 'grid-rows-[1fr] opacity-100'
                                  : 'grid-rows-[0fr] opacity-0'
                              }`}
                              {...(!cardEditForm.unm_enabled && { inert: '' })}
                            >
                              <div className="overflow-hidden">
                                <div className="px-3.5 pb-3 pt-0.5 space-y-2.5">
                                  <div className="grid grid-cols-3 gap-x-3 gap-y-2.5">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Host')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_host}
                                        onChange={(e) => setEditField(oltId, 'unm_host', e.target.value)}
                                        placeholder="192.168.30.101"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Port')}</FieldLabel>
                                      <FieldInput
                                        className="text-center px-1"
                                        type="number"
                                        min={1}
                                        max={65535}
                                        value={cardEditForm.unm_port}
                                        onChange={(e) => setEditField(oltId, 'unm_port', e.target.value)}
                                        placeholder="3306"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('MNEID')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_mneid}
                                        onChange={(e) => setEditField(oltId, 'unm_mneid', e.target.value)}
                                        placeholder="13172740"
                                      />
                                    </div>
                                  </div>

                                  <div className="grid grid-cols-2 gap-x-3">
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Username')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        value={cardEditForm.unm_username}
                                        onChange={(e) => setEditField(oltId, 'unm_username', e.target.value)}
                                        placeholder="unm2000"
                                      />
                                    </div>
                                    <div className="flex flex-col gap-1">
                                      <FieldLabel>{t('Password')}</FieldLabel>
                                      <FieldInput
                                        className="text-center"
                                        type="password"
                                        autoComplete="off"
                                        value={cardEditForm.unm_password}
                                        onChange={(e) => setEditField(oltId, 'unm_password', e.target.value)}
                                        placeholder="••••••••"
                                      />
                                      {olt.unm_password_configured && (
                                        <span className="text-[9px] font-semibold text-slate-400 dark:text-slate-500 text-center">
                                          {t('Leave blank to keep current password')}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                        )}
                      </div>
                    )}

                    {/* ── TAB: Intervals ── */}
                    {cardTab === 'intervals' && (
                      <div className="flex flex-col items-center justify-center animate-in fade-in slide-in-from-left-1 duration-300 px-2 pt-4 pb-2">

                        <div className="grid grid-cols-2 gap-4">
                          {/* Item 1: Discovery — row 1, left */}
                          <div className="group flex flex-col items-center gap-2.5 order-1">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('ONU discovery')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={cardEditForm.discovery_interval}
                                onChange={(e) => setEditField(oltId, 'discovery_interval', e.target.value)}
                                placeholder="5h"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => handleDiscovery(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-slate-400 disabled:dark:hover:text-slate-500 disabled:hover:bg-transparent"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>

                          {/* Item 2: Status — row 2, right */}
                          <div className="group flex flex-col items-center gap-2.5 order-4">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('Status collection')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={cardEditForm.polling_interval}
                                onChange={(e) => setEditField(oltId, 'polling_interval', e.target.value)}
                                placeholder="5m"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => onRunPolling?.(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-slate-400 disabled:dark:hover:text-slate-500 disabled:hover:bg-transparent"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>

                          {/* Item 3: Power — row 2, left */}
                          <div className="group flex flex-col items-center gap-2.5 order-3">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('Power collection')}
                            </label>
                            <div className="flex items-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-16 h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={cardEditForm.power_interval}
                                onChange={(e) => setEditField(oltId, 'power_interval', e.target.value)}
                                placeholder="1d"
                              />
                               <div className="w-px h-4 bg-slate-200 dark:bg-slate-700 mx-0.5"></div>
                              <button
                                type="button"
                                onClick={() => onRefreshPower?.(olt.id)}
                                className="h-7 px-3 rounded-md text-[9px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-white dark:hover:bg-slate-700/50 transition-all flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-slate-400 disabled:dark:hover:text-slate-500 disabled:hover:bg-transparent"
                              >
                                <span>{t('Run')}</span>
                              </button>
                            </div>
                          </div>
                          {/* Item 4: History — row 1, right */}
                          <div className="group flex flex-col items-center gap-2.5 order-2">
                            <label className="text-[9px] font-black tracking-widest text-slate-400 dark:text-slate-500 uppercase transition-colors group-focus-within:text-emerald-500 w-full text-center">
                              {t('History retention')}
                            </label>
                            <div className="flex items-center justify-center p-0.5 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/50 shadow-sm transition-all group-focus-within:border-emerald-500/30 group-focus-within:ring-2 group-focus-within:ring-emerald-500/10">
                              <input
                                className="w-[132px] h-7 bg-transparent text-center text-[11px] text-compact font-bold text-slate-700 dark:text-slate-200 focus:outline-none placeholder:text-slate-300 dark:placeholder:text-slate-600"
                                value={cardEditForm.history_days}
                                onChange={(e) => setEditHistoryDaysField(oltId, e.target.value)}
                                onBlur={() => normalizeEditHistoryDays(oltId)}
                                placeholder="7d"
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── TAB: Thresholds ── */}
                    {cardTab === 'thresholds' && (
                      <div className="space-y-4 animate-in fade-in slide-in-from-left-1 duration-200">
                         <div className={`grid gap-4 relative ${cardEditSupportsOltRxPower ? 'grid-cols-2' : 'grid-cols-1'}`}>
                            {cardEditSupportsOltRxPower && (
                              <div className="absolute left-1/2 top-4 bottom-0 w-px bg-slate-100 dark:bg-slate-800/50 -translate-x-1/2"></div>
                            )}

                            <ThresholdControl
                                label="ONU RX"
                                goodKey="onu_rx_good"
                                badKey="onu_rx_bad"
                                values={cardThresholdFormForRender}
                                onChange={(key, val) => setThresholdField(oltId, key, val)}
                                t={t}
                            />
                            {cardEditSupportsOltRxPower && (
                              <ThresholdControl
                                  label="OLT RX"
                                  goodKey="olt_rx_good"
                                  badKey="olt_rx_bad"
                                  values={cardThresholdFormForRender}
                                  onChange={(key, val) => setThresholdField(oltId, key, val)}
                                  t={t}
                              />
                            )}
                         </div>

                      </div>
                    )}
                    </div>{/* End fixed height */}

                    {/* Notification messages between content and action bar */}
                    {(cardError || actionError) && (
                      <div className="flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <AlertCircle className="w-3.5 h-3.5 text-rose-400 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-rose-500 dark:text-rose-400 uppercase tracking-wider">{cardError || actionError}</p>
                      </div>
                    )}

                    {actionMessage && String(actionMessage.oltId) === String(olt.id) && !(cardError || actionError) && (
                      <div className="flex items-center justify-center gap-2 py-2 animate-in fade-in duration-300 bg-white/90 dark:bg-slate-900/90 backdrop-blur-sm rounded-b-lg">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
                        <p className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">{actionMessage.message}</p>
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
                              onClick={() => handleDiscard(oltId)}
                              disabled={!cardDirty}
                              className="h-7 px-3 rounded-lg border border-transparent text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 text-[9px] font-black uppercase tracking-wider transition-all active:scale-95 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {t('Cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={() => handleUpdate(oltId)}
                              disabled={localUpdateBusy || !cardDirty}
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
