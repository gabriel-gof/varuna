<template>
  <v-container class="py-6">
    <v-row class="align-center mb-4">
      <v-col cols="8">
        <h1 class="text-h4">Network Dashboard</h1>
        <p class="text-body-1">Overview of OLTs, topology and quick statistics.</p>
      </v-col>
      <v-col cols="4" class="text-right">
        <v-btn color="primary" @click="refresh" :loading="loading">
          <v-icon start>mdi-refresh</v-icon>
          Refresh
        </v-btn>
      </v-col>
    </v-row>

    <!-- Loading State -->
    <v-row v-if="loading && olts.length === 0">
      <v-col cols="12" class="text-center py-10">
        <v-progress-circular indeterminate color="primary" size="64"></v-progress-circular>
        <p class="mt-4">Loading OLTs...</p>
      </v-col>
    </v-row>

    <!-- Error State -->
    <v-row v-else-if="error && olts.length === 0">
      <v-col cols="12">
        <v-alert type="error" closable @click:close="error = null">
          {{ error }}
        </v-alert>
      </v-col>
    </v-row>

    <!-- Content -->
    <template v-else>
      <v-row>
        <v-col cols="12" md="8">
          <v-row v-if="olts.length > 0">
            <v-col cols="12" sm="6" md="4" v-for="olt in olts" :key="olt.id">
              <olt-card :olt="olt" @click="openOlt(olt)" />
            </v-col>
          </v-row>
          <v-row v-else>
            <v-col cols="12">
              <v-card class="pa-6 text-center">
                <v-icon size="64" color="grey">mdi-server-network-off</v-icon>
                <h3 class="mt-4">No OLTs Configured</h3>
                <p class="text-body-2 mt-2">Add your first OLT to get started monitoring your network.</p>
                <v-btn color="primary" class="mt-4" @click="goToManagement">
                  <v-icon start>mdi-plus</v-icon>
                  Add OLT
                </v-btn>
              </v-card>
            </v-col>
          </v-row>
        </v-col>

        <v-col cols="12" md="4">
          <v-card>
            <v-card-title>
              <v-icon start>mdi-chart-box</v-icon>
              Statistics
            </v-card-title>
            <v-card-text>
              <v-list density="compact">
                <v-list-item>
                  <template v-slot:prepend>
                    <v-icon color="primary">mdi-server</v-icon>
                  </template>
                  <v-list-item-title>OLTs</v-list-item-title>
                  <template v-slot:append>
                    <span class="font-weight-bold">{{ olts.length }}</span>
                  </template>
                </v-list-item>
                <v-list-item>
                  <template v-slot:prepend>
                    <v-icon color="primary">mdi-ethernet</v-icon>
                  </template>
                  <v-list-item-title>Total ONUs</v-list-item-title>
                  <template v-slot:append>
                    <span class="font-weight-bold">{{ totalOnus }}</span>
                  </template>
                </v-list-item>
              </v-list>
            </v-card-text>
          </v-card>

          <!-- Quick Links -->
          <v-card class="mt-4">
            <v-card-title>
              <v-icon start>mdi-link</v-icon>
              Quick Links
            </v-card-title>
            <v-card-text>
              <v-btn block variant="outlined" class="mb-2" to="/olt-management">
                <v-icon start>mdi-cog</v-icon>
                OLT Management
              </v-btn>
              <v-btn block variant="outlined" to="/offline-onus" color="error">
                <v-icon start>mdi-alert-circle</v-icon>
                {{ t('offline.title') }}
              </v-btn>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <!-- Topology Overview -->
      <v-row class="mt-6" v-if="olts.length > 0">
        <v-col cols="12">
          <v-card>
            <v-card-title>
              <v-icon start>mdi-sitemap</v-icon>
              Network Topology Overview
            </v-card-title>
            <v-card-text>
              <v-row>
                <v-col cols="12" sm="6" md="3" v-for="olt in olts" :key="olt.id">
                  <v-card variant="outlined" class="mb-4 olt-overview-card" @click="openOlt(olt)">
                    <v-card-title class="text-subtitle-1 pb-1">{{ olt.name }}</v-card-title>
                    <v-card-text class="pt-0">
                      <div class="text-caption text-grey">{{ olt.vendor_display || t('topology.unknown') }} {{ olt.model_display || '' }}</div>
                      <v-divider class="my-2" />
                      <v-row class="text-center" dense>
                        <v-col cols="6">
                          <div class="text-h6">{{ olt.slot_count || '-' }}</div>
                          <div class="text-caption">Slots</div>
                        </v-col>
                        <v-col cols="6">
                          <div class="text-h6">{{ olt.pon_count || '-' }}</div>
                          <div class="text-caption">PONs</div>
                        </v-col>
                      </v-row>
                    </v-card-text>
                  </v-card>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </v-container>
</template>

<script setup>
import { onMounted, onUnmounted, computed } from 'vue'
import { t } from '@/i18n'
import { useRouter } from 'vue-router'
import topologyService from '../services/topology'
import OltCard from '../components/OLTCard.vue'

const router = useRouter()

const { state, fetchOlts, startPolling, stopPolling } = topologyService

const olts = state.olts
const loading = state.loading
const error = state.error

const refresh = () => fetchOlts()

const totalOnus = computed(() => {
  return olts.value.reduce((acc, o) => acc + (o.onu_count || 0), 0)
})

const openOlt = (olt) => {
  router.push({ name: 'olt-management', query: { id: olt.id } })
}

const goToManagement = () => {
  router.push({ name: 'olt-management' })
}

onMounted(() => {
  startPolling(30000)
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.olt-overview-card {
  cursor: pointer;
  transition: all 0.2s;
}
.olt-overview-card:hover {
  border-color: rgb(var(--v-theme-primary));
}
</style>
