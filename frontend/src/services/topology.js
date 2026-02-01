/**
 * Topology Service - Manages OLT list state and polling
 */
import { ref, computed } from 'vue'
import api from './api'

// Use ref for proper reactivity
const olts = ref([])
const loading = ref(false)
const error = ref(null)
const lastFetch = ref(null)

/**
 * Fetch OLT list from API
 */
const fetchOlts = async () => {
  loading.value = true
  error.value = null
  
  try {
    const res = await api.get('/olts/')
    // Handle paginated response or direct array
    if (Array.isArray(res.data)) {
      olts.value = res.data
    } else if (res.data && Array.isArray(res.data.results)) {
      olts.value = res.data.results
    } else {
      olts.value = []
    }
    lastFetch.value = new Date().toISOString()
  } catch (e) {
    console.error('Failed to fetch OLTs:', e)
    error.value = e.message || 'Failed to fetch OLTs'
    // Don't clear existing data on error
  } finally {
    loading.value = false
  }
}

/**
 * Fetch topology for a specific OLT
 */
const fetchTopology = async (oltId) => {
  try {
    const res = await api.get(`/olts/${oltId}/topology/`)
    return res.data
  } catch (e) {
    console.error(`Failed to fetch topology for OLT ${oltId}:`, e)
    throw e
  }
}

/**
 * Fetch stats for a specific OLT
 */
const fetchStats = async (oltId) => {
  try {
    const res = await api.get(`/olts/${oltId}/stats/`)
    return res.data
  } catch (e) {
    console.error(`Failed to fetch stats for OLT ${oltId}:`, e)
    throw e
  }
}

// Polling state
let pollHandle = null

/**
 * Start polling for OLT list updates
 */
const startPolling = (interval = 30000) => {
  if (pollHandle) return
  // Initial fetch
  fetchOlts()
  // Set up interval
  pollHandle = setInterval(fetchOlts, interval)
}

/**
 * Stop polling
 */
const stopPolling = () => {
  if (pollHandle) {
    clearInterval(pollHandle)
    pollHandle = null
  }
}

// Computed helpers
const totalOnus = computed(() => {
  return olts.value.reduce((acc, olt) => acc + (olt.onu_count || 0), 0)
})

const totalSlots = computed(() => {
  return olts.value.reduce((acc, olt) => acc + (olt.slot_count || 0), 0)
})

const totalPons = computed(() => {
  return olts.value.reduce((acc, olt) => acc + (olt.pon_count || 0), 0)
})

// Export as reactive state object
const state = {
  olts,
  loading,
  error,
  lastFetch
}

export default {
  state,
  fetchOlts,
  fetchTopology,
  fetchStats,
  startPolling,
  stopPolling,
  totalOnus,
  totalSlots,
  totalPons
}
