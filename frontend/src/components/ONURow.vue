<template>
  <v-row class="onu-row align-center py-2" :class="{ 'offline-row': !onu.online }">
    <v-col cols="12" sm="4">
      <div class="onu-name">{{ onu.name || onu.serial || `ONU ${onu.onu_id}` }}</div>
      <div class="onu-serial text-caption">{{ onu.serial ? `Serial: ${onu.serial}` : '' }}</div>
    </v-col>
    <v-col cols="12" sm="2">
      <v-chip :color="statusColor" small>{{ statusLabel }}</v-chip>
    </v-col>
    <v-col cols="12" sm="3">
      <div v-if="onu.power_rx !== undefined" class="power-info">
        <span class="power-label">RX:</span> {{ onu.power_rx }} dBm
      </div>
      <div v-if="onu.power_tx !== undefined" class="power-info">
        <span class="power-label">TX:</span> {{ onu.power_tx }} dBm
      </div>
    </v-col>
    <v-col cols="12" sm="3">
      <div v-if="!onu.online && onu.disconnect_reason" class="disconnect-info">
        <span class="disconnect-label">Reason:</span> {{ disconnectReasonLabel }}
      </div>
      <div v-if="!onu.online && onu.offline_since" class="offline-time">
        {{ t('status.offline') }}: {{ formatOfflineTime(onu.offline_since) }}
      </div>
    </v-col>
  </v-row>
</template>

<script setup>
import { computed } from 'vue'
import { t, formatRelativeTime } from '@/i18n'

const props = defineProps({
  onu: {
    type: Object,
    required: true
  }
})

const statusLabel = computed(() => {
  if (props.onu.online === undefined) return t('topology.unknown')
  return props.onu.online ? t('topology.online') : t('topology.offline')
})

const statusColor = computed(() => {
  if (props.onu.online === undefined) return 'grey'
  return props.onu.online ? 'green' : 'red'
})

const disconnectReasonLabel = computed(() => {
  if (!props.onu.disconnect_reason) return ''
  const reasons = {
    link_loss: t('status.linkLoss'),
    dying_gasp: t('status.dyingGasp'),
    unknown: t('status.unknownReason')
  }
  return reasons[props.onu.disconnect_reason] || props.onu.disconnect_reason
})

const formatOfflineTime = (timestamp) => {
  return formatRelativeTime(timestamp)
}
</script>

<style scoped>
.onu-row {
  border-bottom: 1px solid #e0e0e0;
}

.offline-row {
  background-color: #fef2f2;
}

.power-info {
  font-size: 0.875rem;
  color: #666;
}

.power-label {
  font-weight: 500;
  color: #333;
}

.disconnect-info {
  font-size: 0.875rem;
  color: #d32f2f;
}

.disconnect-label {
  font-weight: 500;
}

.offline-time {
  font-size: 0.75rem;
  color: #666;
  margin-top: 4px;
}
</style>
