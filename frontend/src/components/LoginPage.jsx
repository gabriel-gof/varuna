import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, AlertCircle, Loader2 } from 'lucide-react'
import { VarunaIcon } from './VarunaIcon'

export function LoginPage({ onLogin }) {
  const { t } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    const frame = requestAnimationFrame(() => setMounted(true))
    return () => cancelAnimationFrame(frame)
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!username.trim() || !password) return
    setError('')
    setLoading(true)
    try {
      await onLogin(username.trim(), password)
    } catch (err) {
      const detail = err?.response?.data?.detail
      setError(detail || t('Login failed'))
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 flex items-center justify-center p-4 transition-colors duration-500 relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute -top-1/3 -right-1/4 w-[800px] h-[800px] rounded-full opacity-[0.04] dark:opacity-[0.06]"
          style={{ background: 'radial-gradient(circle, #10b981 0%, transparent 70%)' }}
        />
        <div
          className="absolute -bottom-1/3 -left-1/4 w-[600px] h-[600px] rounded-full opacity-[0.03] dark:opacity-[0.04]"
          style={{ background: 'radial-gradient(circle, #10b981 0%, transparent 70%)' }}
        />
      </div>

      <div
        className={`w-full max-w-[340px] relative transition-all duration-700 ease-out ${
          mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'
        }`}
      >
        {/* Card */}
        <div
          className={`bg-white dark:bg-slate-900 rounded-xl border border-slate-150 dark:border-slate-700/50 shadow-sm dark:shadow-slate-950/50 px-6 pt-7 pb-6 transition-all duration-700 delay-150 ${
            mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-3'
          }`}
          style={{ borderColor: 'rgba(226,232,240,0.8)' }}
        >
          {/* Brand */}
          <div
            className={`flex flex-col items-center gap-2 mb-6 transition-all duration-700 delay-100 ${
              mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'
            }`}
          >
            <VarunaIcon className="w-8 h-8 text-emerald-600 dark:text-emerald-500" />
            <span className="text-[12px] font-black text-slate-900 dark:text-white tracking-widest uppercase">
              VARUNA
            </span>
          </div>

          <form onSubmit={handleSubmit} className="flex flex-col">
            {/* Error */}
            {error && (
              <div className="flex items-center gap-2 px-3 py-2 mb-4 bg-rose-50 dark:bg-rose-500/10 border border-rose-100 dark:border-rose-500/20 rounded-lg">
                <AlertCircle className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                <span className="text-[11px] font-medium text-rose-600 dark:text-rose-400">{error}</span>
              </div>
            )}

            {/* Username */}
            <div className="flex flex-col gap-1.5">
              <label className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider pl-px">
                {t('Username')}
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                className="h-9 w-full px-3 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/80 text-slate-900 dark:text-white text-[13px] font-medium outline-none transition-all duration-200 focus:border-emerald-500 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 dark:focus:ring-emerald-500/20"
              />
            </div>

            {/* Password */}
            <div className="flex flex-col gap-1.5 mt-3.5">
              <label className="text-[10px] font-semibold text-slate-400 dark:text-slate-500 uppercase tracking-wider pl-px">
                {t('Password')}
              </label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  className="h-9 w-full px-3 pr-9 rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/80 text-slate-900 dark:text-white text-[13px] font-medium outline-none transition-all duration-200 focus:border-emerald-500 dark:focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/10 dark:focus:ring-emerald-500/20"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  tabIndex={-1}
                  className="absolute right-0.5 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center rounded text-slate-350 dark:text-slate-500 hover:text-slate-500 dark:hover:text-slate-300 active:scale-95 transition-all"
                  style={{ color: showPassword ? undefined : 'rgb(180,190,204)' }}
                >
                  {showPassword ? <EyeOff className="w-[14px] h-[14px]" /> : <Eye className="w-[14px] h-[14px]" />}
                </button>
              </div>
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !username.trim() || !password}
              className="h-9 w-full mt-5 rounded-md bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 active:scale-[0.98] dark:bg-emerald-500 dark:hover:bg-emerald-600 dark:active:bg-emerald-700 text-white text-[11px] font-black uppercase tracking-wider transition-all duration-200 flex items-center justify-center gap-2 shadow-sm shadow-emerald-600/15 hover:shadow-md hover:shadow-emerald-600/20 disabled:opacity-50 disabled:pointer-events-none"
            >
              {loading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                t('Sign in')
              )}
            </button>
          </form>
        </div>

        {/* Footer */}
        <p
          className={`text-center mt-4 text-[10px] text-slate-300 dark:text-slate-700 font-medium tracking-wide transition-all duration-700 delay-500 ${
            mounted ? 'opacity-100' : 'opacity-0'
          }`}
        >
          Varuna v1.0
        </p>
      </div>
    </div>
  )
}
