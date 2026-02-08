import React, { useEffect, useMemo, useState } from 'react'
import { Plus, Trash2, RefreshCcw, Server, X, Check, AlertCircle, CheckCircle2 } from 'lucide-react'
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

const FieldLabel = ({ children }) => (
  <span className="text-[10px] font-black uppercase tracking-wider text-slate-400 dark:text-slate-500 select-none">
    {children}
  </span>
)

const FieldInput = ({ className = '', ...props }) => (
  <input
    {...props}
    className={`h-8 w-full px-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-[#F8FAFB] dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200 placeholder:text-slate-300 dark:placeholder:text-slate-600
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500/40 transition-all ${className}`}
  />
)

const FieldSelect = ({ className = '', children, ...props }) => (
  <select
    {...props}
    className={`h-8 w-full px-2.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-[#F8FAFB] dark:bg-slate-800/60
      text-[11px] font-semibold text-slate-800 dark:text-slate-200
      focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500/40 transition-all disabled:opacity-50 ${className}`}
  >
    {children}
  </select>
)

const OltCard = ({ olt, isSelected, onSelect, onDelete, deleteBusy, isDemoMode, t }) => (
  <div
    onClick={() => onSelect(String(olt.id))}
    className={`
      group relative flex items-center gap-3 px-3 py-3 rounded-[14px] border cursor-pointer transition-all duration-200
      ${isSelected
        ? 'bg-emerald-50/60 dark:bg-emerald-500/8 border-emerald-400/30 dark:border-emerald-500/25 shadow-sm shadow-emerald-500/5'
        : 'bg-white dark:bg-slate-900 border-slate-100 dark:border-slate-800 hover:border-slate-200 dark:hover:border-slate-700 shadow-sm'}
    `}
  >
    <div className={`absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-6 rounded-r-full transition-all duration-200 ${
      isSelected ? 'bg-emerald-500 scale-y-100' : 'bg-slate-200 dark:bg-slate-700 scale-y-50 group-hover:scale-y-75'
    }`} />

    <div className={`flex-shrink-0 w-9 h-9 flex items-center justify-center rounded-[10px] transition-all duration-200 ${
      isSelected
        ? 'bg-emerald-600 dark:bg-emerald-500 text-white shadow-md shadow-emerald-600/20'
        : 'bg-[#F4F7FA] dark:bg-slate-800 text-slate-400 group-hover:text-slate-500'
    }`}>
      <Server className="w-4.5 h-4.5" />
    </div>

    <div className="flex-1 min-w-0">
      <p className={`text-[11px] font-black uppercase tracking-tight leading-none mb-1 transition-colors ${
        isSelected ? 'text-emerald-700 dark:text-emerald-400' : 'text-slate-900 dark:text-white'
      }`}>
        {olt.name || '\u2014'}
      </p>
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 tabular-nums">{olt.ip_address || '\u2014'}</span>
        <span className="w-[3px] h-[3px] rounded-full bg-slate-200 dark:bg-slate-700" />
        <span className="text-[10px] font-semibold text-slate-400 dark:text-slate-500">{olt.vendor_display || olt.model_display || olt.vendor_profile_name || '\u2014'}</span>
      </div>
    </div>

    <div className="hidden sm:flex items-center gap-1.5 pr-1">
      <span className="text-[9px] font-bold uppercase tracking-wider text-slate-300 dark:text-slate-600">SNMP</span>
      <span className="text-[10px] font-bold text-slate-500 dark:text-slate-400 tabular-nums">{olt.snmp_community || 'public'}</span>
      <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 tabular-nums">:{olt.snmp_port || '161'}</span>
    </div>

    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onDelete(olt.id)
      }}
      disabled={isDemoMode || deleteBusy}
      className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center text-slate-300 dark:text-slate-600 hover:text-rose-500 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
      title={t('Remove OLT')}
    >
      {deleteBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
    </button>
  </div>
)

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
  onDeleteOlt,
  actionBusy,
  isDemoMode
}) => {
  const { t } = useTranslation()
  const [showAddForm, setShowAddForm] = useState(false)
  const [selectedOltId, setSelectedOltId] = useState(null)
  const [form, setForm] = useState(() => buildInitialForm(vendorProfiles))
  const [localError, setLocalError] = useState('')

  const vendorOptions = useMemo(() => {
    return [...new Set((vendorProfiles || []).map((item) => item?.vendor).filter(Boolean))]
  }, [vendorProfiles])

  const modelOptions = useMemo(() => {
    if (!form.vendor) return []
    return (vendorProfiles || []).filter((item) => item.vendor === form.vendor)
  }, [vendorProfiles, form.vendor])

  useEffect(() => {
    if (!olts.length) {
      setSelectedOltId(null)
      return
    }
    const exists = olts.some((item) => String(item.id) === String(selectedOltId))
    if (!exists) setSelectedOltId(String(olts[0].id))
  }, [olts, selectedOltId])

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

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))

  const handleVendorChange = (nextVendor) => {
    const nextModel = (vendorProfiles || []).find((item) => item.vendor === nextVendor)
    setForm((prev) => ({
      ...prev,
      vendor: nextVendor,
      vendor_profile: nextModel?.id ? String(nextModel.id) : ''
    }))
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
      polling_enabled: true
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

  const handleDelete = async (oltId) => {
    if (isDemoMode) return
    const confirmed = window.confirm(t('Do you want to remove this OLT?'))
    if (!confirmed) return
    const removed = await onDeleteOlt?.(oltId)
    if (removed) setSelectedOltId(null)
  }

  const createBusy = Boolean(actionBusy?.create)
  const anyError = error || vendorError || actionError || localError

  return (
    <div className="w-full h-full flex flex-col">
      <div className="flex-shrink-0 flex items-center gap-2 px-4 lg:px-5 h-[52px] border-b border-slate-100 dark:border-slate-800 bg-white/80 dark:bg-[#0B0F14]/80 backdrop-blur-sm">
        <div className="flex items-center gap-2 mr-auto">
          <div className="w-6 h-6 rounded-lg bg-emerald-600 dark:bg-emerald-500 flex items-center justify-center">
            <Server className="w-3.5 h-3.5 text-white" />
          </div>
          <p className="text-[11px] font-black uppercase tracking-wider text-slate-700 dark:text-slate-200">{t('OLT management')}</p>
          {olts.length > 0 && (
            <span className="ml-1 text-[10px] font-bold text-slate-300 dark:text-slate-600 tabular-nums">{olts.length}</span>
          )}
        </div>

        <button
          type="button"
          onClick={() => setShowAddForm((prev) => !prev)}
          disabled={isDemoMode}
          className={`h-8 px-3 rounded-[10px] flex items-center gap-1.5 text-[10px] font-black uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed ${
            showAddForm
              ? 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700'
              : 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-md shadow-emerald-600/20'
          }`}
          title={showAddForm ? t('Close') : t('Add OLT')}
        >
          {showAddForm ? (
            <>
              <X className="w-3.5 h-3.5" />
              <span className="hidden sm:block">{t('Close')}</span>
            </>
          ) : (
            <>
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:block">{t('Add OLT')}</span>
            </>
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 lg:px-5 py-5 space-y-4">

          {anyError && (
            <div className="flex items-start gap-2.5 rounded-[12px] border border-rose-200 dark:border-rose-500/20 bg-rose-50/80 dark:bg-rose-500/8 px-3.5 py-2.5 animate-in fade-in slide-in-from-top-2 duration-300">
              <AlertCircle className="w-4 h-4 text-rose-500 dark:text-rose-400 flex-shrink-0 mt-0.5" />
              <p className="text-[11px] font-bold text-rose-600 dark:text-rose-300 leading-relaxed">{anyError}</p>
            </div>
          )}

          {actionMessage && (
            <div className="flex items-start gap-2.5 rounded-[12px] border border-emerald-200 dark:border-emerald-500/20 bg-emerald-50/80 dark:bg-emerald-500/8 px-3.5 py-2.5 animate-in fade-in slide-in-from-top-2 duration-300">
              <CheckCircle2 className="w-4 h-4 text-emerald-500 dark:text-emerald-400 flex-shrink-0 mt-0.5" />
              <p className="text-[11px] font-bold text-emerald-700 dark:text-emerald-300 leading-relaxed">{actionMessage}</p>
            </div>
          )}

          {showAddForm && (
            <div className="rounded-[14px] border border-emerald-200/50 dark:border-emerald-500/15 bg-gradient-to-b from-emerald-50/30 to-white dark:from-emerald-500/5 dark:to-slate-900 shadow-sm px-3.5 py-3 space-y-2.5 animate-in fade-in slide-in-from-top-3 duration-300">
              <p className="text-[10px] font-black uppercase tracking-widest text-emerald-600 dark:text-emerald-400 mb-1">{t('New OLT')}</p>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <label className="flex flex-col gap-1">
                  <FieldLabel>{t('OLT name')}</FieldLabel>
                  <FieldInput
                    value={form.name}
                    onChange={(e) => setField('name', e.target.value.slice(0, MAX_OLT_NAME))}
                    maxLength={MAX_OLT_NAME}
                    placeholder="OLT-01"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <FieldLabel>{t('IP')}</FieldLabel>
                  <FieldInput
                    value={form.ip_address}
                    onChange={(e) => setField('ip_address', e.target.value)}
                    placeholder="10.0.0.1"
                  />
                </label>
                <label className="flex flex-col gap-1">
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
                </label>
                <label className="flex flex-col gap-1">
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
                </label>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <label className="flex flex-col gap-1 sm:col-span-2">
                  <FieldLabel>{t('SNMP community')}</FieldLabel>
                  <FieldInput
                    value={form.snmp_community}
                    onChange={(e) => setField('snmp_community', e.target.value)}
                    placeholder="public"
                  />
                </label>
                <div className="flex items-end gap-2 sm:col-span-2">
                  <button
                    type="button"
                    onClick={handleCreate}
                    disabled={isDemoMode || createBusy}
                    className="h-8 px-3.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] font-black uppercase tracking-wider shadow-md shadow-emerald-600/20 flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {createBusy ? <RefreshCcw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                    {t('Create OLT')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowAddForm(false)}
                    className="h-8 px-3.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-500 dark:text-slate-400 text-[10px] font-black uppercase tracking-wider hover:text-slate-700 dark:hover:text-slate-200 transition-colors"
                  >
                    {t('Close')}
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2">
            {loading && !olts.length && (
              <div className="flex items-center justify-center py-20">
                <RefreshCcw className="w-5 h-5 text-slate-300 dark:text-slate-600 animate-spin" />
              </div>
            )}

            {olts.map((olt) => (
              <OltCard
                key={olt.id}
                olt={olt}
                isSelected={String(selectedOltId) === String(olt.id)}
                onSelect={setSelectedOltId}
                onDelete={handleDelete}
                deleteBusy={Boolean(actionBusy?.[`delete:${olt.id}`])}
                isDemoMode={isDemoMode}
                t={t}
              />
            ))}

            {!olts.length && !loading && (
              <div className="flex flex-col items-center justify-center py-20 gap-3">
                <div className="w-12 h-12 rounded-2xl bg-slate-50 dark:bg-slate-800 flex items-center justify-center">
                  <Server className="w-6 h-6 text-slate-300 dark:text-slate-600" />
                </div>
                <p className="text-[11px] font-black uppercase tracking-widest text-slate-300 dark:text-slate-600">
                  {t('No OLTs registered')}
                </p>
              </div>
            )}
          </div>

        </div>
      </div>
    </div>
  )
}
