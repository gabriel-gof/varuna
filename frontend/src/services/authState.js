export const AUTH_TOKEN_STORAGE_KEY = 'auth_token'
export const AUTH_TOKEN_CLEARED_EVENT = 'varuna:auth-token-cleared'

const hasWindow = () => typeof window !== 'undefined'

const dispatchTokenCleared = (reason = 'logout') => {
  if (!hasWindow() || typeof window.dispatchEvent !== 'function') return
  window.dispatchEvent(
    new CustomEvent(AUTH_TOKEN_CLEARED_EVENT, {
      detail: { reason: String(reason || 'logout') }
    })
  )
}

export const getStoredAuthToken = () => {
  if (!hasWindow()) return null
  try {
    const token = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
    const normalized = String(token || '').trim()
    return normalized || null
  } catch {
    return null
  }
}

export const setStoredAuthToken = (token) => {
  if (!hasWindow()) return null
  const normalized = String(token || '').trim()
  try {
    if (!normalized) {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
      return null
    }
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, normalized)
    return normalized
  } catch {
    return normalized || null
  }
}

export const clearStoredAuthToken = (reason = 'logout') => {
  if (hasWindow()) {
    try {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    } catch {
      // Ignore storage failures; React state transition is driven by the event.
    }
  }
  dispatchTokenCleared(reason)
}
