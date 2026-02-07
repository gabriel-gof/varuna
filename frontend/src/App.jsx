import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  LayoutDashboard,
  Network,
  LogOut,
  User,
  ChevronDown,
  ChevronRight,
  X,
  RotateCw
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { NetworkTopology } from './components/NetworkTopology'
import { Dashboard } from './components/Dashboard'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import api from './services/api'
import { classifyOnu, getOnuStats, isZteOlt } from './utils/stats'

const normalizeList = (data) => {
  if (Array.isArray(data)) return data
  if (data && Array.isArray(data.results)) return data.results
  return []
}

const clampPonPanelWidth = (value) => {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 42
  return Math.min(68, Math.max(32, numeric))
}

const normalizeMatchValue = (value) => String(value || '').trim().toLowerCase()

const formatPowerValue = (value) => {
  if (value === null || value === undefined || value === '') return '—'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  return `${numeric.toFixed(2)} dBm`
}

const getOnuPowerSnapshot = (onu) => {
  const onuRx = onu?.onu_rx_power ?? onu?.onu_rx ?? onu?.rx_onu ?? null
  const oltRx = onu?.olt_rx_power ?? onu?.olt_rx ?? onu?.rx_olt ?? null
  const readAt = onu?.power_read_at ?? onu?.read_at ?? onu?.power_timestamp ?? null
  return { onuRx, oltRx, readAt }
}

