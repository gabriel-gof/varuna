<template>
  <v-container class="py-6">
    <v-row>
      <v-col cols="12">
        <h1 class="text-h4">Offline ONUs</h1>
        <p class="text-body-1">View currently offline ONUs with disconnect reasons and timestamps.</p>
      </v-col>
    </v-row>

    <!-- Filters -->
    <v-row class="mb-4">
      <v-col cols="12" md="3">
        <v-select
          v-model="filters.olt"
          :items="oltOptions"
          item-title="name"
          item-value="id"
          label="Filter by OLT"
          clearable
          density="compact"
        ></v-select>
      </v-col>
      <v-col cols="12" md="3">
        <v-select
          v-model="filters.reason"
          :items="reasonOptions"
          item-title="label"
          item-value="value"
          label="Filter by Reason"
          clearable
          density="compact"
        ></v-select>
      </v-col>
      <v-col cols="12" md="4">
        <v-text-field
          v-model="search"
          prepend-inner-icon="mdi-magnify"
          label="Search by name or serial"
          clearable
          density="compact"
        ></v-text-field>
      </v-col>
      <v-col cols="12" md="2" class="d-flex align-center">
        <v-btn color="primary" @click="fetchOfflineOnus" :loading="loading">
          <v-icon start>mdi-refresh</v-icon>
          Refresh
        </v-btn>
      </v-col>
    </v-row>

    <!-- Stats Cards -->
    <v-row class="mb-4">
      <v-col cols="12" sm="4">
        <v-card color="red-lighten-5" variant="flat">
          <v-card-text class="d-flex align-center justify-space-between">
            <div>
              <div class="text-h4 font-weight-bold text-red">{{ stats.total }}</div>
              <div class="text-caption">Total Offline</div>
            </div>
            <v-icon size="48" color="red-lighten-2">mdi-alert-circle</v-icon>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card color="orange-lighten-5" variant="flat">
          <v-card-text class="d-flex align-center justify-space-between">
            <div>
              <div class="text-h4 font-weight-bold text-orange">{{ stats.linkLoss }}</div>
              <div class="text-caption">Link Loss (LOS)</div>
            </div>
            <v-icon size="48" color="orange-lighten-2">mdi-link-off</v-icon>
          </v-card-text>
        </v-card>
      </v-col>
      <v-col cols="12" sm="4">
        <v-card color="purple-lighten-5" variant="flat">
          <v-card-text class="d-flex align-center justify-space-between">
            <div>
              <div class="text-h4 font-weight-bold text-purple">{{ stats.dyingGasp }}</div>
              <div class="text-caption">Dying Gasp</div>
            </div>
            <v-icon size="48" color="purple-lighten-2">mdi-power-plug-off</v-icon>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- Data Table -->
    <v-row>
      <v-col cols="12">
        <v-card>
          <v-data-table 
            :items="filteredOfflines" 
            :headers="headers" 
            :search="search"
            :loading="loading"
            density="comfortable"
            hover
          >
            <template #item.onu_name="{ item }">
              <div class="d-flex align-center">
                <v-icon color="red" class="mr-2">mdi-router-wireless-off</v-icon>
                <div>
                  <strong>{{ item.onu_name || item.onu_serial || `ONU ${item.onu_id}` }}</strong>
                  <div class="text-caption text-grey" v-if="item.onu_serial">{{ item.onu_serial }}</div>
                </div>
              </div>
            </template>
            <template #item.olt_name="{ item }">
              <v-chip size="small" variant="outlined">{{ item.olt_name }}</v-chip>
            </template>
            <template #item.location="{ item }">
              <span class="text-caption">Slot {{ item.slot_id }} / PON {{ item.pon_id }}</span>
            </template>
            <template #item.disconnect_reason="{ item }">
              <v-chip 
                :color="getReasonColor(item.disconnect_reason)" 
                size="small"
              >
                <v-icon start size="small">{{ getReasonIcon(item.disconnect_reason) }}</v-icon>
                {{ getReasonLabel(item.disconnect_reason) }}
              </v-chip>
            </template>
            <template #item.offline_since="{ item }">
              <div>
                <div>{{ formatDateTime(item.offline_since) }}</div>
                <div class="text-caption text-grey">{{ formatDuration(item.offline_since) }}</div>
              </div>
            </template>
            <template #no-data>
              <div class="text-center pa-6">
                <v-icon size="64" color="green">mdi-check-circle</v-icon>
                <p class="mt-4 text-body-1 text-green">All ONUs are online!</p>
              </div>
            </template>
          </v-data-table>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import api from '../services/api'

