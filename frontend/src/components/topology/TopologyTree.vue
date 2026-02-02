<template>
  <div class="topology-tree">
    <!-- Toolbar -->
    <div class="topology-toolbar">
      <v-row align="center" no-gutters>
        <v-col cols="12" md="4">
          <v-text-field
            v-model="searchQuery"
            :placeholder="t('dashboard.search')"
            density="compact"
            variant="outlined"
            hide-details
            clearable
            prepend-inner-icon="mdi-magnify"
            class="search-field"
          />
        </v-col>
        
        <v-col cols="12" md="3" class="pl-md-3 pt-2 pt-md-0">
          <v-select
            v-model="filterOlt"
            :items="oltFilterOptions"
            :label="t('dashboard.filterByOlt')"
            density="compact"
            variant="outlined"
            hide-details
            clearable
          />
        </v-col>
        
        <v-col cols="12" md="5" class="pl-md-3 pt-2 pt-md-0 d-flex justify-end align-center toolbar-actions">
          <v-checkbox
            v-model="showOfflineOnly"
            :label="t('dashboard.showOfflineOnly')"
            density="compact"
            hide-details
            class="mr-4"
          />
          
        </v-col>
      </v-row>
    </div>
    
    <!-- Tree Container -->
    <div class="tree-container">
      <!-- Loading State -->
      <div v-if="loading" class="tree-loading">
        <v-progress-circular indeterminate color="primary" size="48" />
        <p class="text-medium-emphasis mt-4">{{ t('dashboard.loading') }}</p>
      </div>
      
      <!-- Empty State -->
      <div v-else-if="!filteredOlts.length" class="tree-empty">
        <v-icon size="80" color="grey-lighten-1">mdi-server-network-off</v-icon>
        <h3 class="text-h6 mt-4">{{ t('dashboard.noOlts') }}</h3>
        <p class="text-medium-emphasis">{{ t('dashboard.noOltsDesc') }}</p>
        <v-btn color="accent" class="mt-4" @click="$emit('add-olt')">
          <v-icon start>mdi-plus</v-icon>
          {{ t('dashboard.addOlt') }}
        </v-btn>
      </div>
      
      <!-- OLT Nodes -->
      <div v-else class="tree-nodes">
        <OltNodeNew
          v-for="olt in filteredOlts"
          :key="olt.id"
          :olt="olt"
          :search-query="searchQuery"
          :show-offline-only="showOfflineOnly"
          @refresh-power="$emit('refresh-power', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { t } from '@/i18n'
import OltNodeNew from './OltNodeNew.vue'

const props = defineProps({
  olts: {
    type: Array,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  isRefreshing: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['refresh', 'add-olt', 'refresh-power'])

// State
const searchQuery = ref('')
const filterOlt = ref(null)
const showOfflineOnly = ref(false)

// Computed
const oltFilterOptions = computed(() => {
  const options = [{ title: t('dashboard.allOlts'), value: null }]
  props.olts.forEach(olt => {
    options.push({ title: olt.name, value: olt.id })
  })
  return options
})

const filteredOlts = computed(() => {
  let result = [...props.olts]
  
  // Filter by OLT
  if (filterOlt.value) {
    result = result.filter(olt => olt.id === filterOlt.value)
  }
  
  // Filter by offline only
  if (showOfflineOnly.value) {
    result = result.filter(olt => olt.offline_count > 0)
  }
  
  // Filter by search query
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(olt => {
      // Check OLT name
      if (olt.name.toLowerCase().includes(query)) return true
      
      // Check slots/PONs/ONUs (deep search)
      if (olt.slots) {
        for (const slot of olt.slots) {
          if (slot.pons) {
            for (const pon of slot.pons) {
              if (pon.onus) {
                for (const onu of pon.onus) {
                  if (
                    onu.name?.toLowerCase().includes(query) ||
                    onu.serial_number?.toLowerCase().includes(query)
                  ) {
                    return true
                  }
                }
              }
            }
          }
        }
      }
      return false
    })
  }
  
  return result
})

</script>

<style scoped>
.topology-tree {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.topology-toolbar {
  padding: 16px;
  background: var(--varuna-panel-strong);
  border: 1px solid var(--varuna-card-border);
  border-radius: var(--varuna-radius-lg);
  box-shadow: var(--varuna-shadow-sm);
  backdrop-filter: blur(8px);
  margin-bottom: 12px;
}

.tree-container {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: var(--varuna-panel);
  border: 1px solid var(--varuna-card-border);
  border-radius: var(--varuna-radius-lg);
  box-shadow: var(--varuna-shadow-sm);
}

.tree-loading,
.tree-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 64px 16px;
  text-align: center;
  border: 1px dashed var(--varuna-card-border);
  border-radius: var(--varuna-radius-lg);
  background: rgba(var(--v-theme-surface), 0.6);
}

.tree-nodes {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

  .topology-toolbar :deep(.v-field) {
    border-radius: var(--varuna-radius-md);
    background: rgba(var(--v-theme-surface), 0.85);
    border: 1px solid var(--varuna-card-border);
  }

  .search-field :deep(.v-field) {
    border-radius: var(--varuna-radius-md);
    background: rgba(var(--v-theme-surface), 0.85);
    border: 1px solid var(--varuna-card-border);
  }
  
  .search-field :deep(.v-field__input) {
    opacity: 0.95;
  }

  .toolbar-group {
    border: 1px solid var(--varuna-card-border);
    border-radius: 999px;
    overflow: hidden;
    box-shadow: var(--varuna-shadow-sm);
  }

  .toolbar-actions :deep(.v-selection-control__wrapper) {
    margin-right: 6px;
  }
</style>
