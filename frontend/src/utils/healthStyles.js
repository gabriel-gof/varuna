/**
 * Shared health-state visual styles used by both NetworkTopology and SettingsPanel.
 * Each state maps to a set of Tailwind class strings for borders, accents, icons, labels, and chevrons.
 */
export const HEALTH_STYLES = {
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

export const resolveHealthStyle = (healthState) => {
  return HEALTH_STYLES[healthState] || HEALTH_STYLES.green
}
