<template>
  <div class="settings-view">
    <div class="settings-content">
      <v-row>
        <v-col cols="12">
          <!-- OLT Management -->
          <v-card class="mb-4">
            <v-card-title class="d-flex align-center justify-space-between py-3 px-4">
              <div class="d-flex align-center">
                <v-icon start class="text-medium-emphasis">mdi-server-network</v-icon>
                <span class="text-h6 font-weight-regular">{{ t('settings.olts') }}</span>
              </div>
              <v-btn
                color="primary"
                variant="flat"
                size="small"
                prepend-icon="mdi-plus"
                @click="showAddOltDialog = true"
              >
                {{ t('olt.add') }}
              </v-btn>
            </v-card-title>
            
            <v-divider />
            
            <v-card-text class="pa-0">
              <v-data-table
                :headers="oltHeaders"
                :items="olts"
                :loading="loadingOlts"
                density="comfortable"
                hover
                class="elevation-0 olt-table"
              >
                <template #item.status="{ item }">
                  <v-chip
                    :color="item.polling_enabled ? 'success' : 'surface-variant'"
                    size="small"
                    variant="flat"
                    class="font-weight-medium"
                  >
                    {{ item.polling_enabled ? t('status.active') : t('status.inactive') }}
                  </v-chip>
                </template>
                
                <template #item.vendor="{ item }">
                  <div class="d-flex align-center">
                    <v-avatar size="24" color="surface-variant" class="mr-2" variant="flat">
                      <span class="text-caption font-weight-bold text-medium-emphasis">
                        {{ (item.vendor_profile_name || '?').substring(0, 1).toUpperCase() }}
                      </span>
                    </v-avatar>
                    {{ item.vendor_profile_name || '-' }}
                  </div>
                </template>

                <template #item.actions="{ item }">
                  <div class="d-flex justify-end">
                    <v-btn
                      icon
                      size="small"
                      variant="text"
                      color="medium-emphasis"
                      @click="editOlt(item)"
                    >
                      <v-icon>mdi-pencil-outline</v-icon>
                      <v-tooltip activator="parent" location="top">{{ t('olt.edit') }}</v-tooltip>
                    </v-btn>
                    <v-btn
                      icon
                      size="small"
                      variant="text"
                      color="error"
                      class="ml-1"
                      @click="confirmDeleteOlt(item)"
                    >
                      <v-icon>mdi-trash-can-outline</v-icon>
                      <v-tooltip activator="parent" location="top">{{ t('olt.delete') }}</v-tooltip>
                    </v-btn>
                  </div>
                </template>
                
                <template #no-data>
                  <div class="text-center py-8">
                    <v-icon size="48" color="surface-variant" class="mb-3">mdi-server-off</v-icon>
                    <p class="text-medium-emphasis">{{ t('messages.noData') }}</p>
                    <v-btn 
                      variant="text" 
                      color="primary" 
                      size="small" 
                      class="mt-2"
                      @click="showAddOltDialog = true"
                    >
                      {{ t('olt.add') }}
                    </v-btn>
                  </div>
                </template>
              </v-data-table>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </div>
    
    <!-- Add/Edit OLT Dialog -->
    <v-dialog v-model="showAddOltDialog" max-width="600" persistent>
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon start>{{ editingOlt ? 'mdi-pencil' : 'mdi-server-plus' }}</v-icon>
          {{ editingOlt ? t('olt.edit') : t('olt.add') }}
        </v-card-title>
        
        <v-card-text>
          <v-form ref="oltForm" v-model="formValid">
            <v-row>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="oltFormData.name"
                  :label="t('olt.name')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model="oltFormData.ip_address"
                  :label="t('olt.ipAddress')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="oltFormData.vendor_profile"
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
                  v-model="oltFormData.snmp_community"
                  :label="t('olt.snmpCommunity')"
                  :rules="[v => !!v || 'Required']"
                  required
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-text-field
                  v-model.number="oltFormData.snmp_port"
                  :label="t('olt.snmpPort')"
                  type="number"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-select
                  v-model="oltFormData.snmp_version"
                  :items="['2c', '3']"
                  :label="t('olt.snmpVersion')"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-switch
                  v-model="oltFormData.discovery_enabled"
                  :label="t('olt.discoveryEnabled')"
                  color="primary"
                />
              </v-col>
              <v-col cols="12" md="6">
                <v-switch
                  v-model="oltFormData.polling_enabled"
                  :label="t('olt.pollingEnabled')"
                  color="primary"
                />
              </v-col>
            </v-row>
          </v-form>
        </v-card-text>
        
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="closeOltDialog">
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
    
    <!-- Delete Confirmation Dialog -->
    <v-dialog v-model="showDeleteDialog" max-width="400">
      <v-card>
        <v-card-title>{{ t('olt.delete') }}</v-card-title>
        <v-card-text>{{ t('messages.confirmDelete') }}</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="showDeleteDialog = false">
            {{ t('olt.cancel') }}
          </v-btn>
          <v-btn
            color="error"
            variant="flat"
            :loading="isDeleting"
            @click="deleteOlt"
          >
            {{ t('olt.delete') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
    
    <!-- Snackbar -->
    <v-snackbar v-model="snackbar.show" :color="snackbar.color" :timeout="3000">
      {{ snackbar.message }}
    </v-snackbar>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { t } from '@/i18n'
import api from '@/services/api'

// State
const loadingOlts = ref(false)
const olts = ref([])
const vendorProfiles = ref([])

const showAddOltDialog = ref(false)
const showDeleteDialog = ref(false)
const editingOlt = ref(null)
const deletingOlt = ref(null)
const formValid = ref(false)
const isSaving = ref(false)
const isDeleting = ref(false)
const oltForm = ref(null)

const oltFormData = ref({
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

// Computed
const oltHeaders = computed(() => [
  { title: t('olt.name'), key: 'name' },
  { title: t('olt.ipAddress'), key: 'ip_address' },
  { title: t('olt.vendor'), key: 'vendor' },
  { title: 'Status', key: 'status' },
  { title: '', key: 'actions', sortable: false, width: 100 },
])

// Watchers
const fetchOlts = async () => {
  loadingOlts.value = true
  try {
    const response = await api.get('/olts/')
    const data = response.data.results || response.data
    olts.value = Array.isArray(data) ? data : []
  } catch (error) {
    console.error('Failed to fetch OLTs:', error)
    showSnackbar(t('messages.loadError'), 'error')
  } finally {
    loadingOlts.value = false
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

const resetForm = () => {
  oltFormData.value = {
    name: '',
    ip_address: '',
    vendor_profile: null,
    snmp_community: 'public',
    snmp_port: 161,
    snmp_version: '2c',
    discovery_enabled: true,
    polling_enabled: true,
  }
  editingOlt.value = null
}

const editOlt = (olt) => {
  editingOlt.value = olt
  oltFormData.value = { ...olt }
  showAddOltDialog.value = true
}

const closeOltDialog = () => {
  showAddOltDialog.value = false
  resetForm()
}

const saveOlt = async () => {
  isSaving.value = true
  try {
    if (editingOlt.value) {
      await api.put(`/olts/${editingOlt.value.id}/`, oltFormData.value)
    } else {
      await api.post('/olts/', oltFormData.value)
    }
    showSnackbar(t('messages.saved'), 'success')
    closeOltDialog()
    await fetchOlts()
  } catch (error) {
    console.error('Failed to save OLT:', error)
    showSnackbar(t('messages.saveError'), 'error')
  } finally {
    isSaving.value = false
  }
}

const confirmDeleteOlt = (olt) => {
  deletingOlt.value = olt
  showDeleteDialog.value = true
}

const deleteOlt = async () => {
  if (!deletingOlt.value) return
  
  isDeleting.value = true
  try {
    await api.delete(`/olts/${deletingOlt.value.id}/`)
    showSnackbar(t('messages.deleted'), 'success')
    showDeleteDialog.value = false
    deletingOlt.value = null
    await fetchOlts()
  } catch (error) {
    console.error('Failed to delete OLT:', error)
    showSnackbar(t('messages.error'), 'error')
  } finally {
    isDeleting.value = false
  }
}

const showSnackbar = (message, color = 'success') => {
  snackbar.value = { show: true, message, color }
}

// Lifecycle
onMounted(() => {
  fetchOlts()
  fetchVendorProfiles()
})
</script>

<style scoped>
.settings-view {
  padding: 16px 20px 24px;
  max-height: 100%;
  overflow-y: auto;
  background: transparent;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.settings-content {
  width: 100%;
}

.olt-table {
  background: transparent;
}

.olt-table :deep(.v-data-table__th),
.olt-table :deep(.v-data-table__td) {
  color: rgb(var(--v-theme-on-surface)) !important;
}
</style>
