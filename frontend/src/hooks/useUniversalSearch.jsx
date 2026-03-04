import { useMemo, useState } from 'react'

const asList = (value) => (Array.isArray(value) ? value : Object.values(value || {}))

export const normalizeSearch = (value) => String(value || '').toLowerCase().trim()

const scoreSearchMatch = (rawValue, term) => {
  const value = normalizeSearch(rawValue)
  if (!value || !term) return -1
  if (value === term) return 1000
  if (value.startsWith(term)) return 700
  const index = value.indexOf(term)
  if (index === -1) return -1
  return Math.max(200 - index, 1)
}

const SEARCH_STATUS_WEIGHT = {
  online: 3,
  offline: 2,
  unknown: 1
}

const getSearchStatusWeight = (status) => SEARCH_STATUS_WEIGHT[normalizeSearch(status)] || 0

const toEpochMillis = (value) => {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : 0
}

const shouldReplaceSearchSuggestion = (current, candidate) => {
  if (!current) return true
  if (candidate.score !== current.score) return candidate.score > current.score

  const candidateStatusWeight = getSearchStatusWeight(candidate.status)
  const currentStatusWeight = getSearchStatusWeight(current.status)
  if (candidateStatusWeight !== currentStatusWeight) return candidateStatusWeight > currentStatusWeight

  if (candidate.powerReadAtMs !== current.powerReadAtMs) return candidate.powerReadAtMs > current.powerReadAtMs
  return candidate.key < current.key
}

export const renderHighlightedText = (value, term) => {
  const source = String(value || '')
  const normalizedTerm = normalizeSearch(term)
  if (!normalizedTerm || !source) return source

  const lowerSource = source.toLowerCase()
  const parts = []
  let cursor = 0
  let key = 0

  while (cursor < source.length) {
    const matchIndex = lowerSource.indexOf(normalizedTerm, cursor)
    if (matchIndex === -1) {
      parts.push(<span key={`plain-${key++}`}>{source.slice(cursor)}</span>)
      break
    }

    if (matchIndex > cursor) {
      parts.push(<span key={`plain-${key++}`}>{source.slice(cursor, matchIndex)}</span>)
    }

    const matchEnd = matchIndex + normalizedTerm.length
    parts.push(
      <mark
        key={`match-${key++}`}
        className="px-[1px] rounded-sm bg-emerald-100 text-emerald-700 dark:bg-emerald-400/20 dark:text-emerald-300"
      >
        {source.slice(matchIndex, matchEnd)}
      </mark>
    )
    cursor = matchEnd
  }

  return parts
}

const asCount = (value) => {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : 0
}

export const useUniversalSearch = (olts) => {
  const [searchTerm, setSearchTerm] = useState('')
  const [searchFocused, setSearchFocused] = useState(false)

  const normalizedSearchTerm = normalizeSearch(searchTerm)

  const suggestions = useMemo(() => {
    if (!normalizedSearchTerm) return []

    const dedupedSuggestions = new Map()
    asList(olts).forEach((olt) => {
      asList(olt?.slots).forEach((slot) => {
        asList(slot?.pons).forEach((pon) => {
          asList(pon?.onus).forEach((onu) => {
            const clientName = onu?.client_name || onu?.name || ''
            const serial = onu?.serial || onu?.serial_number || ''
            const rawOnuId = asCount(onu?.onu_number ?? onu?.onu_id)
            const onuId = rawOnuId >= 1 && rawOnuId <= 128 ? rawOnuId : '-'
            const loginScore = scoreSearchMatch(clientName, normalizedSearchTerm)
            const serialScore = scoreSearchMatch(serial, normalizedSearchTerm)
            const bestScore = Math.max(loginScore, serialScore)
            if (bestScore < 0) return

            const slotNumber = slot.slot_number ?? slot.slot_id ?? slot.id
            const ponNumber = pon.pon_number ?? pon.pon_id ?? pon.id
            const key = `${olt.id}-${slot.id}-${pon.id}-${onu?.id || serial || clientName}`
            const normalizedSerial = normalizeSearch(serial)
            const dedupeKey = normalizedSerial || `path:${key}`
            const suggestion = {
              key,
              clientName: clientName || '-',
              serial: serial || '-',
              oltId: olt.id,
              oltName: olt.name,
              slotId: slot.id,
              slotNumber,
              ponId: pon.id,
              ponNumber,
              onuId,
              status: onu?.status,
              powerReadAt: onu?.power_read_at,
              powerReadAtMs: toEpochMillis(onu?.power_read_at),
              matchType: serialScore > loginScore ? 'serial' : 'login',
              score: bestScore + (serialScore > loginScore ? 10 : 0),
            }

            const current = dedupedSuggestions.get(dedupeKey)
            if (shouldReplaceSearchSuggestion(current, suggestion)) {
              dedupedSuggestions.set(dedupeKey, suggestion)
            }
          })
        })
      })
    })

    return Array.from(dedupedSuggestions.values())
      .sort((a, b) => b.score - a.score || a.clientName.localeCompare(b.clientName) || a.serial.localeCompare(b.serial))
      .slice(0, 7)
  }, [olts, normalizedSearchTerm])

  const clearSearch = () => {
    setSearchTerm('')
    setSearchFocused(false)
  }

  return {
    searchTerm,
    setSearchTerm,
    searchFocused,
    setSearchFocused,
    suggestions,
    normalizedSearchTerm,
    clearSearch,
  }
}
