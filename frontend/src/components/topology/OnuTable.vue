<template>
  <div class="onu-table-container">
    <!-- Tabs -->
    <div class="table-tabs">
      <button 
        class="tab-btn" 
        :class="{ active: activeTab === 'status' }"
        @click="activeTab = 'status'"
      >
        <v-icon size="16">mdi-list-status</v-icon>
        Status
      </button>
      <button 
        class="tab-btn" 
        :class="{ active: activeTab === 'power' }"
        @click="activeTab = 'power'"
      >
        <v-icon size="16">mdi-flash</v-icon>
        Power
      </button>
    </div>

    <!-- Status Tab -->
    <div v-if="activeTab === 'status'" class="table-wrapper">
      <table class="onu-table">
        <thead>
          <tr>
            <th class="col-id">ID</th>
            <th class="col-name">Name</th>
            <th class="col-status">Status</th>
            <th class="col-reason">Reason</th>
            <th class="col-time">Disconnected</th>
          </tr>
        </thead>
        <tbody>
          <tr 
            v-for="(onu, index) in onus" 
            :key="onu.id"
            :class="{ 'row-alt': index % 2 === 1, 'row-offline': onu.status !== 'online' }"
          >
            <td class="col-id">{{ onu.onu_number }}</td>
            <td class="col-name">{{ onu.name || '-' }}</td>
            <td class="col-status">
              <span class="status-dot" :class="onu.status === 'online' ? 'online' : 'offline'"></span>
              {{ onu.status === 'online' ? t('topology.online') : t('topology.offline') }}
            </td>
            <td class="col-reason">
              <span v-if="onu.status !== 'online'" class="reason-tag" :class="getReasonClass(onu.disconnect_reason)">
                {{ formatReason(onu.disconnect_reason) }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <td class="col-time">
              <span v-if="onu.status !== 'online' && onu.last_offline_at" class="time-text">
                {{ formatTime(onu.last_offline_at) }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
          </tr>
          <tr v-if="!onus.length">
            <td colspan="5" class="empty-row">No ONUs found</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Power Tab -->
    <div v-if="activeTab === 'power'" class="table-wrapper">
      <table class="onu-table">
        <thead>
          <tr>
            <th class="col-id">ID</th>
            <th class="col-name">Name</th>
            <th class="col-power">ONU RX (dBm)</th>
            <th class="col-power">OLT TX (dBm)</th>
            <th class="col-status">Status</th>
          </tr>
        </thead>
        <tbody>
          <tr 
            v-for="(onu, index) in onus" 
            :key="onu.id"
            :class="{ 'row-alt': index % 2 === 1 }"
          >
            <td class="col-id">{{ onu.onu_number }}</td>
            <td class="col-name">{{ onu.name || '-' }}</td>
            <td class="col-power">
              <span v-if="onu.onu_rx_power != null" :class="getPowerClass(onu.onu_rx_power)">
                {{ formatPower(onu.onu_rx_power) }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <td class="col-power">
              <span v-if="onu.olt_tx_power != null" :class="getPowerClass(onu.olt_tx_power)">
                {{ formatPower(onu.olt_tx_power) }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <td class="col-status">
              <span class="status-dot" :class="onu.status === 'online' ? 'online' : 'offline'"></span>
            </td>
          </tr>
          <tr v-if="!onus.length">
            <td colspan="5" class="empty-row">No ONUs found</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { t } from '@/i18n'

defineProps({
  onus: {
    type: Array,
    default: () => [],
  },
})

const activeTab = ref('status')

const formatReason = (reason) => {
  if (!reason) return t('status.unknownReason')
  const r = reason.toLowerCase()
  if (r.includes('dying') || r.includes('gasp')) return t('status.dyingGasp')
  if (r.includes('loss') || r.includes('los')) return t('status.linkLoss')
  return t('status.unknownReason')
}

const getReasonClass = (reason) => {
  if (!reason) return 'reason-unknown'
  const r = reason.toLowerCase()
  if (r.includes('dying') || r.includes('gasp')) return 'reason-dying-gasp'
  if (r.includes('loss') || r.includes('los')) return 'reason-link-loss'
  return 'reason-unknown'
}

const formatTime = (timestamp) => {
  if (!timestamp) return '-'
  const date = new Date(timestamp)
  const now = new Date()
  const diff = Math.floor((now - date) / 1000)
  
  if (diff < 60) return 'Just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return date.toLocaleDateString()
}

const formatPower = (power) => {
  if (power == null) return '-'
  return power.toFixed(2)
}

const getPowerClass = (power) => {
  if (power == null) return ''
  if (power >= -20) return 'power-good'
  if (power >= -25) return 'power-warning'
  return 'power-bad'
}
</script>

<style scoped>
.onu-table-container {
  width: 100%;
}

.table-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--varuna-line);
  padding-bottom: 8px;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--varuna-line-strong);
  background: var(--varuna-surface-2);
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 6px;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  background: rgba(var(--v-theme-primary), 0.12);
  color: var(--varuna-ink);
}

.tab-btn.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: var(--varuna-ink);
  border-color: rgba(var(--v-theme-primary), 0.45);
}

