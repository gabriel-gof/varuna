import test from 'node:test'
import assert from 'node:assert/strict'

import {
  AUTH_TOKEN_CLEARED_EVENT,
  AUTH_TOKEN_STORAGE_KEY,
  clearStoredAuthToken,
  getStoredAuthToken,
  setStoredAuthToken,
} from './authState.js'

const originalWindow = globalThis.window
const originalCustomEvent = globalThis.CustomEvent

const withMockWindow = (callback) => {
  const store = new Map()
  const events = []
  globalThis.window = {
    localStorage: {
      getItem(key) {
        return store.has(key) ? store.get(key) : null
      },
      setItem(key, value) {
        store.set(key, String(value))
      },
      removeItem(key) {
        store.delete(key)
      },
    },
    dispatchEvent(event) {
      events.push(event)
      return true
    },
  }
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
      this.type = type
      this.detail = init.detail
    }
  }

  try {
    callback({ store, events })
  } finally {
    globalThis.window = originalWindow
    globalThis.CustomEvent = originalCustomEvent
  }
}

test('getStoredAuthToken normalizes missing and blank values', () => {
  assert.equal(getStoredAuthToken(), null)

  withMockWindow(({ store }) => {
    store.set(AUTH_TOKEN_STORAGE_KEY, '   ')
    assert.equal(getStoredAuthToken(), null)
  })
})

test('setStoredAuthToken stores normalized values', () => {
  withMockWindow(({ store }) => {
    const token = setStoredAuthToken(' abc123 ')
    assert.equal(token, 'abc123')
    assert.equal(store.get(AUTH_TOKEN_STORAGE_KEY), 'abc123')
    assert.equal(getStoredAuthToken(), 'abc123')
  })
})

test('clearStoredAuthToken removes the token and emits an auth-clear event', () => {
  withMockWindow(({ store, events }) => {
    setStoredAuthToken('token-1')
    clearStoredAuthToken('unauthorized')

    assert.equal(store.has(AUTH_TOKEN_STORAGE_KEY), false)
    assert.equal(events.length, 1)
    assert.equal(events[0].type, AUTH_TOKEN_CLEARED_EVENT)
    assert.deepEqual(events[0].detail, { reason: 'unauthorized' })
  })
})
