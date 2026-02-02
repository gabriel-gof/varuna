<template>
  <v-tooltip :text="tooltipText" location="top">
    <template #activator="{ props: tooltipProps }">
      <div
        v-bind="tooltipProps"
        class="onu-chip"
        :class="[statusClass, reasonClass, { 'is-highlighted': highlight }]"
      >
        <span class="onu-number">#{{ onu.onu_number }}</span>
        <span class="onu-name" v-if="onu.name">{{ truncatedName }}</span>
        <v-icon v-if="reasonIcon" class="reason-icon" size="12">{{ reasonIcon }}</v-icon>
      </div>
    </template>
  </v-tooltip>
</template>

<script setup>
import { computed } from 'vue'
import { t } from '@/i18n'

const props = defineProps({
  onu: {
    type: Object,
    required: true,
  },
  highlight: {
    type: Boolean,
    default: false,
  },
})

// Computed
const truncatedName = computed(() => {
  const name = props.onu.name || ''
  if (name.length > 16) {
    return name.substring(0, 14) + '...'
  }
  return name
})

const statusClass = computed(() => {
  const status = props.onu.status?.toLowerCase() || 'unknown'
  switch (status) {
    case 'online':
    case 'working':
      return 'chip-online'
    case 'offline':
      return 'chip-offline'
    default:
      return 'chip-unknown'
  }
})

const reasonClass = computed(() => {
  const reason = props.onu.disconnect_reason?.toLowerCase() || ''
  if (reason.includes('dying') || reason.includes('gasp')) {
    return 'reason-dying-gasp'
  }
  if (reason.includes('loss') || reason.includes('los')) {
    return 'reason-link-loss'
  }
  return ''
})

const reasonIcon = computed(() => {
  const reason = props.onu.disconnect_reason?.toLowerCase() || ''
  if (reason.includes('dying') || reason.includes('gasp')) {
    return 'mdi-power-plug-off'
  }
  if (reason.includes('loss') || reason.includes('los')) {
    return 'mdi-link-variant-off'
  }
  return null
})

const reasonText = computed(() => {
  const reason = props.onu.disconnect_reason?.toLowerCase() || ''
  if (reason.includes('dying') || reason.includes('gasp')) {
    return t('status.dyingGasp')
  }
  if (reason.includes('loss') || reason.includes('los')) {
    return t('status.linkLoss')
  }
  if (props.onu.status !== 'online' && reason) {
    return t('status.unknownReason')
  }
  return ''
})

const tooltipText = computed(() => {
  let text = `#${props.onu.onu_number}`
  if (props.onu.name) {
    text += ` - ${props.onu.name}`
  }
  if (props.onu.serial_number) {
    text += `\nSerial: ${props.onu.serial_number}`
  }
  text += `\nStatus: ${t('status.' + (props.onu.status || 'unknown'))}`
  if (reasonText.value) {
    text += `\n${reasonText.value}`
  }
  return text
})
</script>

<style scoped>
.onu-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  cursor: default;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background-color 0.2s ease;
  border: 1px solid transparent;
  letter-spacing: 0.02em;
}

.onu-chip:hover {
    transform: translateY(-1px);
    box-shadow: var(--varuna-shadow-sm);
  }

/* Online Status */
.chip-online {
  background: rgba(16, 185, 129, 0.15);
  color: #047857;
  border-color: rgba(16, 185, 129, 0.35);
}

.chip-online:hover {
  background: rgba(16, 185, 129, 0.25);
}

/* Offline Status */
.chip-offline {
  background: rgba(239, 68, 68, 0.15);
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.35);
}

.chip-offline:hover {
  background: rgba(239, 68, 68, 0.25);
}

/* Unknown Status */
.chip-unknown {
  background: rgba(107, 114, 128, 0.15);
  color: #4b5563;
  border-color: rgba(107, 114, 128, 0.35);
}

.chip-unknown:hover {
  background: rgba(107, 114, 128, 0.25);
}

/* Dark theme overrides */
:global(.v-theme--dark) .chip-online {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
  border-color: rgba(16, 185, 129, 0.4);
}

:global(.v-theme--dark) .chip-offline {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.4);
}

:global(.v-theme--dark) .chip-unknown {
  background: rgba(107, 114, 128, 0.2);
  color: #9ca3af;
  border-color: rgba(107, 114, 128, 0.4);
}

/* Disconnect Reasons */
.reason-dying-gasp {
  background: rgba(139, 92, 246, 0.15) !important;
  color: #7c3aed !important;
  border-color: rgba(139, 92, 246, 0.35) !important;
}

.reason-dying-gasp:hover {
  background: rgba(139, 92, 246, 0.25) !important;
}

.reason-link-loss {
  background: rgba(245, 158, 11, 0.15) !important;
  color: #b45309 !important;
  border-color: rgba(245, 158, 11, 0.35) !important;
}

.reason-link-loss:hover {
  background: rgba(245, 158, 11, 0.25) !important;
}

:global(.v-theme--dark) .reason-dying-gasp {
  background: rgba(139, 92, 246, 0.2) !important;
  color: #a78bfa !important;
  border-color: rgba(139, 92, 246, 0.4) !important;
}

:global(.v-theme--dark) .reason-link-loss {
  background: rgba(245, 158, 11, 0.2) !important;
  color: #fbbf24 !important;
  border-color: rgba(245, 158, 11, 0.4) !important;
}

/* Highlight */
.is-highlighted {
  box-shadow: 0 0 0 2px rgba(var(--v-theme-accent), 0.6);
  transform: scale(1.04);
}

  .onu-number {
    opacity: 0.9;
    font-weight: 600;
  }

.onu-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reason-icon {
  margin-left: 2px;
  opacity: 0.9;
}
</style>
