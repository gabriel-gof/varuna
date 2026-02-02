<template>
  <div class="slot-node">
    <!-- Slot Header -->
    <div class="node-header slot-header" @click="toggleExpanded">
      <div class="node-toggle">
        <v-icon size="18">
          {{ isExpanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
        </v-icon>
      </div>
      
      <div class="node-icon slot-icon" :class="statusClass">
        <v-icon size="18">mdi-expansion-card</v-icon>
      </div>
      
      <div class="node-info">
        <div class="node-name">{{ t('topology.slot') }} {{ slotData.slot_number }}</div>
      </div>
      
      <div class="node-stats">
        <StatusBadge :count="onlineCount" status="online" size="small" />
        <StatusBadge :count="offlineCount" status="offline" size="small" />
        <span class="stat-count">{{ ponCount }} {{ t('topology.pons') }}</span>
      </div>
    </div>
    
    <!-- Slot Children (PONs) -->
    <v-expand-transition>
      <div v-show="isExpanded" class="node-children">
        <PonNode
          v-for="pon in filteredPons"
          :key="pon.id"
          :pon="pon"
          :olt-id="oltId"
          :slot-id="slotData.id"
          :search-query="searchQuery"
          :show-offline-only="showOfflineOnly"
        />
        
        <div v-if="!filteredPons.length" class="empty-children">
          <v-icon size="20" color="grey">mdi-folder-open-outline</v-icon>
          <span>{{ t('messages.noData') }}</span>
        </div>
      </div>
    </v-expand-transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { t } from '@/i18n'
import StatusBadge from './StatusBadge.vue'
import PonNode from './PonNode.vue'

const props = defineProps({
  slotData: {
    type: Object,
    required: true,
  },
  oltId: {
    type: [Number, String],
    required: true,
  },
  searchQuery: {
    type: String,
    default: '',
  },
  showOfflineOnly: {
    type: Boolean,
    default: false,
  },
})

// State
const isExpanded = ref(false)

// Computed
const ponCount = computed(() => {
  return props.slotData.pons?.length || 0
})

const onlineCount = computed(() => {
  let count = 0
  if (props.slotData.pons) {
    for (const pon of props.slotData.pons) {
      if (pon.onus) {
        count += pon.onus.filter(onu => onu.status === 'online').length
      }
    }
  }
  return count
})

const offlineCount = computed(() => {
  let count = 0
  if (props.slotData.pons) {
    for (const pon of props.slotData.pons) {
      if (pon.onus) {
        count += pon.onus.filter(onu => onu.status !== 'online').length
      }
    }
  }
  return count
})

const statusClass = computed(() => {
  const total = onlineCount.value + offlineCount.value
  if (total === 0) return 'status-neutral'
  if (offlineCount.value === 0) return 'status-online'
  if (onlineCount.value === 0) return 'status-offline'
  return 'status-partial'
})

const filteredPons = computed(() => {
  if (!props.slotData.pons) return []
  
  let pons = [...props.slotData.pons]
  
  // Sort by PON number
  pons.sort((a, b) => (a.pon_number || 0) - (b.pon_number || 0))
  
  // Filter by offline only
  if (props.showOfflineOnly) {
    pons = pons.filter(pon => {
      if (!pon.onus) return false
      return pon.onus.some(onu => onu.status !== 'online')
    })
  }
  
  return pons
})

// Methods
const toggleExpanded = () => {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
.slot-node {
  border-radius: var(--varuna-radius-md);
  margin: 6px 0;
  background: var(--varuna-panel);
  overflow: hidden;
  border: 1px solid var(--varuna-card-border);
  box-shadow: var(--varuna-shadow-sm);
}

.node-header {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.node-header:hover {
  background: rgba(var(--v-theme-primary), 0.06);
}

.slot-header {
  min-height: 44px;
}

.node-toggle {
  margin-right: 6px;
  color: rgb(var(--v-theme-on-surface-variant));
}

.node-icon {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 10px;
  border: 1px solid var(--varuna-card-border);
}

.slot-icon {
  background: var(--varuna-surface-2);
  color: rgb(var(--v-theme-primary));
}

.slot-icon.status-online {
  background: rgba(var(--varuna-status-online), 0.1);
  color: rgb(var(--varuna-status-online));
  border-color: rgba(var(--varuna-status-online), 0.3);
}

.slot-icon.status-offline {
  background: rgba(var(--varuna-status-offline), 0.1);
  color: rgb(var(--varuna-status-offline));
  border-color: rgba(var(--varuna-status-offline), 0.3);
}

.slot-icon.status-partial {
  background: rgba(var(--varuna-status-partial), 0.1);
  color: rgb(var(--varuna-status-partial));
  border-color: rgba(var(--varuna-status-partial), 0.3);
}

.slot-icon.status-neutral {
  background: rgba(var(--varuna-status-neutral), 0.1);
  color: rgb(var(--varuna-status-neutral));
  border-color: rgba(var(--varuna-status-neutral), 0.3);
}

.node-info {
  flex: 1;
  min-width: 0;
}

.node-name {
  font-size: 14px;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface));
}

.node-stats {
  display: flex;
  align-items: center;
  gap: 6px;
}

.stat-count {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.85);
  font-weight: 600;
  margin-left: 4px;
}

.node-children {
  padding: 8px 8px 8px 16px;
  background: rgba(var(--v-theme-surface-variant), 0.06);
  border-top: 1px solid var(--varuna-card-border);
  margin-left: 16px;
  border-left: 2px solid var(--varuna-line-strong);
}

.empty-children {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 13px;
}
</style>
