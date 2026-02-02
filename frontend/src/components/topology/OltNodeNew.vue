<template>
  <div class="olt-node">
    <!-- OLT Header -->
    <div class="olt-header" :class="statusClass">
      <div class="olt-info">
        <span class="olt-name">{{ olt.name }}</span>
        <span class="olt-meta">
          {{ olt.ip_address }}
          <span v-if="olt.vendor_profile_name"> · {{ olt.vendor_profile_name }}</span>
        </span>
      </div>
      
      <div class="olt-stats">
        <span class="stat online">{{ olt.online_count || 0 }} {{ t('topology.online') }}</span>
        <span class="stat offline">{{ olt.offline_count || 0 }} {{ t('topology.offline') }}</span>
        <span class="stat-total">{{ totalOnus }} {{ t('topology.onus') }}</span>
      </div>
      
      <v-btn
        icon
        size="x-small"
        variant="text"
        class="refresh-btn"
        @click.stop="$emit('refresh-power', olt.id)"
      >
        <v-icon size="18">mdi-flash</v-icon>
        <v-tooltip activator="parent" location="top">Refresh Power</v-tooltip>
      </v-btn>
    </div>

    <!-- OLT Body (Left: Slots/PONs, Right: PON Details) -->
    <div class="olt-body">
      <div class="olt-left">
        <div v-if="!slotItems.length" class="left-empty">
          {{ t('messages.noData') }}
        </div>
        <div v-else>
          <div class="slot-group" v-for="slot in slotItems" :key="slot.id">
            <button
              type="button"
              class="slot-header"
              :class="{ expanded: isSlotExpanded(slot.id) }"
              @click="toggleSlot(slot.id)"
            >
              <span class="slot-left">
                <v-icon size="16" class="slot-toggle-icon">
                  {{ isSlotExpanded(slot.id) ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
                </v-icon>
                <span class="slot-title">{{ t('topology.slot') }} {{ slot.slot_number }}</span>
              </span>
              <div class="slot-counts">
                <span class="stat online">{{ slot.onlineCount }}</span>
                <span class="stat offline">{{ slot.offlineCount }}</span>
                <span class="stat-total">{{ slot.ponCount }} {{ t('topology.pons') }}</span>
              </div>
            </button>
            <div class="pon-list" v-show="isSlotExpanded(slot.id)">
              <button
                v-for="pon in slot.pons"
                :key="pon.id"
                type="button"
                class="pon-row"
                :class="{ active: pon.id === selectedPonId }"
                @click="selectPon(pon.id, slot.id)"
              >
                <span class="pon-title">{{ t('topology.pon') }} {{ pon.pon_number }}</span>
                <div class="pon-counts">
                  <span class="stat online">{{ pon.onlineCount }}</span>
                  <span class="stat offline">{{ pon.offlineCount }}</span>
                  <span class="stat-total">{{ pon.totalCount }} {{ t('topology.onus') }}</span>
                </div>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="olt-right">
        <div v-if="selectedPon" class="pon-detail">
          <div class="pon-detail-header">
            <div class="pon-detail-title">
              <span class="pon-detail-name">
                {{ olt.name }} &gt; {{ t('topology.slot') }} {{ selectedPon.slot_number }} &gt; {{ t('topology.pon') }} {{ selectedPon.pon_number }}
              </span>
              <span class="pon-detail-meta">
                {{ t('topology.slot') }} {{ selectedPon.slot_number }} · {{ selectedPon.totalCount }} {{ t('topology.onus') }}
              </span>
            </div>
            <div class="pon-detail-counts">
              <span class="stat online">{{ reasonCounts.online }} {{ t('topology.online') }}</span>
              <span class="stat dying-gasp">{{ reasonCounts.dyingGasp }} {{ t('status.dyingGasp') }}</span>
              <span class="stat link-loss">{{ reasonCounts.linkLoss }} {{ t('status.linkLoss') }}</span>
              <span class="stat unknown">{{ reasonCounts.unknown }} {{ t('topology.unknown') }}</span>
            </div>
          </div>
          <OnuTable :onus="selectedOnus" />
        </div>
        <div v-else class="pon-empty">
          <v-icon size="28" color="grey">mdi-access-point</v-icon>
          <div class="pon-empty-title">{{ t('dashboard.selectPonTitle') }}</div>
          <div class="pon-empty-sub">{{ t('dashboard.selectPonSubtitle') }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { t } from '@/i18n'
import OnuTable from './OnuTable.vue'

const props = defineProps({
  olt: {
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

defineEmits(['refresh-power'])

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

const selectedPonId = ref(null)
const expandedSlots = ref(new Set())

const slotItems = computed(() => {
  if (!props.olt.slots) return []
  
  let slots = [...props.olt.slots]
  slots.sort((a, b) => (a.slot_number || 0) - (b.slot_number || 0))
  
  return slots
    .map(slot => {
      let pons = slot.pons ? [...slot.pons] : []
      pons.sort((a, b) => (a.pon_number || 0) - (b.pon_number || 0))

      if (props.showOfflineOnly) {
        pons = pons.filter(pon => {
          if (!pon.onus) return false
          return pon.onus.some(onu => onu.status !== 'online')
        })
      }

      if (props.searchQuery) {
        const query = props.searchQuery.toLowerCase()
        pons = pons.filter(pon => {
          if (!pon.onus) return false
          return pon.onus.some(onu =>
            onu.name?.toLowerCase().includes(query) ||
            onu.serial_number?.toLowerCase().includes(query)
          )
        })
      }

      const mappedPons = pons.map(pon => {
        const onus = pon.onus || []
        const onlineCount = onus.filter(onu => onu.status === 'online').length
        const offlineCount = onus.length - onlineCount
        return {
          ...pon,
          slot_id: slot.id,
          slot_number: slot.slot_number,
          onlineCount,
          offlineCount,
          totalCount: onus.length,
        }
      })

      const onlineCount = mappedPons.reduce((sum, pon) => sum + pon.onlineCount, 0)
      const offlineCount = mappedPons.reduce((sum, pon) => sum + pon.offlineCount, 0)

      return {
        ...slot,
        pons: mappedPons,
        ponCount: mappedPons.length,
        onlineCount,
        offlineCount,
      }
    })
    .filter(slot => slot.pons.length > 0 || !props.showOfflineOnly)
})

const selectedPon = computed(() => {
  for (const slot of slotItems.value) {
    const match = slot.pons.find(pon => pon.id === selectedPonId.value)
    if (match) return match
  }
  return null
})

const selectedOnus = computed(() => {
  if (!selectedPon.value) return []
  let onus = selectedPon.value.onus ? [...selectedPon.value.onus] : []

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

const reasonCounts = computed(() => {
  const counts = {
    online: 0,
    dyingGasp: 0,
    linkLoss: 0,
    unknown: 0,
  }

  if (!selectedPon.value) return counts

  const onus = selectedOnus.value
  for (const onu of onus) {
    if (onu.status === 'online') {
      counts.online += 1
      continue
    }

    const reason = (onu.disconnect_reason || '').toLowerCase()
    if (reason.includes('dying') || reason.includes('gasp')) {
      counts.dyingGasp += 1
    } else if (reason.includes('loss') || reason.includes('los')) {
      counts.linkLoss += 1
    } else {
      counts.unknown += 1
    }
  }

  return counts
})

const selectPon = (ponId, slotId) => {
  selectedPonId.value = ponId
  if (slotId) {
    expandedSlots.value.add(slotId)
    expandedSlots.value = new Set(expandedSlots.value)
  }
}

const isSlotExpanded = (slotId) => expandedSlots.value.has(slotId)

const toggleSlot = (slotId) => {
  if (expandedSlots.value.has(slotId)) {
    expandedSlots.value.delete(slotId)
  } else {
    expandedSlots.value.add(slotId)
  }
  expandedSlots.value = new Set(expandedSlots.value)
}

watch(slotItems, (slots) => {
  const slotIds = new Set(slots.map(slot => slot.id))
  expandedSlots.value = new Set(
    [...expandedSlots.value].filter(slotId => slotIds.has(slotId))
  )

  const allPons = slots.flatMap(slot => slot.pons)
  if (!allPons.length) {
    selectedPonId.value = null
    return
  }
  if (!allPons.some(pon => pon.id === selectedPonId.value)) {
    selectedPonId.value = allPons[0].id
  }

  const selected = allPons.find(pon => pon.id === selectedPonId.value)
  if (selected?.slot_id) {
    expandedSlots.value.add(selected.slot_id)
  }

  if (expandedSlots.value.size === 0 && slots[0]) {
    expandedSlots.value.add(slots[0].id)
  }

  expandedSlots.value = new Set(expandedSlots.value)
}, { immediate: true })
</script>

<style scoped>
.olt-node {
  background: var(--varuna-surface);
  border: 1px solid var(--varuna-card-border);
  border-radius: var(--varuna-radius-md);
  overflow: hidden;
  box-shadow: var(--varuna-shadow-sm);
}

.olt-header {
  display: flex;
  align-items: center;
  padding: 14px 16px;
  cursor: default;
  transition: background 0.15s ease;
  border-left: 3px solid transparent;
}

.olt-header:hover {
  background: rgba(var(--v-theme-primary), 0.03);
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

.olt-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.olt-name {
  font-size: 16px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.olt-meta {
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.olt-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-right: 12px;
}

.stat {
  font-size: 13px;
  font-weight: 700;
  padding: 5px 12px;
  border-radius: 14px;
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

.stat.dying-gasp {
  background: rgba(139, 92, 246, 0.2);
  color: #6d28d9;
  border-color: rgba(139, 92, 246, 0.45);
}

.stat.link-loss {
  background: rgba(245, 158, 11, 0.2);
  color: #92400e;
  border-color: rgba(245, 158, 11, 0.45);
}

.stat.unknown {
  background: rgba(107, 114, 128, 0.2);
  color: #374151;
  border-color: rgba(107, 114, 128, 0.45);
}

.stat-total {
  font-size: 14px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.refresh-btn {
  opacity: 0.8;
  transition: opacity 0.15s ease;
}

.refresh-btn:hover {
  opacity: 1;
}

.olt-body {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 16px;
  padding: 16px;
  border-top: 1px solid var(--varuna-line);
  background: var(--varuna-surface-2);
}

.olt-left,
.olt-right {
  background: var(--varuna-panel-strong);
  border: 1px solid var(--varuna-line-strong);
  border-radius: var(--varuna-radius-md);
  padding: 12px;
}

.left-empty {
  padding: 16px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 13px;
}

.slot-group + .slot-group {
  margin-top: 12px;
}

.slot-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  background: var(--varuna-surface-2);
  border: 1px solid var(--varuna-line-strong);
  border-radius: 10px;
  font-weight: 600;
  text-align: left;
  cursor: pointer;
  appearance: none;
  outline: none;
}

.slot-header:hover {
  border-color: rgba(var(--v-theme-primary), 0.35);
  background: rgba(var(--v-theme-primary), 0.06);
}

.slot-header:focus-visible {
  box-shadow: 0 0 0 2px rgba(var(--v-theme-primary), 0.25);
}

.slot-left {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.slot-toggle-icon {
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.slot-title {
  font-size: 14px;
  color: var(--varuna-ink);
}

.slot-counts,
.pon-counts,
.pon-detail-counts {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.pon-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: 18px;
}

.pon-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  background: var(--varuna-surface);
  border: 1px solid var(--varuna-line-strong);
  border-radius: 10px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}

.pon-row:hover {
  border-color: rgba(var(--v-theme-primary), 0.35);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.pon-row.active {
  border-color: rgba(var(--v-theme-primary), 0.6);
  background: rgba(var(--v-theme-primary), 0.08);
}

.pon-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--varuna-ink);
}

.stat-total {
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.pon-detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--varuna-line-strong);
}

.pon-detail-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pon-detail-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--varuna-ink);
  letter-spacing: 0.01em;
}

.pon-detail-meta {
  font-size: 13px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.pon-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
  padding: 24px 16px;
  color: rgba(var(--v-theme-on-surface), 0.7);
  min-height: 240px;
}

.pon-empty-title {
  font-weight: 700;
  color: var(--varuna-ink);
}

.pon-empty-sub {
  font-size: 13px;
}

@media (max-width: 1024px) {
  .olt-body {
    grid-template-columns: 1fr;
  }
}

/* Dark theme */
:global(.v-theme--dark) .stat.online {
  color: #34d399;
}

:global(.v-theme--dark) .stat.offline {
  color: #f87171;
}
</style>
