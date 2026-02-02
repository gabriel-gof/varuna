<template>
  <v-container class="py-6">
    <v-row>
      <v-col cols="12" class="d-flex justify-space-between align-center">
        <div>
          <h1 class="text-h4">OLT Management</h1>
          <p class="text-body-1">Manage OLTs, edit settings and run discovery.</p>
        </div>
        <v-btn color="primary" @click="showAddDialog = true" :disabled="!canManage">
          <v-icon start>mdi-plus</v-icon>
          Add OLT
        </v-btn>
      </v-col>
    </v-row>

    <v-row v-if="!canManage" class="mb-4">
      <v-col cols="12">
        <v-alert type="warning" variant="tonal">
          Read-only access: OLT management actions are disabled.
        </v-alert>
      </v-col>
    </v-row>

    <!-- OLT List View -->
    <v-row v-if="!selectedOlt">
      <v-col cols="12">
        <v-card>
          <v-card-title class="d-flex justify-space-between align-center">
            <span>OLTs</span>
            <v-text-field
              v-model="search"
              prepend-inner-icon="mdi-magnify"
              label="Search"
              single-line
              hide-details
              density="compact"
              class="max-width-300"
              clearable
            ></v-text-field>
          </v-card-title>
          <v-data-table 
            :items="olts" 
            :headers="headers" 
            :search="search"
            item-key="id" 
            density="comfortable"
            hover
          >
            <template #item.name="{ item }">
              <div class="d-flex align-center">
                <v-icon color="primary" class="mr-2">mdi-server</v-icon>
                <strong>{{ item.name }}</strong>
              </div>
            </template>
            <template #item.ip_address="{ item }">
              <code>{{ item.ip_address }}</code>
            </template>
            <template #item.vendor="{ item }">
              {{ item.vendor_display || t('topology.unknown') }} {{ item.model_display || '' }}
            </template>
            <template #item.status="{ item }">
              <v-chip :color="item.is_active ? 'green' : 'grey'" size="small">
                {{ item.is_active ? 'Active' : 'Disabled' }}
              </v-chip>
            </template>
            <template #item.actions="{ item }">
              <v-btn icon variant="text" size="small" @click="selectOlt(item)" title="View Topology">
                <v-icon>mdi-eye</v-icon>
              </v-btn>
              <v-btn icon variant="text" size="small" @click="editOlt(item)" title="Edit" :disabled="!canManage">
                <v-icon>mdi-pencil</v-icon>
              </v-btn>
              <v-btn icon variant="text" size="small" @click="runDiscovery(item)" title="Run Discovery" :loading="discovering === item.id" :disabled="!canManage">
                <v-icon>mdi-magnify-scan</v-icon>
              </v-btn>
            </template>
            <template #no-data>
              <div class="text-center pa-6">
                <v-icon size="64" color="grey">mdi-server-off</v-icon>
                <p class="mt-4 text-body-1">No OLTs configured yet.</p>
                <v-btn color="primary" class="mt-2" @click="showAddDialog = true" :disabled="!canManage">
                  <v-icon start>mdi-plus</v-icon>
                  Add Your First OLT
                </v-btn>
              </div>
            </template>
          </v-data-table>
        </v-card>
      </v-col>
    </v-row>

    <!-- Topology View -->
    <v-row v-else>
      <v-col cols="12">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-btn icon variant="text" @click="selectedOlt = null; topology = null" class="mr-2">
              <v-icon>mdi-arrow-left</v-icon>
            </v-btn>
            <div>
              <span class="text-h6">{{ selectedOlt.name }}</span>
              <span class="text-caption ml-2 text-grey">{{ selectedOlt.ip_address }}</span>
            </div>
            <v-spacer />
            <v-chip :color="topology?.olt?.status === 'online' ? 'green' : topology?.olt?.status === 'partial' ? 'orange' : 'grey'" class="mr-2">
              {{ topology?.olt?.online_count || 0 }} {{ t('topology.online') }} / {{ topology?.olt?.offline_count || 0 }} {{ t('topology.offline') }}
            </v-chip>
            <v-btn icon variant="text" @click="loadTopology(selectedOlt)" :loading="loadingTopology">
              <v-icon>mdi-refresh</v-icon>
            </v-btn>
          </v-card-title>
          
          <v-card-text>
            <!-- Loading State -->
            <div v-if="loadingTopology" class="text-center py-6">
              <v-progress-circular indeterminate color="primary"></v-progress-circular>
              <p class="mt-2">Loading topology...</p>
            </div>

            <!-- Topology Content -->
            <template v-else-if="topology">
              <slot-panel :slots="topologySlots">
                <template #content="{ slot }">
                  <pon-panel :pons="slot.pons">
                    <template #content="{ pon }">
                      <onu-table v-if="pon.onus && pon.onus.length > 0" :onus="pon.onus" />
                      <p v-else class="text-caption text-grey pa-4">No ONUs discovered on this PON</p>
                    </template>
                  </pon-panel>
                </template>
              </slot-panel>
              
              <v-alert v-if="Object.keys(topologySlots).length === 0" type="info" class="mt-4">
                No topology data available. Try running discovery first.
              </v-alert>
            </template>

            <!-- Error State -->
            <v-alert v-else type="warning">
              Failed to load topology. Please try again.
            </v-alert>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- Add/Edit OLT Dialog -->
    <v-dialog v-model="showAddDialog" max-width="600">
      <v-card>
        <v-card-title>{{ editingOlt ? 'Edit OLT' : 'Add New OLT' }}</v-card-title>
        <v-card-text>
          <v-form ref="form">
            <v-text-field
              v-model="oltForm.name"
              label="Name"
              :rules="[v => !!v || 'Name is required']"
              required
              :disabled="!canManage"
            ></v-text-field>
            <v-text-field
              v-model="oltForm.ip_address"
              label="IP Address"
              :rules="[v => !!v || 'IP is required']"
              required
              :disabled="!canManage"
            ></v-text-field>
            <v-select
              v-model="oltForm.vendor_profile"
              :items="vendorProfiles"
              item-title="display_name"
              item-value="id"
              label="Vendor Profile"
              :rules="[v => !!v || 'Vendor is required']"
              required
              :disabled="!canManage"
            ></v-select>
            <v-text-field
              v-model="oltForm.snmp_community"
              label="SNMP Community"
              :rules="[v => !!v || 'Community is required']"
              required
              :disabled="!canManage"
            ></v-text-field>
            <v-row>
              <v-col cols="6">
                <v-text-field
                  v-model.number="oltForm.snmp_port"
                  label="SNMP Port"
                  type="number"
                  :disabled="!canManage"
                ></v-text-field>
              </v-col>
              <v-col cols="6">
                <v-select
                  v-model="oltForm.snmp_version"
                  :items="['v2c', 'v3']"
                  label="SNMP Version"
                  :disabled="!canManage"
                ></v-select>
              </v-col>
            </v-row>
            <v-switch
              v-model="oltForm.discovery_enabled"
              label="Enable Discovery"
              color="primary"
              :disabled="!canManage"
            ></v-switch>
            <v-switch
              v-model="oltForm.polling_enabled"
              label="Enable Status Polling"
              color="primary"
              :disabled="!canManage"
            ></v-switch>
          </v-form>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="closeDialog">Cancel</v-btn>
          <v-btn color="primary" @click="saveOlt" :loading="saving" :disabled="!canManage">
            {{ editingOlt ? 'Save' : 'Add' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- Snackbar for notifications -->
    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="3000">
      {{ snackbar.text }}
    </v-snackbar>
  </v-container>
</template>

<script setup>
import { onMounted, ref, computed, watch } from 'vue'
import { t } from '@/i18n'
import { useRoute } from 'vue-router'
import topologyService from '../services/topology'
import api from '../services/api'
import SlotPanel from '../components/SlotPanel.vue'
import PonPanel from '../components/PONPanel.vue'
import OnuTable from '../components/ONUTable.vue'

const route = useRoute()

const { state, fetchOlts } = topologyService
const olts = state.olts

const search = ref('')
const selectedOlt = ref(null)
const topology = ref(null)
const loadingTopology = ref(false)
const discovering = ref(null)
const vendorProfiles = ref([])
const showAddDialog = ref(false)
const editingOlt = ref(null)
const saving = ref(false)
const snackbar = ref({ show: false, text: '', color: 'success' })
const canManage = computed(() => {
  const role = (localStorage.getItem('user_role') || '').toLowerCase()
  if (!role) return true
  return !['viewer', 'read_only', 'readonly'].includes(role)
})

const oltForm = ref({
  name: '',
  ip_address: '',
  vendor_profile: null,
  snmp_community: 'public',
  snmp_port: 161,
  snmp_version: 'v2c',
  discovery_enabled: true,
  polling_enabled: true,
})

const headers = [
  { title: 'Name', key: 'name', sortable: true },
  { title: 'IP Address', key: 'ip_address', sortable: true },
  { title: 'Vendor', key: 'vendor', sortable: true },
  { title: 'Status', key: 'status', sortable: true },
  { title: 'Actions', key: 'actions', sortable: false, width: '150px' },
]

const topologySlots = computed(() => {
  if (!topology.value || !topology.value.slots) return []
  return Object.values(topology.value.slots)
})

const loadTopology = async (olt) => {
  loadingTopology.value = true
  try {
    const response = await api.get(`/olts/${olt.id}/topology/`)
    topology.value = response.data
  } catch (error) {
    console.error('Failed to load topology:', error)
    showNotification('Failed to load topology', 'error')
  } finally {
    loadingTopology.value = false
  }
}

const selectOlt = async (olt) => {
  selectedOlt.value = olt
  await loadTopology(olt)
}

const editOlt = (olt) => {
  if (!canManage.value) {
    showNotification('You do not have permission to edit OLTs', 'error')
    return
  }
  editingOlt.value = olt
  oltForm.value = {
    name: olt.name,
    ip_address: olt.ip_address,
    vendor_profile: olt.vendor_profile,
    snmp_community: olt.snmp_community,
    snmp_port: olt.snmp_port,
    snmp_version: olt.snmp_version,
    discovery_enabled: olt.discovery_enabled,
    polling_enabled: olt.polling_enabled,
  }
  showAddDialog.value = true
}

const runDiscovery = async (olt) => {
  if (!canManage.value) {
    showNotification('You do not have permission to run discovery', 'error')
    return
  }
  discovering.value = olt.id
  showNotification(`Discovery started for ${olt.name}...`, 'info')
  // In a real app, this would trigger the discovery management command
  // For now, just simulate with a delay
  setTimeout(() => {
    discovering.value = null
    showNotification(`Discovery completed for ${olt.name}`, 'success')
  }, 2000)
}

const loadVendorProfiles = async () => {
  try {
    const response = await api.get('/vendor-profiles/')
    const data = Array.isArray(response.data) ? response.data : response.data.results || []
    vendorProfiles.value = data.map(vp => ({
      ...vp,
      display_name: `${vp.vendor?.toUpperCase() || vp.vendor} - ${vp.model_name}`
    }))
  } catch (error) {
    console.error('Failed to load vendor profiles:', error)
  }
}

const closeDialog = () => {
  showAddDialog.value = false
  editingOlt.value = null
  oltForm.value = {
    name: '',
    ip_address: '',
    vendor_profile: null,
    snmp_community: 'public',
    snmp_port: 161,
    snmp_version: 'v2c',
    discovery_enabled: true,
    polling_enabled: true,
  }
}

const saveOlt = async () => {
  if (!canManage.value) {
    showNotification('You do not have permission to manage OLTs', 'error')
    return
  }
  saving.value = true
  try {
    if (editingOlt.value) {
      await api.put(`/olts/${editingOlt.value.id}/`, oltForm.value)
      showNotification('OLT updated successfully', 'success')
    } else {
      await api.post('/olts/', oltForm.value)
      showNotification('OLT added successfully', 'success')
    }
    closeDialog()
    fetchOlts()
  } catch (error) {
    console.error('Failed to save OLT:', error)
    showNotification('Failed to save OLT: ' + (error.response?.data?.detail || error.message), 'error')
  } finally {
    saving.value = false
  }
}

const showNotification = (text, color = 'success') => {
  snackbar.value = { show: true, text, color }
}

// Check for OLT ID in query params
watch(() => route.query.id, async (id) => {
  if (id) {
    const olt = olts.value.find(o => o.id === parseInt(id))
    if (olt) {
      await selectOlt(olt)
    }
  }
}, { immediate: true })

onMounted(async () => {
  await Promise.all([
    fetchOlts(),
    loadVendorProfiles()
  ])
  
  // Check if we have an OLT ID in query params
  if (route.query.id) {
    const olt = olts.value.find(o => o.id === parseInt(route.query.id))
    if (olt) {
      await selectOlt(olt)
    }
  }
})
</script>

<style scoped>
.max-width-300 {
  max-width: 300px;
}
</style>