.tab-btn :deep(.v-icon) {
  opacity: 0.85;
}

.table-wrapper {
  overflow-x: auto;
}

.onu-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  background: var(--varuna-panel-strong);
}

.onu-table th {
  text-align: left;
  padding: 12px 14px;
  font-weight: 700;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--varuna-ink);
  border-bottom: 1px solid var(--varuna-line-strong);
  background: var(--varuna-surface-3);
}

.onu-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--varuna-line-strong);
  color: var(--varuna-ink);
}

.onu-table tr.row-alt {
  background: rgba(var(--v-theme-surface-variant), 0.16);
}

.onu-table tr.row-offline td {
  background: rgba(239, 68, 68, 0.12);
}

.onu-table tr.row-alt.row-offline td {
  background: rgba(239, 68, 68, 0.2);
}

.col-id {
  width: 60px;
  font-weight: 600;
  font-family: monospace;
}

.col-name {
  min-width: 120px;
}

.col-status {
  width: 100px;
}

.col-reason {
  width: 100px;
}

.col-time {
  width: 100px;
}

.col-power {
  width: 100px;
  font-family: monospace;
}

.status-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 6px;
  border: 1px solid var(--varuna-line-strong);
}

.status-dot.online {
  background: #10b981;
}

.status-dot.offline {
  background: #ef4444;
}

.reason-tag {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 700;
  border: 1px solid transparent;
}

.reason-dying-gasp {
  background: rgba(139, 92, 246, 0.22);
  color: #6d28d9;
  border-color: rgba(139, 92, 246, 0.4);
}

.reason-link-loss {
  background: rgba(245, 158, 11, 0.22);
  color: #92400e;
  border-color: rgba(245, 158, 11, 0.4);
}

.reason-unknown {
  background: rgba(107, 114, 128, 0.22);
  color: #374151;
  border-color: rgba(107, 114, 128, 0.4);
}

.time-text {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.text-muted {
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.power-good {
  color: #059669;
}

.power-warning {
  color: #b45309;
}

.power-bad {
  color: #dc2626;
}

.empty-row {
  text-align: center;
  padding: 24px !important;
  color: rgb(var(--v-theme-on-surface-variant));
}

/* Dark theme adjustments */
:global(.v-theme--dark) .onu-table th {
  background: rgba(var(--v-theme-surface-variant), 0.22);
  color: var(--varuna-ink);
}

:global(.v-theme--dark) .onu-table td {
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--varuna-ink);
}

:global(.v-theme--dark) .onu-table tr.row-alt {
  background: rgba(255, 255, 255, 0.04);
}

:global(.v-theme--dark) .onu-table tr.row-offline td {
  background: rgba(239, 68, 68, 0.16);
}

:global(.v-theme--dark) .onu-table tr.row-alt.row-offline td {
  background: rgba(239, 68, 68, 0.22);
}

:global(.v-theme--dark) .reason-dying-gasp {
  color: #a78bfa;
  border-color: rgba(139, 92, 246, 0.5);
}

:global(.v-theme--dark) .reason-link-loss {
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.5);
}

:global(.v-theme--dark) .reason-unknown {
  color: #9ca3af;
  border-color: rgba(107, 114, 128, 0.5);
}

:global(.v-theme--dark) .power-good {
  color: #34d399;
}

:global(.v-theme--dark) .power-warning {
  color: #fbbf24;
}

:global(.v-theme--dark) .power-bad {
  color: #f87171;
}
</style>
