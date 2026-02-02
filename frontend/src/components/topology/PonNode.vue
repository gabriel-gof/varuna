<template>
  <div class="pon-node">
    <!-- PON Header -->
    <div class="node-header pon-header" @click="toggleExpanded">
      <div class="node-toggle">
        <v-icon size="16">
          {{ isExpanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
        </v-icon>
      </div>
      
      <div class="node-icon pon-icon" :class="statusClass">
        <v-icon size="16">mdi-connection</v-icon>
      </div>
      
      <div class="node-info">
        <div class="node-name">{{ t('topology.pon') }} {{ pon.pon_number }}</div>
      </div>
      
      <div class="node-stats">
        <StatusBadge :count="onlineCount" status="online" size="x-small" />
        <StatusBadge :count="offlineCount" status="offline" size="x-small" />
        <span class="stat-count">{{ totalCount }} {{ t('topology.onus') }}</span>
      </div>
    </div>
    
    <!-- PON Children (ONUs) -->
    <v-expand-transition>
      <div v-show="isExpanded" class="node-children">
        <div class="onu-grid" v-if="filteredOnus.length">
          <OnuChip
            v-for="onu in filteredOnus"
            :key="onu.id"
            :onu="onu"
            :highlight="isHighlighted(onu)"
          />
        </div>
        
        <div v-else class="empty-children">
          <v-icon size="18" color="grey">mdi-access-point-off</v-icon>
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
import OnuChip from './OnuChip.vue'

const props = defineProps({
  pon: {
    type: Object,
    required: true,
  },
  oltId: {
    type: [Number, String],
    required: true,
  },
  slotId: {
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
const totalCount = computed(() => {
  return props.pon.onus?.length || 0
})

const onlineCount = computed(() => {
  if (!props.pon.onus) return 0
  return props.pon.onus.filter(onu => onu.status === 'online').length
})

const offlineCount = computed(() => {
  if (!props.pon.onus) return 0
  return props.pon.onus.filter(onu => onu.status !== 'online').length
})

const statusClass = computed(() => {
  const total = totalCount.value
  if (total === 0) return 'status-neutral'
  if (offlineCount.value === 0) return 'status-online'
  if (onlineCount.value === 0) return 'status-offline'
  return 'status-partial'
})

const filteredOnus = computed(() => {
  if (!props.pon.onus) return []
  
  let onus = [...props.pon.onus]
  
  // Sort by ONU number
  onus.sort((a, b) => (a.onu_number || 0) - (b.onu_number || 0))
  
  // Filter by offline only
  if (props.showOfflineOnly) {
    onus = onus.filter(onu => onu.status !== 'online')
  }
  
  // Filter by search
  if (props.searchQuery) {
    const query = props.searchQuery.toLowerCase()
    onus = onus.filter(onu => 
      onu.name?.toLowerCase().includes(query) ||
      onu.serial_number?.toLowerCase().includes(query)
    )
  }
  
  return onus
})

// Methods
const toggleExpanded = () => {
  isExpanded.value = !isExpanded.value
}

const isHighlighted = (onu) => {
  if (!props.searchQuery) return false
  const query = props.searchQuery.toLowerCase()
  return (
    onu.name?.toLowerCase().includes(query) ||
    onu.serial_number?.toLowerCase().includes(query)
  )
}
</script>

<style scoped>
.pon-node {
  border-radius: var(--varuna-radius-sm);
  margin: 4px 0;
  background: var(--varuna-surface);
  overflow: hidden;
  border: 1px solid var(--varuna-card-border);
}

.node-header {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.node-header:hover {
  background: rgba(var(--v-theme-primary), 0.06);
}

.pon-header {
  min-height: 38px;
}

.node-toggle {
  margin-right: 4px;
  color: rgb(var(--v-theme-on-surface-variant));
}

.node-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 8px;
  border: 1px solid var(--varuna-card-border);
}

.pon-icon {
  background: var(--varuna-surface-2);
  color: rgb(var(--v-theme-secondary));
}

.pon-icon.status-online {
  background: rgba(var(--varuna-status-online), 0.1);
  color: rgb(var(--varuna-status-online));
  border-color: rgba(var(--varuna-status-online), 0.3);
}

.pon-icon.status-offline {
  background: rgba(var(--varuna-status-offline), 0.1);
  color: rgb(var(--varuna-status-offline));
  border-color: rgba(var(--varuna-status-offline), 0.3);
}

.pon-icon.status-partial {
  background: rgba(var(--varuna-status-partial), 0.1);
  color: rgb(var(--varuna-status-partial));
  border-color: rgba(var(--varuna-status-partial), 0.3);
}

.pon-icon.status-neutral {
  background: rgba(var(--varuna-status-neutral), 0.1);
  color: rgb(var(--varuna-status-neutral));
  border-color: rgba(var(--varuna-status-neutral), 0.3);
}

.node-info {
  flex: 1;
  min-width: 0;
}

.node-name {
  font-size: 13px;
  font-weight: 500;
  color: rgb(var(--v-theme-on-surface));
}

.node-stats {
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat-count {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.85);
  font-weight: 600;
  margin-left: 4px;
}

.node-children {
  padding: 10px 10px 10px 24px;
  background: rgba(var(--v-theme-surface-variant), 0.08);
  border-top: 1px solid var(--varuna-card-border);
  margin-left: 12px;
  border-left: 2px solid var(--varuna-line-strong);
}

.onu-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-left: 8px;
}

.empty-children {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 12px;
}
</style>
