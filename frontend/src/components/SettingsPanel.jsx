import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Plus, Trash2, RefreshCcw, X, Check, AlertCircle, CheckCircle2, ChevronDown, Server } from 'lucide-react'
import { useTranslation } from 'react-i18next'

const MAX_OLT_NAME = 12

const buildInitialForm = (vendorProfiles = []) => {
  const firstVendor = vendorProfiles[0]?.vendor || ''
  const firstModel = vendorProfiles.find((item) => item.vendor === firstVendor)
  return {
    name: '',
    ip_address: '',
    vendor: firstVendor,
    vendor_profile: firstModel?.id ? String(firstModel.id) : '',
    snmp_community: 'public',
    snmp_port: '161'
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
    snmp_port: String(olt.snmp_port || 161)
  }
}

const FieldLabel = ({ children }) => (
  <span className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 select-none">
    {children}
  </span>
)

const FieldInput = React.forwardRef(({ className = '', ...props }, ref) => (
  <input
    ref={ref}
    {...props}
    className={`h-9 w-full px-3 rounded-[10px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200 placeholder:text-slate-300 dark:placeholder:text-slate-600
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all ${className}`}
  />
))

const FieldSelect = ({ className = '', children, ...props }) => (
  <select
    {...props}
    className={`h-9 w-full px-3 rounded-[10px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-400 transition-all disabled:opacity-50 ${className}`}
  >
    {children}
  </select>
)

/* Mirror NODE_HEALTH_STYLE from NetworkTopology for visual coherence */
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

/** Resolve health color: gray when SNMP unreachable, neutral while pending, green/yellow/red from ONU data */
const getOltHealth = (olt, snmpStatuses) => {
  const st = snmpStatuses?.[olt.id]
  // SNMP unreachable → gray (not red) so user doesn't confuse with actual offline ONUs
  if (st?.status === 'unreachable') return OLT_HEALTH.gray
  // Still checking → neutral
  if (!st || st.status === 'pending') return OLT_HEALTH.neutral
  // SNMP reachable → mirror topology colors based on ONU data
  return OLT_HEALTH.green
}

