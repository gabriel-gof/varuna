<template>
  <div class="slot-node">
    <!-- Slot Header -->
    <div class="slot-header" @click="toggleExpanded">
      <v-icon size="18" class="toggle-icon">
        {{ isExpanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
      </v-icon>
      
      <span class="slot-label">Slot {{ slotData.slot_number }}</span>
      
      <div class="slot-stats">
        <span class="stat online">{{ onlineCount }}</span>
        <span class="stat offline">{{ offlineCount }}</span>
        <span class="stat-total">{{ ponCount }} PONs</span>
      </div>
    </div>
    
    <!-- Slot Content (PONs) -->
    <v-expand-transition>
      <div v-show="isExpanded" class="slot-content">
        <PonNodeNew
          v-for="pon in filteredPons"
          :key="pon.id"
          :pon="pon"
          :search-query="searchQuery"
          :show-offline-only="showOfflineOnly"
        />
        <div v-if="!filteredPons.length" class="empty-state">
          No PONs found
        </div>
      </div>
    </v-expand-transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import PonNodeNew from './PonNodeNew.vue'

const props = defineProps({
  slotData: {
    type: Object,
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

const isExpanded = ref(false)

const ponCount = computed(() => props.slotData.pons?.length || 0)

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

const filteredPons = computed(() => {
  if (!props.slotData.pons) return []
  
  let pons = [...props.slotData.pons]
  pons.sort((a, b) => (a.pon_number || 0) - (b.pon_number || 0))
  
  if (props.showOfflineOnly) {
    pons = pons.filter(pon => {
      if (!pon.onus) return false
      return pon.onus.some(onu => onu.status !== 'online')
    })
  }
  
  return pons
})

const toggleExpanded = () => {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
.slot-node {
  margin-left: 16px;
  border-left: 2px solid var(--varuna-line-strong);
}

.slot-header {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.slot-header:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}

.toggle-icon {
  color: rgba(var(--v-theme-on-surface), 0.85);
  margin-right: 10px;
}

.slot-label {
  font-size: 15px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.slot-stats {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.stat {
  font-size: 13px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 12px;
  border: 1px solid transparent;
}

.stat.online {
  background: rgba(16, 185, 129, 0.22);
  color: #059669;
  border-color: rgba(16, 185, 129, 0.45);
}

.stat.offline {
  background: rgba(239, 68, 68, 0.22);
  color: #dc2626;
  border-color: rgba(239, 68, 68, 0.45);
}

.stat-total {
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
  margin-left: 4px;
}

.slot-content {
  padding-left: 8px;
}

.empty-state {
  padding: 16px 32px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 13px;
}

/* Dark theme */
:global(.v-theme--dark) .stat.online {
  color: #34d399;
}

:global(.v-theme--dark) .stat.offline {
  color: #f87171;
}
</style>
