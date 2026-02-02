<template>
  <div class="dashboard-view">
    <!-- Topology Tree -->
    <div class="dashboard-content">
      <TopologyTree
        :olts="olts"
        :loading="loading"
        :is-refreshing="isRefreshing"
        @refresh="refreshTopology"
        @add-olt="showAddOltDialog = true"
        @refresh-power="handleRefreshPower"
      />
    </div>
    
    <!-- Add OLT Dialog -->
    <v-dialog v-model="showAddOltDialog" max-width="600" persistent>
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon start>mdi-server-plus</v-icon>
          {{ t('olt.add') }}
        </v-card-title>
        
        <v-card-text>
          <v-form ref="oltForm" v-model="formValid">
            <v-row>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="newOlt.name"
                  :label="t('olt.name')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="newOlt.ip_address"
                  :label="t('olt.ipAddress')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="newOlt.vendor_profile"
                  :items="vendorProfiles"
                  item-title="name"
                  item-value="id"
                  :label="t('olt.vendor')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="newOlt.snmp_community"
                  :label="t('olt.snmpCommunity')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model.number="newOlt.snmp_port"
                  :label="t('olt.snmpPort')"
                  type="number"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="newOlt.snmp_version"
                  :items="['2c', '3']"
                  :label="t('olt.snmpVersion')"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-switch
                  v-model="newOlt.discovery_enabled"
                  :label="t('olt.discoveryEnabled')"
                  color="primary"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-switch
                  v-model="newOlt.polling_enabled"
                  :label="t('olt.pollingEnabled')"
                  color="primary"
                />
              </v-col>
            </v-row>
          </v-form>
        </v-card-text>
        
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showAddOltDialog = false">
            {{ t('olt.cancel') }}
          </v-btn>
          <v-btn
            color="primary"
            variant="flat"
            :disabled="!formValid"
            :loading="isSaving"
            @click="saveOlt"
          >
            {{ t('olt.save') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    
    <!-- Snackbar -->
    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="3000">
      {{ snackbar.message }}
      <template #actions>
        <v-btn variant="text" @click="snackbar.show = false">OK</v-btn>
      </template>
    </v-snackbar>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { t } from '@/i18n'
import api from '@/services/api'
import TopologyTree from '@/components/topology/TopologyTree.vue'

// State
const loading = ref(true)
const isRefreshing = ref(false)
const isSaving = ref(false)
const olts = ref([])
const vendorProfiles = ref([])
const showAddOltDialog = ref(false)
const formValid = ref(false)
const oltForm = ref(null)

const newOlt = ref({
  name: '',
  ip_address: '',
  vendor_profile: null,
  snmp_community: 'public',
  snmp_port: 161,
  snmp_version: '2c',
  discovery_enabled: true,
  polling_enabled: true,
})

const snackbar = ref({
  show: false,
  message: '',
  color: 'success',
})

let refreshInterval = null

// Computed
const totalOlts = computed(() => olts.value.length)

const totalOnline = computed(() => {
  return olts.value.reduce((sum, olt) => sum + (olt.online_count || 0), 0)
})

const totalOffline = computed(() => {
  return olts.value.reduce((sum, olt) => sum + (olt.offline_count || 0), 0)
})

const totalOnus = computed(() => totalOnline.value + totalOffline.value)

// Count ONUs by disconnect reason
const getOnusByReason = (reasonCheck) => {
  let count = 0
  olts.value.forEach(olt => {
    if (!olt.slots) return
    olt.slots.forEach(slot => {
      if (!slot.pons) return
      slot.pons.forEach(pon => {
        if (!pon.onus) return
        pon.onus.forEach(onu => {
          if (onu.status !== 'online' && reasonCheck(onu.disconnect_reason)) {
            count++
          }
        })
      })
    })
  })
  return count
}

const totalLinkLoss = computed(() => {
  return getOnusByReason(reason => {
    const r = (reason || '').toLowerCase()
    return r.includes('loss') || r.includes('los')
  })
})

const totalDyingGasp = computed(() => {
  return getOnusByReason(reason => {
    const r = (reason || '').toLowerCase()
    return r.includes('dying') || r.includes('gasp')
  })
})

const totalUnknown = computed(() => {
  return totalOffline.value - totalLinkLoss.value - totalDyingGasp.value
})

// Methods
const fetchTopology = async () => {
  try {
    // Fetch OLTs with nested slots/pons/onus
    const response = await api.get('/olts/', {
      params: { include_topology: true }
    })
    
    // Handle paginated response
    const data = response.data.results || response.data
    olts.value = Array.isArray(data) ? data : []
  } catch (error) {
    console.error('Failed to fetch topology:', error)
    showSnackbar(t('messages.loadError'), 'error')
  }
}

const fetchVendorProfiles = async () => {
  try {
    const response = await api.get('/vendor-profiles/')
    const data = response.data.results || response.data
    vendorProfiles.value = Array.isArray(data) ? data : []
  } catch (error) {
    console.error('Failed to fetch vendor profiles:', error)
  }
}

const loadData = async () => {
  loading.value = true
  await Promise.all([fetchTopology(), fetchVendorProfiles()])
  loading.value = false
}

const refreshTopology = async () => {
  isRefreshing.value = true
  await fetchTopology()
  isRefreshing.value = false
}

const saveOlt = async () => {
  if (!oltForm.value?.validate()) return
  
  isSaving.value = true
  try {
    await api.post('/olts/', newOlt.value)
    showSnackbar(t('messages.saved'), 'success')
    showAddOltDialog.value = false
    resetForm()
    await fetchTopology()
  } catch (error) {
    console.error('Failed to save OLT:', error)
    showSnackbar(t('messages.saveError'), 'error')
  } finally {
    isSaving.value = false
  }
}

const resetForm = () => {
  newOlt.value = {
    name: '',
    ip_address: '',
    vendor_profile: null,
    snmp_community: 'public',
    snmp_port: 161,
    snmp_version: '2c',
    discovery_enabled: true,
    polling_enabled: true,
  }
}

const handleRefreshPower = async (oltId) => {
  try {
    await api.post(`/olts/${oltId}/refresh-power/`)
    showSnackbar('Power refresh requested', 'info')
  } catch (error) {
    console.error('Failed to refresh power:', error)
  }
}

const showSnackbar = (message, color = 'success') => {
  snackbar.value = { show: true, message, color }
}

// Lifecycle
onMounted(() => {
  loadData()
  
  // Auto-refresh every 30 seconds
  refreshInterval = setInterval(() => {
    refreshTopology()
  }, 30000)
})

onUnmounted(() => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})
</script>

<style scoped>
.dashboard-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
  padding: 16px 20px 24px;
  gap: 12px;
}

/* Stats Panel */
.dashboard-content {
  flex: 1;
  overflow: hidden;
}
</style>
