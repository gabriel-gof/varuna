/**
 * Power threshold storage and color-mapping utility.
 *
 * Thresholds determine how ONU RX and OLT RX power values are color-coded:
 *   - green:  value >= good threshold  (strong signal)
 *   - yellow: value >= bad threshold   (warning / degraded)
 *   - red:    value <  bad threshold   (critical)
 *
 * Storage: localStorage (frontend-only phase).
 * Architecture: global defaults with optional per-OLT overrides.
 */

const STORAGE_KEY_GLOBAL = 'varuna:power_thresholds'
const STORAGE_KEY_OLT = (id) => `varuna:power_thresholds:${id}`

/** Sensible GPON defaults (dBm) */
export const DEFAULT_THRESHOLDS = {
  onu_rx_good: -25,
  onu_rx_bad: -28,
  olt_rx_good: -25,
  olt_rx_bad: -28,
}

/* ─── Read / write ─── */

const getGlobalThresholds = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_GLOBAL)
    if (raw) return { ...DEFAULT_THRESHOLDS, ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return { ...DEFAULT_THRESHOLDS }
}

export const getOltThresholds = (oltId) => {
  const global = getGlobalThresholds()
  if (!oltId) return global
  try {
    const raw = localStorage.getItem(STORAGE_KEY_OLT(oltId))
    if (raw) return { ...global, ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return global
}

export const saveOltThresholds = (oltId, thresholds) => {
  try { localStorage.setItem(STORAGE_KEY_OLT(oltId), JSON.stringify(thresholds)) } catch { /* ignore */ }
}

export const hasOltOverride = (oltId) => {
  try { return !!localStorage.getItem(STORAGE_KEY_OLT(oltId)) } catch { return false }
}

/* ─── Color mapping ─── */

/**
 * Map a dBm value to a color key based on thresholds.
 * @param {number|string|null} value  - power value in dBm
 * @param {'onu_rx'|'olt_rx'} type    - which metric
 * @param {string|number} [oltId]     - OLT ID for per-OLT overrides
 * @returns {'green'|'yellow'|'red'|null}
 */
export const getPowerColor = (value, type, oltId) => {
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return null
  const t = getOltThresholds(oltId)
  const good = t[`${type}_good`]
  const bad = t[`${type}_bad`]
  if (!Number.isFinite(good) || !Number.isFinite(bad)) return null
  if (numeric >= good) return 'green'
  if (numeric >= bad) return 'yellow'
  return 'red'
}

/**
 * CSS class string for a power color.
 */
export const powerColorClass = (color) => {
  if (color === 'green') return 'text-emerald-600 dark:text-emerald-400'
  if (color === 'yellow') return 'text-yellow-600 dark:text-yellow-400'
  if (color === 'red') return 'text-rose-500 dark:text-rose-400'
  return 'text-slate-500 dark:text-slate-400'
}
