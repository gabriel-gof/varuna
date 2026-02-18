import React, { useEffect, useRef, useState } from 'react'
import { Pencil, Loader2 } from 'lucide-react'

export const InlineEditableText = ({ value, placeholder, onSave }) => {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!editing) setDraft(value)
  }, [value, editing])

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    }
  }, [editing])

  const commit = async () => {
    const trimmed = draft.trim()
    if (trimmed === (value || '').trim()) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(trimmed)
    } finally {
      setSaving(false)
      setEditing(false)
    }
  }

  const cancel = () => {
    setDraft(value)
    setEditing(false)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      commit()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      cancel()
    }
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1.5 min-w-0 w-full">
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={handleKeyDown}
          disabled={saving}
          className="h-7 flex-1 min-w-0 bg-white dark:bg-slate-800 border border-emerald-400/60 dark:border-emerald-500/40 rounded-md px-2 text-[13px] font-bold tracking-wide text-slate-700 dark:text-slate-200 placeholder:text-slate-400/50 placeholder:font-bold focus:outline-none focus:ring-2 focus:ring-emerald-500/20 transition-all"
          placeholder={placeholder}
        />
        {saving && (
          <Loader2 className="w-3 h-3 text-emerald-500 animate-spin shrink-0" />
        )}
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="group/desc flex items-center gap-1.5 min-w-0 max-w-full rounded-md px-1 -mx-1 py-0.5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
    >
      <span
        className={`text-[13px] font-bold tracking-wide truncate leading-none ${
          value
            ? 'text-slate-500 dark:text-slate-400'
            : 'text-slate-400/50 dark:text-slate-500/50'
        }`}
      >
        {value || placeholder}
      </span>
      <Pencil className="w-3 h-3 shrink-0 text-slate-300 dark:text-slate-600 opacity-0 group-hover/desc:opacity-100 transition-opacity" />
    </button>
  )
}