const OltCard = ({ olt, isSelected, health, onSelect, resolvedVendor, t, children }) => {
  const hasExpanded = isSelected && children
  return (
    <div className={`
      w-full transition-all duration-300 bg-white dark:bg-slate-900
      rounded-[12px] border
      ${isSelected ? health.borderActive : health.borderIdle}
    `}>
      <div
        onClick={() => onSelect(isSelected ? null : String(olt.id))}
        className="group/node relative flex items-center gap-2.5 px-2.5 py-2 cursor-pointer select-none"
      >
        <div className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full transition-all duration-300 ${
          isSelected ? health.accentActive : health.accentIdle
        }`} />

        {/* Server icon — mirrors topology NetworkNode */}
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
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{olt.ip_address || '\u2014'}:{olt.snmp_port || '161'}</span>
            <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{olt.snmp_community || 'public'}</span>
            <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
            <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400">{String(resolvedVendor || 'Unknown').toUpperCase()}</span>
            <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
            <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">{olt.model_display || olt.vendor_profile_name || '\u2014'}</span>
          </div>
        </div>

        <div className={`transition-transform duration-300 ${
          isSelected ? `rotate-180 ${health.chevronOpen}` : 'text-slate-300 group-hover/node:text-slate-400'
        }`}>
          <ChevronDown className="w-3 h-3" />
        </div>
      </div>
      {hasExpanded && (
        <div className="px-2.5 pb-2.5 animate-in slide-in-from-top-1 duration-200 cursor-default">
          {children}
        </div>
      )}
    </div>
  )
}

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
  actionBusy,
  isDemoMode,
  snmpStatus = {}
}) => {
  const { t } = useTranslation()
  const [showAddForm, setShowAddForm] = useState(false)
  const [selectedOltId, setSelectedOltId] = useState(null)
  const [form, setForm] = useState(() => buildInitialForm(vendorProfiles))
  const [editForm, setEditForm] = useState(null)
  const [localError, setLocalError] = useState('')
  const addNameRef = useRef(null)

  const vendorOptions = useMemo(() => {
    return [...new Set((vendorProfiles || []).map((item) => item?.vendor).filter(Boolean))]
  }, [vendorProfiles])

  const modelOptionsForVendor = useCallback((vendor) => {
    if (!vendor) return []
    return (vendorProfiles || []).filter((item) => item.vendor === vendor)
  }, [vendorProfiles])

  const modelOptions = useMemo(() => modelOptionsForVendor(form.vendor), [modelOptionsForVendor, form.vendor])
  const editModelOptions = useMemo(() => modelOptionsForVendor(editForm?.vendor), [modelOptionsForVendor, editForm?.vendor])

  // Auto-select first OLT / clear selection
  useEffect(() => {
    if (!olts.length) { setSelectedOltId(null); return }
    if (selectedOltId) {
      const exists = olts.some((item) => String(item.id) === String(selectedOltId))
      if (exists) return
    }
    // Don't auto-select; user must click
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
    if (isDemoMode) return

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
      discovery_interval_minutes: 240,
      polling_interval_seconds: 300
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
    if (isDemoMode || !selectedOltId || !editForm) return

    const payload = {
      name: String(editForm.name || '').trim().slice(0, MAX_OLT_NAME),
      ip_address: String(editForm.ip_address || '').trim(),
      vendor_profile: Number(editForm.vendor_profile),
      snmp_community: String(editForm.snmp_community || '').trim(),
      snmp_port: Number(editForm.snmp_port || 161),
    }

    if (!payload.name || !payload.ip_address || !payload.snmp_community || !Number.isFinite(payload.vendor_profile)) {
      setLocalError(t('Required fields are missing'))
      return
    }

    setLocalError('')
    const updated = await onUpdateOlt?.(selectedOltId, payload)
    if (updated) {
      // Re-run SNMP check for this OLT
      runSnmpChecks(olts.filter((o) => String(o.id) === String(selectedOltId)))
    }
  }

  const handleDelete = async (oltId) => {
    if (isDemoMode) return
    const confirmed = window.confirm(t('Do you want to remove this OLT?'))
    if (!confirmed) return
    const removed = await onDeleteOlt?.(oltId)
    if (removed) setSelectedOltId(null)
  }

  const createBusy = Boolean(actionBusy?.create)
  const updateBusy = Boolean(actionBusy?.[`update:${selectedOltId}`])
  const anyError = error || vendorError || actionError || localError

  return (
    <div className="w-full h-full overflow-y-auto custom-scrollbar">
      <div className="max-w-3xl mx-auto px-6 lg:px-10 py-8 space-y-6 animate-in fade-in duration-500">

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

        <div className="flex items-start gap-3">
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
              disabled={isDemoMode}
              className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-600/20 hover:shadow-emerald-600/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              title={t('Add OLT')}
            >
              <Plus className="w-5 h-5" />
            </button>
          </div>
          <div className="flex-shrink-0 w-8" />
        </div>

        {showAddForm && (
          <div className="flex items-start gap-3">
            <div className="w-full rounded-[12px] border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 shadow-sm px-4 py-4 space-y-3 animate-in fade-in slide-in-from-top-3 duration-300">
              {/* Row 1: Name, IP, Community, Port */}
              <div className="grid grid-cols-[1fr_1fr_1fr_4.5rem] gap-3">
                <div className="flex flex-col gap-1.5">
                  <FieldLabel>{t('OLT name')}</FieldLabel>
                  <FieldInput
                    ref={addNameRef}
                    value={form.name}
                    onChange={(e) => setField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                    maxLength={MAX_OLT_NAME}
                    placeholder="OLT-01"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <FieldLabel>{t('IP')}</FieldLabel>
                  <FieldInput
                    value={form.ip_address}
                    onChange={(e) => setField('ip_address', e.target.value)}
                    placeholder="10.0.0.1"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <FieldLabel>SNMP Community</FieldLabel>
                  <FieldInput
                    value={form.snmp_community}
                    onChange={(e) => setField('snmp_community', e.target.value)}
                    placeholder="public"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <FieldLabel>{t('Port')}</FieldLabel>
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

              {/* Row 2: Vendor, Model, Cancel, Save */}
              <div className="grid grid-cols-[1fr_1fr_1fr_4.5rem] gap-3">
                <div className="flex flex-col gap-1.5">
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
                <div className="flex flex-col gap-1.5">
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
                <div className="col-span-2 flex items-end justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setShowAddForm(false)}
                    className="h-8 px-4 rounded-[8px] text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300 text-[10px] font-black uppercase tracking-wider transition-colors hover:bg-slate-50 dark:hover:bg-slate-800 whitespace-nowrap"
                  >
                    {t('Cancel')}
                  </button>
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={isDemoMode || createBusy}
                    className="h-8 px-5 rounded-[8px] border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 text-[10px] font-black uppercase tracking-wider flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                  >
                    {createBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                    {t('Save')}
                  </button>
                </div>
              </div>
            </div>
            {/* Spacer to match the trash button column on OLT cards */}
            <div className="flex-shrink-0 w-8" />
          </div>
        )}

        <div className="space-y-2">
          {loading && !olts.length && (
            <div className="flex items-center justify-center py-20">
              <RefreshCcw className="w-5 h-5 text-slate-300 dark:text-slate-600 animate-spin" />
            </div>
          )}

          {olts.map((olt) => {
            const isSelected = String(selectedOltId) === String(olt.id)
            const health = getOltHealth(olt, snmpStatus)
            // Robust check for vendor profile
            const vp = vendorProfiles?.find(p => String(p.id) === String(olt.vendor_profile))
            const resolvedVendor = olt.vendor || olt.vendor_display || vp?.vendor || 'Unknown'

            return (
              <div key={olt.id} className="flex items-start gap-3 group/row transition-all duration-300">
                <OltCard
                  olt={olt}
                  isSelected={isSelected}
                  health={health}
                  onSelect={setSelectedOltId}
                  resolvedVendor={resolvedVendor}
                  t={t}
                >
                  {isSelected && editForm && (
                    <div className="pt-2 space-y-3 border-t border-slate-100 dark:border-slate-800/50">
                      {/* Row 1: Name, IP, Community, Port — equal thirds + small port */}
                      <div className="grid grid-cols-[1fr_1fr_1fr_4.5rem] gap-3">
                        <div className="flex flex-col gap-1.5">
                          <FieldLabel>{t('OLT name')}</FieldLabel>
                          <FieldInput
                            value={editForm.name}
                            onChange={(e) => setEditField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                            maxLength={MAX_OLT_NAME}
                            placeholder="OLT-01"
                          />
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <FieldLabel>{t('IP')}</FieldLabel>
                          <FieldInput
                            value={editForm.ip_address}
                            onChange={(e) => setEditField('ip_address', e.target.value)}
                            placeholder="10.0.0.1"
                          />
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <FieldLabel>SNMP Community</FieldLabel>
                          <FieldInput
                            value={editForm.snmp_community}
                            onChange={(e) => setEditField('snmp_community', e.target.value)}
                            placeholder="public"
                          />
                        </div>
                        <div className="flex flex-col gap-1.5">
                          <FieldLabel>{t('Port')}</FieldLabel>
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

                      {/* Row 2: Vendor, Model (match Name/IP width), Buttons far right */}
                      <div className="grid grid-cols-[1fr_1fr_1fr_4.5rem] gap-3">
                        <div className="flex flex-col gap-1.5">
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
                        <div className="flex flex-col gap-1.5">
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
                        <div className="col-span-2 flex items-end justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setSelectedOltId(null)}
                            className="h-8 px-4 rounded-[8px] text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300 text-[10px] font-black uppercase tracking-wider transition-colors hover:bg-slate-50 dark:hover:bg-slate-800 whitespace-nowrap"
                          >
                            {t('Cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={handleUpdate}
                            disabled={isDemoMode || updateBusy}
                            className="h-8 px-5 rounded-[8px] border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 text-[10px] font-black uppercase tracking-wider flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
                          >
                            {updateBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                            {t('Save')}
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </OltCard>

                <button
                  type="button"
                  onClick={() => handleDelete(olt.id)}
                  disabled={isDemoMode || Boolean(actionBusy?.[`delete:${olt.id}`])}
                  className="mt-2.5 flex-shrink-0 w-8 h-8 rounded-[10px] flex items-center justify-center text-slate-300 dark:text-slate-600 hover:text-rose-500 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-all opacity-0 group-hover/row:opacity-100 focus:opacity-100 disabled:opacity-40 disabled:cursor-not-allowed"
                  title={t('Remove OLT')}
                >
                  {Boolean(actionBusy?.[`delete:${olt.id}`]) ? <RefreshCcw className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                </button>
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
