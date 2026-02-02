<template>
  <div class="olt-node" :class="{ 'is-expanded': expanded }">
    <!-- OLT Header -->
    <div class="node-header olt-header" :class="statusClass" @click="$emit('toggle')">
      <div class="node-toggle">
        <v-icon size="20">
          {{ expanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
        </v-icon>
      </div>
      
      <div class="node-icon">
        <v-icon size="24" color="primary">mdi-server-network</v-icon>
      </div>
      
      <div class="node-info">
        <div class="node-name">{{ olt.name }}</div>
        <div class="node-meta">
          <span class="meta-item">
            <v-icon size="12">mdi-ip-network</v-icon>
            {{ olt.ip_address }}
          </span>
          <span class="meta-item" v-if="olt.vendor_profile_name">
            <v-icon size="12">mdi-factory</v-icon>
            {{ olt.vendor_profile_name }}
          </span>
        </div>
      </div>
      
      <div class="node-stats">
        <StatusBadge :count="olt.online_count || 0" status="online" :label="t('topology.online')" />
        <StatusBadge :count="olt.offline_count || 0" status="offline" :label="t('topology.offline')" />
        <span class="stat-divider"></span>
        <span class="stat-total">{{ totalOnus }} {{ t('topology.onus') }}</span>
      </div>
      
      <div class="node-actions">
        <v-btn
          icon
          size="x-small"
          variant="text"
          color="primary"
          class="action-btn"
          @click.stop="$emit('refresh-power', olt.id)"
        >
          <v-icon>mdi-lightning-bolt</v-icon>
          <v-tooltip activator="parent" location="top">Refresh Power</v-tooltip>
        </v-btn>
      </div>
    </div>
    
    <!-- OLT Children (Slots) -->
    <v-expand-transition>
      <div v-show="expanded" class="node-children">
        <SlotNode
          v-for="slot in filteredSlots"
          :key="slot.id"
          :slot-data="slot"
          :olt-id="olt.id"
          :search-query="searchQuery"
          :show-offline-only="showOfflineOnly"
        />
        
        <div v-if="!filteredSlots.length" class="empty-children">
          <v-icon size="24" color="grey">mdi-folder-open-outline</v-icon>
          <span>{{ t('messages.noData') }}</span>
        </div>
      </div>
    </v-expand-transition>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { t } from '@/i18n'
import StatusBadge from './StatusBadge.vue'
import SlotNode from './SlotNode.vue'

const props = defineProps({
  olt: {
    type: Object,
    required: true,
  },
  expanded: {
    type: Boolean,
    default: false,
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

defineEmits(['toggle', 'refresh-power'])

// Computed
const totalOnus = computed(() => {
  return (props.olt.online_count || 0) + (props.olt.offline_count || 0)
})

const statusClass = computed(() => {
  const online = props.olt.online_count || 0
  const offline = props.olt.offline_count || 0
  const total = online + offline
  
  if (total === 0) return 'status-neutral'
  if (offline === 0) return 'status-online'
  if (online === 0) return 'status-offline'
  return 'status-partial'
})

const filteredSlots = computed(() => {
  if (!props.olt.slots) return []
  
  let slots = [...props.olt.slots]
  
  // Sort by slot number
  slots.sort((a, b) => (a.slot_number || 0) - (b.slot_number || 0))
  
  // Filter by offline only if needed
  if (props.showOfflineOnly) {
    slots = slots.filter(slot => {
      if (!slot.pons) return false
      return slot.pons.some(pon => {
        if (!pon.onus) return false
        return pon.onus.some(onu => onu.status !== 'online')
      })
    })
  }
  
  return slots
})
</script>

<style scoped>
.olt-node {
  border-radius: var(--varuna-radius-lg);
  overflow: hidden;
  box-shadow: var(--varuna-shadow-sm);
  transition: box-shadow 0.2s ease;
  border: 1px solid var(--varuna-card-border);
  background: var(--varuna-surface);
}

.olt-node:hover {
  box-shadow: var(--varuna-shadow-md);
}

.node-header {
  display: flex;
  align-items: center;
  padding: 14px 18px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s ease;
  position: relative;
}

.olt-header {
  min-height: 64px;
  background: var(--varuna-panel-strong);
  color: var(--varuna-ink);
  border-left: 3px solid transparent;
}

  .olt-header.status-online {
    border-left-color: #10b981;
  }
  
  .olt-header.status-offline {
    border-left-color: #ef4444;
  }
  
  .olt-header.status-partial {
    border-left-color: #f59e0b;
  }
  
  .olt-header.status-neutral {
    border-left-color: #6b7280;
  }

.node-toggle {
  margin-right: 8px;
  opacity: 1;
}

.node-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--varuna-surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-right: 12px;
  border: 1px solid var(--varuna-card-border);
}

.node-info {
  flex: 1;
  min-width: 0;
}

.node-name {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

  .node-meta {
    display: flex;
    gap: 12px;
    font-size: 13px;
    color: rgba(var(--v-theme-on-surface), 0.82);
    margin-top: 3px;
  }

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.node-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 16px;
  padding: 0;
}

.stat-divider {
  width: 1px;
  height: 22px;
  background: var(--varuna-line-strong);
  margin: 0 4px;
}

.stat-total {
  font-size: 14px;
  font-weight: 600;
  color: var(--varuna-ink);
  opacity: 0.9;
}

.node-actions {
  margin-left: 8px;
}

.action-btn {
  background: transparent;
  border: 1px solid var(--varuna-card-border);
  box-shadow: none;
}

.action-btn:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

  .node-children {
    background: var(--varuna-panel);
    padding: 10px 10px 10px 24px;
    border-top: 1px solid var(--varuna-card-border);
    border-left: 1px solid var(--varuna-line);
  }

.empty-children {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: 14px;
}
</style>
