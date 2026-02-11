import React from 'react'
import { useTranslation } from 'react-i18next'
import { LayoutDashboard } from 'lucide-react'

export const Dashboard = () => {
  const { t } = useTranslation()

  return (
    <div className="w-full max-w-7xl mx-auto p-6 lg:p-10 flex flex-col items-center justify-center min-h-[60vh] animate-in fade-in duration-700">
      <LayoutDashboard className="w-16 h-16 text-slate-200 dark:text-slate-700 mb-6" />
      <h2 className="text-[14px] font-black text-slate-300 dark:text-slate-600 uppercase tracking-[0.2em] mb-2">{t('Dashboard')}</h2>
      <p className="text-[11px] font-bold text-slate-300 dark:text-slate-600 uppercase tracking-widest">{t('Coming soon')}</p>
    </div>
  )
}