const offlines = ref([])
const loading = ref(false)
const search = ref('')
const filters = ref({
  olt: null,
  reason: null,
})

const headers = [
  { title: 'ONU', key: 'onu_name', sortable: true },
  { title: 'OLT', key: 'olt_name', sortable: true },
  { title: 'Location', key: 'location', sortable: false },
  { title: 'Reason', key: 'disconnect_reason', sortable: true },
  { title: 'Offline Since', key: 'offline_since', sortable: true },
]

const reasonOptions = [
  { label: 'Link Loss (LOS)', value: 'link_loss' },
  { label: 'Dying Gasp', value: 'dying_gasp' },
  { label: 'Unknown', value: 'unknown' },
]

const oltOptions = computed(() => {
  const olts = new Map()
  offlines.value.forEach(o => {
    if (o.olt_id && o.olt_name) {
      olts.set(o.olt_id, { id: o.olt_id, name: o.olt_name })
    }
  })
  return Array.from(olts.values())
})

const filteredOfflines = computed(() => {
  let result = offlines.value
  if (filters.value.olt) {
    result = result.filter(o => o.olt_id === filters.value.olt)
  }
  if (filters.value.reason) {
    result = result.filter(o => o.disconnect_reason === filters.value.reason)
  }
  return result
})

const stats = computed(() => {
  return {
    total: filteredOfflines.value.length,
    linkLoss: filteredOfflines.value.filter(o => o.disconnect_reason === 'link_loss').length,
    dyingGasp: filteredOfflines.value.filter(o => o.disconnect_reason === 'dying_gasp').length,
  }
})

const fetchOfflineOnus = async () => {
  loading.value = true
  try {
    // Fetch ONUs with offline status
    const res = await api.get('/onu/', { params: { status: 'offline' } })
    const data = Array.isArray(res.data) ? res.data : res.data.results || []
    
    // Transform to expected format with additional details
    offlines.value = data.map(onu => ({
      id: onu.id,
      onu_id: onu.onu_id,
      onu_name: onu.name,
      onu_serial: onu.serial,
      olt_id: onu.olt,
      olt_name: onu.olt_name,
      slot_id: onu.slot_id,
      pon_id: onu.pon_id,
      disconnect_reason: onu.disconnect_reason || 'unknown',
      offline_since: onu.offline_since || onu.last_discovered_at,
    }))
  } catch (e) {
    console.error('Failed to fetch offline ONUs:', e)
  } finally {
    loading.value = false
  }
}

const getReasonColor = (reason) => {
  switch (reason) {
    case 'link_loss': return 'orange'
    case 'dying_gasp': return 'purple'
    default: return 'grey'
  }
}

const getReasonIcon = (reason) => {
  switch (reason) {
    case 'link_loss': return 'mdi-link-off'
    case 'dying_gasp': return 'mdi-power-plug-off'
    default: return 'mdi-help-circle'
  }
}

const getReasonLabel = (reason) => {
  switch (reason) {
    case 'link_loss': return 'Link Loss'
    case 'dying_gasp': return 'Dying Gasp'
    default: return 'Unknown'
  }
}

const formatDateTime = (timestamp) => {
  if (!timestamp) return '-'
  return new Date(timestamp).toLocaleString('pt-BR')
}

const formatDuration = (timestamp) => {
  if (!timestamp) return ''
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now - date
  const diffMinutes = Math.floor(diffMs / (1000 * 60))
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffDays > 0) {
    return `${diffDays}d ${diffHours % 24}h ago`
  } else if (diffHours > 0) {
    return `${diffHours}h ${diffMinutes % 60}m ago`
  } else {
    return `${diffMinutes}m ago`
  }
}

// Auto-refresh
let refreshInterval = null

onMounted(() => {
  fetchOfflineOnus()
  refreshInterval = setInterval(fetchOfflineOnus, 30000) // Refresh every 30 seconds
})

onUnmounted(() => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})
</script>