const formatOfflineSince = (value, language) => {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(language === 'pt' ? 'pt-BR' : 'en-US', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(parsed)
}

const formatReadingAt = (value, language) => {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(language === 'pt' ? 'pt-BR' : 'en-US', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(parsed)
}

const buildClientLogin = (index) => {
  if (index === 1) return 'gabriel.gomes'
  if (index === 2) return 'alexandre.nunes'
  if (index === 3) return 'paula.macedo22'

  const firstNames = [
    'carlos', 'fernanda', 'bruno', 'mariana', 'ricardo', 'camila', 'thiago', 'renata',
    'lucas', 'juliana', 'rodrigo', 'beatriz', 'rafael', 'patricia', 'vinicius', 'larissa'
  ]
  const lastNames = [
    'silva', 'souza', 'oliveira', 'santos', 'lima', 'pereira', 'almeida', 'costa',
    'rocha', 'martins', 'barbosa', 'cardoso', 'dias', 'teixeira', 'fonseca', 'ribeiro'
  ]

  const first = firstNames[(index - 1) % firstNames.length]
  const last = lastNames[Math.floor((index - 1) / firstNames.length) % lastNames.length]
  const withNumericSuffix = index % 5 === 0 || index % 9 === 0
  const suffix = withNumericSuffix ? String((index % 90) + 10) : ''
  return `${first}.${last}${suffix}`
}

const buildTestTopology = () => {
  const testOlts = [
    {
      id: 'olt-zte-gabisat',
      name: 'OLT-ZTE',
      slotCount: 2,
      ponsPerSlot: 16,
      totalOnus: 680
    },
    {
      id: 'olt-maxprint-gabisat',
      name: 'OLT-MAXPRINT',
      slotCount: 2,
      ponsPerSlot: 16,
      totalOnus: 420
    }
  ]

  // Seeded pseudo-random for deterministic test data
  let seed = 42
  const rand = () => {
    seed = (seed * 16807 + 0) % 2147483647
    return (seed - 1) / 2147483646
  }

  let onuIndex = 1

  return testOlts.map((olt) => {
    // Distribute ONUs across PONs with realistic variation
    const totalPons = olt.slotCount * olt.ponsPerSlot
    const ponOnuCounts = []
    let remaining = olt.totalOnus
    for (let i = 0; i < totalPons; i += 1) {
      if (i === totalPons - 1) {
        ponOnuCounts.push(Math.min(128, remaining))
      } else {
        const avg = remaining / (totalPons - i)
        const count = Math.min(128, Math.max(0, Math.round(avg + (rand() - 0.5) * avg * 0.8)))
        ponOnuCounts.push(count)
        remaining -= count
      }
    }

    let ponIdx2 = 0
    const slots = []

    for (let slotIdx = 1; slotIdx <= olt.slotCount; slotIdx += 1) {
      const pons = []
      for (let ponIdx = 1; ponIdx <= olt.ponsPerSlot; ponIdx += 1) {
        const onuCount = ponOnuCounts[ponIdx2] || 0
        ponIdx2 += 1
        const onus = []

        for (let i = 0; i < onuCount; i += 1) {
          // Realistic distribution: ~75% online, ~10% dying_gasp, ~10% link_loss, ~5% unknown
          const roll = rand()
          let status = 'online'
          let reason = ''

          if (roll < 0.10) {
            status = 'offline'
            reason = 'dying_gasp'
          } else if (roll < 0.20) {
            status = 'offline'
            reason = 'link_loss'
          } else if (roll < 0.25) {
            status = 'unknown'
          }

          // Keep mock serials in realistic 12-char vendor format (e.g. FHTT106169C8).
          const simulatedSerial = `FHTT${(0x10616900 + onuIndex).toString(16).toUpperCase()}`
          const simulatedLogin = buildClientLogin(onuIndex)

          onus.push({
            id: `${olt.id}-onu-${onuIndex}`,
            onu_number: onuIndex,
            onu_id: onuIndex,
            client_name: simulatedLogin,
            name: simulatedLogin,
            serial_number: simulatedSerial,
            serial: simulatedSerial,
            status,
            disconnect_reason: reason,
            onu_rx_power: status === 'online' ? Number((-17.2 - (onuIndex % 14) * 0.27).toFixed(2)) : null,
            olt_rx_power: status === 'online' ? Number((-16.4 - (onuIndex % 11) * 0.24).toFixed(2)) : null,
            power_read_at: status === 'online'
              ? new Date(Date.now() - ((onuIndex % 30) + 1) * 60000).toISOString()
              : null,
            offline_since: status === 'online'
              ? null
              : new Date(Date.now() - ((onuIndex % 96) + 1) * 300000).toISOString()
          })
          onuIndex += 1
        }

        pons.push({
          id: `${olt.id}-pon-${slotIdx}-${ponIdx}`,
          pon_number: ponIdx,
          pon_id: ponIdx,
          onus
        })
      }

      slots.push({
        id: `${olt.id}-slot-${slotIdx}`,
        slot_number: slotIdx,
        slot_id: slotIdx,
        pons,
        pon_count: pons.length
      })
    }

    return {
      id: olt.id,
      name: olt.name,
      vendor_profile_name: 'ZTE',
      slots,
      slot_count: slots.length
    }
  })
}

const USE_TEST_DATA = true

const mapTopologyToSlots = (olt, topology) => {
  const slotsMap = topology?.slots || {}
  const slots = Object.values(slotsMap).map((slot) => {
    const slotId = `${olt.id}-${slot.slot_id}`
    const pons = Object.values(slot.pons || {}).map((pon) => {
      const ponId = `${olt.id}-${slot.slot_id}-${pon.pon_id}`
      return {
        id: ponId,
        pon_number: pon.pon_id,
        pon_id: pon.pon_id,
        pon_key: pon.pon_key,
        name: pon.pon_name,
        onus: (pon.onus || []).map((onu) => ({
          id: onu.id,
          onu_number: onu.onu_number ?? onu.onu_id,
          onu_id: onu.onu_id ?? onu.onu_number,
          client_name: onu.client_name || onu.name,
          name: onu.name,
          serial_number: onu.serial_number ?? onu.serial,
          serial: onu.serial ?? onu.serial_number,
          status: onu.status,
          disconnect_reason: onu.disconnect_reason,
          offline_since: onu.offline_since,
          onu_rx_power: onu.onu_rx_power ?? onu.onu_rx ?? onu.rx_onu ?? null,
          olt_rx_power: onu.olt_rx_power ?? onu.olt_rx ?? onu.rx_olt ?? null,
          power_read_at: onu.power_read_at ?? onu.read_at ?? onu.power_timestamp ?? null
        }))
      }
    })
    return {
      id: slotId,
      slot_number: slot.slot_id,
      slot_id: slot.slot_id,
      slot_key: slot.slot_key,
      name: slot.slot_name,
      pons
    }
  })

  return {
    ...olt,
    slots,
    slot_count: slots.length
  }
}

const collectOnusFromOlt = (olt) => {
  const onus = []
  ;(olt?.slots || []).forEach((slot) => {
    ;(slot?.pons || []).forEach((pon) => {
      ;(pon?.onus || []).forEach((onu) => onus.push(onu))
    })
  })
  return onus
}

const findPonById = (olts, ponId) => {
  for (const olt of olts) {
    for (const slot of olt?.slots || []) {
      for (const pon of slot?.pons || []) {
        if (String(pon?.id) === String(ponId)) {
          return { olt, slot, pon }
        }
      }
    }
  }
  return null
}

const VarunaIcon = ({ className }) => (
  <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className={className}>
    <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2.5" />
    <circle cx="12" cy="12" r="7" stroke="currentColor" strokeWidth="1.5" strokeDasharray="2 2" />
    <circle cx="12" cy="12" r="10.5" stroke="currentColor" strokeWidth="1" strokeDasharray="1 3" opacity="0.4" />
    <path
      d="M12 2V4M12 20V22M2 12H4M20 12H22M4.93 4.93L6.34 6.34M17.66 17.66L19.07 19.07M4.93 19.07L6.34 17.66M17.66 6.34L19.07 4.93"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  </svg>
)

const SegmentedControl = ({ options, value, onChange }) => (
  <div className="inline-flex bg-slate-100/50 dark:bg-slate-800/50 p-0.5 rounded-lg border border-slate-100 dark:border-slate-800">
    {options.map((opt) => (
      <button
        key={opt.id}
        onClick={(e) => {
          e.stopPropagation()
          onChange(opt.id)
        }}
        className={`w-24 py-2 text-[11px] font-bold uppercase tracking-wider rounded-md transition-all ${
          value === opt.id
            ? 'bg-white dark:bg-slate-700 text-emerald-600 shadow-sm ring-1 ring-black/5 dark:ring-white/5'
            : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-300'
        }`}
      >
        {opt.label}
      </button>
    ))}
  </div>
)

const App = () => {
  const { t, i18n } = useTranslation()
  const [selectedPonId, setSelectedPonId] = useState(null)
  const [selectedSearchMatch, setSelectedSearchMatch] = useState(null)
  const [isDarkMode, setIsDarkMode] = useState(false)
  const [activeTab, setActiveTab] = useState('status')
  const [activeNav, setActiveNav] = useState('dashboard')
  const [isResizingPonPanel, setIsResizingPonPanel] = useState(false)
  const [ponPanelWidth, setPonPanelWidth] = useState(() => {
    try {
      if (typeof window === 'undefined') return 42
      const saved = window.localStorage.getItem('varuna.ponSidebarWidth')
      return clampPonPanelWidth(saved ?? 42)
    } catch (_err) {
      return 42
    }
  })
  const [olts, setOlts] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const mainLayoutRef = useRef(null)
  const resizePointerIdRef = useRef(null)
  const previousBodyCursorRef = useRef('')
  const previousBodyUserSelectRef = useRef('')
  const previousHtmlCursorRef = useRef('')

  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }, [isDarkMode])

  const fetchOlts = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get('/olts/', { params: { include_topology: 'true' } })
      setOlts(normalizeList(res.data))
    } catch (err) {
      try {
        const base = await api.get('/olts/')
        const baseOlts = normalizeList(base.data)
        const enriched = await Promise.all(
          baseOlts.map(async (olt) => {
            try {
              const topoRes = await api.get(`/olts/${olt.id}/topology/`)
              return mapTopologyToSlots(olt, topoRes.data)
            } catch (_innerErr) {
              return olt
            }
          })
        )
        setOlts(enriched)
      } catch (fallbackErr) {
        setError(err?.message || fallbackErr?.message || 'Failed to load OLT data')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleRefresh = async () => {
    setIsRefreshing(true)
    await fetchOlts()
    setTimeout(() => setIsRefreshing(false), 400)
  }

  useEffect(() => {
    fetchOlts()
    const interval = setInterval(fetchOlts, 30000)
    return () => clearInterval(interval)
  }, [])

  const zteOlts = useMemo(() => olts.filter(isZteOlt), [olts])
  const testOlts = useMemo(() => buildTestTopology(), [])
  const displayOlts = USE_TEST_DATA ? testOlts : zteOlts
  const displayLoading = USE_TEST_DATA ? false : loading
  const displayError = USE_TEST_DATA ? null : error

  const overallStats = useMemo(() => {
    const allOnus = displayOlts.flatMap(collectOnusFromOlt)
    return getOnuStats(allOnus)
  }, [displayOlts])

  const oltStats = useMemo(() => {
    return displayOlts.map((olt, index) => {
      const stats = getOnuStats(collectOnusFromOlt(olt))
      return {
        id: olt.id,
        name: olt.name,
        authorized: stats.total,
        online: stats.online,
        offline: stats.offline,
        dyingGasp: stats.dyingGasp,
        linkLoss: stats.linkLoss,
        unknown: stats.unknown,
        seed: index * 7 + 10
      }
    })
  }, [displayOlts])

  const selectedPonData = useMemo(() => {
    if (!selectedPonId) return null
    return findPonById(displayOlts, selectedPonId)
  }, [displayOlts, selectedPonId])

  useEffect(() => {
    if (selectedPonId && !selectedPonData) {
      setSelectedPonId(null)
    }
  }, [selectedPonId, selectedPonData])

  const selectedSlotNumber = selectedPonData?.slot?.slot_number ?? selectedPonData?.slot?.slot_id
  const selectedPonNumber = selectedPonData?.pon?.pon_number ?? selectedPonData?.pon?.pon_id
  const selectedPonPath = [
    selectedPonData?.olt?.name || 'OLT',
    `SLOT ${selectedSlotNumber ?? '—'}`,
    `PON ${selectedPonNumber ?? '—'}`
  ]
  const isPonPanelOpen = activeNav === 'topology' && Boolean(selectedPonId)

  const updatePonPanelWidthByClientX = (clientX) => {
    const container = mainLayoutRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (!rect.width) return
    const widthFromRight = ((rect.right - clientX) / rect.width) * 100
    setPonPanelWidth(clampPonPanelWidth(widthFromRight))
  }

  const stopPonPanelResize = () => {
    resizePointerIdRef.current = null
    setIsResizingPonPanel(false)
  }

  const handlePonResizePointerDown = (event) => {
    if (event.button !== 0) return
    event.preventDefault()
    resizePointerIdRef.current = event.pointerId
    setIsResizingPonPanel(true)
    event.currentTarget.setPointerCapture(event.pointerId)
    updatePonPanelWidthByClientX(event.clientX)
  }

  const handlePonResizePointerMove = (event) => {
    if (!isResizingPonPanel) return
    if (resizePointerIdRef.current !== event.pointerId) return
    updatePonPanelWidthByClientX(event.clientX)
  }

  const handlePonResizePointerUp = (event) => {
    if (resizePointerIdRef.current !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    stopPonPanelResize()
  }

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return
      window.localStorage.setItem('varuna.ponSidebarWidth', String(ponPanelWidth))
    } catch (_err) {
      // noop
    }
  }, [ponPanelWidth])

  useEffect(() => {
    if (!isPonPanelOpen && isResizingPonPanel) {
      stopPonPanelResize()
    }
  }, [isPonPanelOpen, isResizingPonPanel])

  useEffect(() => {
    if (!isResizingPonPanel) return undefined

    previousBodyCursorRef.current = document.body.style.cursor
    previousBodyUserSelectRef.current = document.body.style.userSelect
    previousHtmlCursorRef.current = document.documentElement.style.cursor

    document.body.style.cursor = 'ew-resize'
    document.body.style.userSelect = 'none'
    document.documentElement.style.cursor = 'ew-resize'

    return () => {
      document.body.style.cursor = previousBodyCursorRef.current
      document.body.style.userSelect = previousBodyUserSelectRef.current
      document.documentElement.style.cursor = previousHtmlCursorRef.current
    }
  }, [isResizingPonPanel])

  const selectedOnus = useMemo(() => {
    const onus = selectedPonData?.pon?.onus || []
    return [...onus].sort((a, b) => (a?.onu_number ?? 0) - (b?.onu_number ?? 0))
  }, [selectedPonData])

  useEffect(() => {
    if (!selectedSearchMatch) return
    if (!selectedPonId || String(selectedSearchMatch.ponId) !== String(selectedPonId)) return

    const row = document.querySelector('[data-onu-highlight="true"]')
    if (row) {
      row.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [selectedSearchMatch, selectedPonId, selectedOnus, activeTab])

  const selectedPonStats = useMemo(() => {
    return selectedOnus.reduce((acc, onu) => {
      const { status } = classifyOnu(onu)
      if (status === 'online') acc.online++
      else acc.offline++
      return acc
    }, { online: 0, offline: 0 })
  }, [selectedOnus])

  const statusStyle = (statusKey) => {
    if (statusKey === 'online') {
      return 'bg-emerald-50 text-emerald-600'
    }
    if (statusKey === 'dying_gasp') {
      return 'bg-blue-50 text-blue-600'
    }
    if (statusKey === 'link_loss') {
      return 'bg-rose-50 text-rose-500'
    }
    if (statusKey === 'unknown') {
      return 'bg-purple-50 text-purple-500'
    }
    if (statusKey === 'offline') {
      return 'bg-rose-50 text-rose-500'
    }
    return 'bg-slate-100 text-slate-500'
  }

  const statusDot = (statusKey) => {
    if (statusKey === 'online') return 'bg-emerald-500'
    if (statusKey === 'dying_gasp') return 'bg-blue-500'
    if (statusKey === 'link_loss') return 'bg-rose-500'
    if (statusKey === 'unknown') return 'bg-purple-500'
    if (statusKey === 'offline') return 'bg-rose-500'
    return 'bg-slate-400'
  }

  return (
    <div className="h-screen bg-[#FDFDFD] dark:bg-slate-950 flex flex-col font-sans transition-colors duration-300">
      <nav className="h-16 bg-white dark:bg-slate-900 border-b border-slate-100 dark:border-slate-800 flex items-center px-6 sticky top-0 z-[100] transition-colors shadow-sm">
        <div className="flex items-center gap-3 mr-4 sm:mr-10">
          <div className="w-9 h-9 bg-emerald-600 rounded-lg flex items-center justify-center shadow-lg shadow-emerald-500/20">
            <VarunaIcon className="w-6 h-6 text-white" />
          </div>
          <span className="text-[12px] font-black text-slate-900 dark:text-white tracking-widest uppercase hidden md:block">VARUNA</span>
        </div>

        <div className="flex items-center gap-1 h-full">
          <button
            onClick={() => setActiveNav('dashboard')}
            className={`flex items-center gap-2.5 px-4 h-full transition-all relative group ${activeNav === 'dashboard' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <LayoutDashboard className="w-[18px] h-[18px]" />
            <span className="text-[12px] font-black uppercase tracking-wider hidden sm:block">{t('Dashboard')}</span>
            {activeNav === 'dashboard' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
          <button
            onClick={() => setActiveNav('topology')}
            className={`flex items-center gap-2.5 px-4 h-full transition-all relative group ${activeNav === 'topology' ? 'text-emerald-600' : 'text-slate-400 hover:text-slate-600 dark:hover:text-slate-200'}`}
          >
            <Network className="w-[18px] h-[18px] shrink-0" />
            <span className="text-[12px] font-black uppercase tracking-wider hidden sm:block">{t('Topology')}</span>
            {activeNav === 'topology' && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-emerald-600 rounded-t-full" />}
          </button>
        </div>

        <div className="flex items-center gap-3 ml-auto">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
              <button className="flex items-center gap-2.5 p-1.5 rounded-xl hover:bg-slate-50 dark:hover:bg-slate-800 transition-all group outline-none">
                <div className="w-8 h-8 rounded-lg bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600 transition-colors group-hover:bg-emerald-200 dark:group-hover:bg-emerald-800/40">
                  <User className="w-[18px] h-[18px]" />
                </div>
                <ChevronDown className="w-3.5 h-3.5 text-slate-400 transition-transform duration-200 group-data-[state=open]:rotate-180" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                className="min-w-[280px] bg-white dark:bg-slate-900 rounded-2xl p-2 shadow-2xl border border-slate-100 dark:border-slate-800 z-[200] animate-in fade-in zoom-in-95 duration-200"
                sideOffset={8}
                align="end"
              >
                <div className="px-4 py-3 mb-2 border-b border-slate-100 dark:border-slate-800">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center text-emerald-600">
                      <User className="w-5 h-5" />
                    </div>
                    <div>
                      <p className="text-[13px] font-black text-slate-900 dark:text-white leading-none mb-1">Administrator</p>
                      <p className="text-[10px] font-bold text-slate-400">admin@varuna.net</p>
                    </div>
                  </div>
                </div>

                <div className="px-3 py-2 space-y-4">
                  <div>
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-2 px-1">{t('THEME')}</span>
                    <SegmentedControl
                      value={isDarkMode ? 'dark' : 'light'}
                      onChange={(val) => setIsDarkMode(val === 'dark')}
                      options={[{ id: 'light', label: t('LIGHT') }, { id: 'dark', label: t('DARK') }]}
                    />
                  </div>
                  <div>
                    <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest block mb-2 px-1">{t('LANGUAGE')}</span>
                    <SegmentedControl
                      value={i18n.language}
                      onChange={(val) => i18n.changeLanguage(val)}
                      options={[{ id: 'pt', label: 'PORTUGUÊS' }, { id: 'en', label: 'ENGLISH' }]}
                    />
                  </div>
                </div>
                <DropdownMenu.Separator className="h-px bg-slate-100 dark:bg-slate-800 my-2 mx-2" />
                <DropdownMenu.Item className="flex items-center gap-3 px-3 py-2.5 text-[11px] font-black text-rose-500 rounded-xl cursor-pointer outline-none transition-colors hover:bg-rose-50 dark:hover:bg-rose-900/20 uppercase group">
                  <div className="w-8 h-8 rounded-lg bg-rose-100 dark:bg-rose-900/30 flex items-center justify-center text-rose-500 group-hover:bg-rose-200 dark:group-hover:bg-rose-800/50 transition-colors">
                    <LogOut className="w-4 h-4" />
                  </div>
                  <span>{t('LOGOUT')}</span>
                </DropdownMenu.Item>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
      </nav>

      <main
        ref={mainLayoutRef}
        className="flex-1 min-h-0 flex overflow-hidden"
        style={isPonPanelOpen ? { '--pon-panel-width': `${ponPanelWidth}%` } : undefined}
      >
        <section
          className={`
            min-w-0 overflow-y-auto custom-scrollbar ${isResizingPonPanel ? '' : 'transition-[width] duration-150'}
            ${isPonPanelOpen
              ? 'hidden lg:block lg:w-[calc(100%-var(--pon-panel-width))] border-r border-slate-100 dark:border-slate-800'
              : 'flex-1'}
          `}
        >
          {activeNav === 'dashboard' ? (
            <Dashboard
              stats={overallStats}
              oltStats={oltStats}
              loading={displayLoading}
              error={displayError}
              onRefresh={handleRefresh}
              isRefreshing={isRefreshing}
            />
          ) : (
            <NetworkTopology
              olts={displayOlts}
              loading={displayLoading}
              error={displayError}
              selectedPonId={selectedPonId}
              onSearchMatchSelect={setSelectedSearchMatch}
              onPonSelect={(id, options = {}) => {
                if (options?.force) {
                  setSelectedPonId(id)
                  return
                }
                setSelectedSearchMatch(null)
                setSelectedPonId((prev) => (prev === id ? null : id))
              }}
            />
          )}
        </section>

        {activeNav === 'topology' && isPonPanelOpen && (
          <div className="hidden lg:flex relative w-4 -mx-1 flex-shrink-0 items-stretch z-20">
            <button
              type="button"
              onPointerDown={handlePonResizePointerDown}
              onPointerMove={handlePonResizePointerMove}
              onPointerUp={handlePonResizePointerUp}
              onPointerCancel={handlePonResizePointerUp}
              onLostPointerCapture={stopPonPanelResize}
              onDoubleClick={() => setPonPanelWidth(42)}
              aria-label={t('Resize PON sidebar')}
              className="relative h-full w-full cursor-ew-resize touch-none focus:outline-none group"
              style={{ cursor: 'ew-resize' }}
            >
              <span
                className={`
                  absolute inset-y-0 left-1/2 -translate-x-1/2 w-[2px] rounded-full transition-colors
                  ${isResizingPonPanel
                    ? 'bg-emerald-500'
                    : 'bg-slate-200 dark:bg-slate-700 group-hover:bg-emerald-400/80'}
                `}
                style={{ cursor: 'ew-resize' }}
              />
            </button>
          </div>
        )}

        {activeNav === 'topology' && (
          <aside
            className={`
              h-full min-h-0 flex flex-col flex-shrink-0 bg-slate-100 dark:bg-slate-950 overflow-hidden ${isResizingPonPanel ? '' : 'transition-[width] duration-150'}
              ${isPonPanelOpen
                ? 'w-full lg:w-[var(--pon-panel-width)] opacity-100 border-l border-slate-100 dark:border-slate-800'
                : 'w-0 opacity-0 pointer-events-none border-l-0'}
            `}
          >
            {selectedPonId && (
              <div className="h-full min-h-0 flex flex-col">
                <div className="px-8 lg:px-10 h-[78px] border-b border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900 flex items-center">
                  <div className="w-full flex items-center justify-between gap-4">
                    <div className="min-w-0 flex items-center gap-1.5 text-[13px] font-bold uppercase tracking-wide">
                      {selectedPonPath.map((part, idx) => (
                        <React.Fragment key={`${part}-${idx}`}>
                          {idx > 0 && <ChevronRight className="w-3.5 h-3.5 text-slate-300 dark:text-slate-600" strokeWidth={2.5} />}
                          <span className={`${idx === selectedPonPath.length - 1 ? 'text-slate-900 dark:text-white' : 'text-slate-500 dark:text-slate-400'} ${idx === 0 ? 'truncate' : 'whitespace-nowrap'}`}>
                            {part}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                    <button
                      onClick={() => {
                        setSelectedSearchMatch(null)
                        setSelectedPonId(null)
                      }}
                      className="p-1.5 hover:bg-slate-50 dark:hover:bg-slate-800 rounded-lg transition-all text-slate-400 hover:text-slate-600 shrink-0"
                      aria-label={t('Close')}
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                <div className="flex-1 min-h-0 flex flex-col p-3 lg:p-4 bg-slate-100 dark:bg-slate-950 overflow-hidden">
                  <div className="flex items-center gap-2 mb-3">
                    <SegmentedControl
                      value={activeTab}
                      onChange={setActiveTab}
                      options={[
                        { id: 'status', label: t('Status') },
                        { id: 'power', label: t('Potência') }
                      ]}
                    />
                    <button
                      onClick={() => { /* TODO: trigger refresh */ }}
                      className="shrink-0 ml-auto p-2.5 rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-400 hover:text-slate-600 hover:bg-slate-50 dark:hover:bg-slate-700/50 shadow-sm transition-all active:scale-95"
                      aria-label={t('Refresh')}
                      title={t('Refresh')}
                    >
                      <RotateCw className="w-4 h-4" strokeWidth={2.5} />
                    </button>
                  </div>

                  {activeTab === 'status' ? (
                    <div className="flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-x-auto overflow-y-auto min-h-0">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <thead className="sticky top-0 z-10">
                            <tr className="bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                              <th className="w-[10%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU ID')}</th>
                              <th className="w-[24%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Client')}</th>
                              <th className="w-[18%] pl-2.5 pr-4 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Serial')}</th>
                              <th className="w-[24%] pl-4 pr-6 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Status')}</th>
                              <th className="w-[24%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Desconexão')}</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                            {selectedOnus.map((onu) => {
                              const classification = classifyOnu(onu)
                              const label = classification.label
                              const statusKey = classification.status
                              const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                              const serialValue = onu.serial_number || onu.serial || '—'
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                              const offlineSince = statusKey === 'online' ? '—' : formatOfflineSince(onu.offline_since, i18n.language)
                              const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                              const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                                (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                                (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                                (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                              ))
                              return (
                                <tr
                                  key={onu.id}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/35 hover:bg-emerald-50/80 dark:hover:bg-slate-800/70 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? { boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                      {clientLabel}
                                    </span>
                                  </td>
                                  <td className="pl-2.5 pr-4 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 font-mono whitespace-nowrap tracking-[0.01em]">
                                    {serialValue}
                                  </td>
                                  <td className="pl-4 pr-6 py-0 align-middle whitespace-nowrap">
                                    <span
                                      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-black uppercase ${statusStyle(statusKey)}`}
                                    >
                                      <div className={`w-1.5 h-1.5 rounded-full ${statusDot(statusKey)}`} />
                                      {label}
                                    </span>
                                  </td>
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums text-right">
                                    {offlineSince}
                                  </td>
                                </tr>
                              )
                            })}
                            {selectedOnus.length === 0 && (
                              <tr>
                                <td colSpan={5} className="p-8 text-center text-[11px] font-bold text-slate-400 uppercase tracking-widest">
                                  {t('No ONU data available')}
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-col w-full max-h-full rounded-xl border border-slate-200/70 dark:border-slate-800 bg-white dark:bg-slate-900 shadow-sm overflow-hidden">
                      <div className="overflow-x-auto overflow-y-auto min-h-0">
                        <table className="w-full table-fixed text-left border-collapse" style={{ minWidth: '520px' }}>
                          <thead className="sticky top-0 z-10">
                            <tr className="bg-slate-50 dark:bg-slate-800/90 border-b-2 border-slate-200 dark:border-slate-700">
                              <th className="w-[10%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('ONU ID')}</th>
                              <th className="w-[24%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('Client')}</th>
                              <th className="w-[18%] pl-2.5 pr-4 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Serial')}</th>
                              <th className="w-[24%] pl-4 pr-6 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap">{t('Power')}</th>
                              <th className="w-[24%] px-2.5 py-2 text-[11px] font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider whitespace-nowrap text-center">{t('Leitura')}</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-100/80 dark:divide-slate-800">
                            {selectedOnus.map((onu) => {
                              const clientLabel = onu.client_name || onu.login || onu.client_login || onu.name || `ONU ${onu.onu_number ?? onu.onu_id ?? ''}`.trim()
                              const serialValue = onu.serial_number || onu.serial || '—'
                              const onuNumber = onu.onu_number ?? onu.onu_id ?? '—'
                              const { onuRx, oltRx, readAt } = getOnuPowerSnapshot(onu)
                              const hasOnuRx = onuRx !== null && onuRx !== undefined && onuRx !== ''
                              const hasOltRx = oltRx !== null && oltRx !== undefined && oltRx !== ''
                              const readingAt = formatReadingAt(readAt, i18n.language)
                              const searchTargetMatchesPon = selectedSearchMatch && String(selectedSearchMatch.ponId) === String(selectedPonId)
                              const isHighlightedFromSearch = Boolean(searchTargetMatchesPon && (
                                (selectedSearchMatch.serial && normalizeMatchValue(serialValue) === normalizeMatchValue(selectedSearchMatch.serial)) ||
                                (selectedSearchMatch.onuId && Number(onuNumber) === Number(selectedSearchMatch.onuId)) ||
                                (selectedSearchMatch.clientName && normalizeMatchValue(clientLabel) === normalizeMatchValue(selectedSearchMatch.clientName))
                              ))
                              return (
                                <tr
                                  key={`power-${onu.id}`}
                                  data-onu-highlight={isHighlightedFromSearch ? 'true' : 'false'}
                                  className={`
                                    h-14 odd:bg-white even:bg-slate-50/65 dark:odd:bg-slate-900 dark:even:bg-slate-800/35 hover:bg-emerald-50/80 dark:hover:bg-slate-800/70 transition-colors
                                    ${isHighlightedFromSearch ? 'bg-emerald-50/90 dark:bg-emerald-900/25' : ''}
                                  `}
                                  style={isHighlightedFromSearch ? { boxShadow: 'inset 0 0 0 2px rgba(16, 185, 129, 0.65)' } : undefined}
                                >
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 tabular-nums text-center">
                                    {onuNumber}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle">
                                    <span className="block text-[12px] font-bold text-slate-800 dark:text-slate-100 leading-[1.15] truncate">
                                      {clientLabel}
                                    </span>
                                  </td>
                                  <td className="pl-2.5 pr-4 py-0 align-middle text-[11px] font-semibold text-slate-600 dark:text-slate-300 font-mono whitespace-nowrap tracking-[0.01em]">
                                    {serialValue}
                                  </td>
                                  <td className="pl-4 pr-6 py-0 align-middle">
                                    {!hasOnuRx && !hasOltRx ? (
                                      <span className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 tabular-nums">—</span>
                                    ) : (
                                      <div className="flex flex-col items-start gap-1 leading-snug text-left tabular-nums">
                                        <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                          <span className="inline-block w-8">{t('ONU')}</span>
                                          <span className="font-semibold text-slate-500 dark:text-slate-400">{hasOnuRx ? formatPowerValue(onuRx) : '—'}</span>
                                        </span>
                                        <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200 whitespace-nowrap">
                                          <span className="inline-block w-8">{t('OLT')}</span>
                                          <span className="font-semibold text-slate-500 dark:text-slate-400">{hasOltRx ? formatPowerValue(oltRx) : '—'}</span>
                                        </span>
                                      </div>
                                    )}
                                  </td>
                                  <td className="px-2.5 py-0 align-middle text-[11px] font-semibold text-slate-500 dark:text-slate-400 whitespace-nowrap tabular-nums text-right">
                                    {readingAt}
                                  </td>
                                </tr>
                              )
                            })}
                            {selectedOnus.length === 0 && (
                              <tr>
                                <td colSpan={5} className="p-8 text-center text-[12px] font-bold text-slate-400 uppercase tracking-widest">
                                  {t('No ONU data available')}
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </aside>
        )}
      </main>
    </div>
  )
}

export default App
