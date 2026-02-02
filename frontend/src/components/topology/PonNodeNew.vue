<template>
  <div class="pon-node">
    <!-- PON Header -->
    <div class="pon-header" @click="toggleExpanded">
      <v-icon size="16" class="toggle-icon">
        {{ isExpanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
      </v-icon>
      
      <span class="pon-label">PON {{ pon.pon_number }}</span>
      
      <div class="pon-stats">
        <span class="stat online">{{ onlineCount }}</span>
        <span class="stat offline">{{ offlineCount }}</span>
        <span class="stat-total">{{ totalCount }} ONUs</span>
      </div>
    </div>
    
    <!-- PON Content (ONUs Table) -->
    <v-expand-transition>
      <div v-show="isExpanded" class="pon-content">
        <OnuTable :onus="filteredOnus" />
      </div>
    </v-expand-transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import OnuTable from './OnuTable.vue'

const props = defineProps({
  pon: {
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

const totalCount = computed(() => props.pon.onus?.length || 0)

const onlineCount = computed(() => {
  if (!props.pon.onus) return 0
  return props.pon.onus.filter(onu => onu.status === 'online').length
})

const offlineCount = computed(() => {
  if (!props.pon.onus) return 0
  return props.pon.onus.filter(onu => onu.status !== 'online').length
})

const filteredOnus = computed(() => {
  if (!props.pon.onus) return []
  
  let onus = [...props.pon.onus]
  onus.sort((a, b) => (a.onu_number || 0) - (b.onu_number || 0))
  
  if (props.showOfflineOnly) {
    onus = onus.filter(onu => onu.status !== 'online')
  }
  
  if (props.searchQuery) {
    const query = props.searchQuery.toLowerCase()
    onus = onus.filter(onu => 
      onu.name?.toLowerCase().includes(query) ||
      onu.serial_number?.toLowerCase().includes(query)
    )
  }
  
  return onus
})

const toggleExpanded = () => {
  isExpanded.value = !isExpanded.value
}
</script>

<style scoped>
.pon-node {
  margin-left: 24px;
  border-left: 1px solid var(--varuna-line);
}

.pon-header {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.pon-header:hover {
  background: rgba(var(--v-theme-primary), 0.04);
}

.toggle-icon {
  color: rgba(var(--v-theme-on-surface), 0.85);
  margin-right: 8px;
}

.pon-label {
  font-size: 14px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.pon-stats {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
}

.stat {
  font-size: 12px;
  font-weight: 700;
  padding: 4px 9px;
  border-radius: 10px;
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
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
  margin-left: 4px;
}

.pon-content {
  padding: 8px 12px 12px 32px;
  background: rgba(var(--v-theme-surface-variant), 0.03);
}

/* Dark theme */
:global(.v-theme--dark) .stat.online {
  color: #34d399;
}

:global(.v-theme--dark) .stat.offline {
  color: #f87171;
}
</style>
