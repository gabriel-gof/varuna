<template>
  <div class="status-badge" :class="[statusClass, sizeClass]">
    <span class="badge-count">{{ count }}</span>
    <span class="badge-label" v-if="showLabel">{{ label }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  count: {
    type: Number,
    default: 0,
  },
  status: {
    type: String,
    default: 'neutral',
    validator: (v) => ['online', 'offline', 'partial', 'neutral', 'unknown'].includes(v),
  },
  label: {
    type: String,
    default: '',
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['x-small', 'small', 'default', 'large'].includes(v),
  },
})

const statusClass = computed(() => `badge-${props.status}`)
const sizeClass = computed(() => `badge-${props.size}`)
const showLabel = computed(() => props.label && props.size !== 'x-small')
</script>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  border-radius: 999px;
  font-weight: 700;
  border: 1px solid transparent;
}

/* Sizes */
.badge-x-small {
  padding: 2px 7px;
  font-size: 12px;
  border-radius: 12px;
}

.badge-small {
  padding: 3px 8px;
  font-size: 13px;
  border-radius: 14px;
}

.badge-default {
  padding: 4px 10px;
  font-size: 13px;
  border-radius: 16px;
}

.badge-large {
  padding: 4px 12px;
  font-size: 14px;
  border-radius: 20px;
}

/* Status Colors - Light theme optimized */
.badge-online {
  background: rgba(16, 185, 129, 0.22);
  color: #065f46;
  border-color: rgba(16, 185, 129, 0.5);
}

.badge-offline {
  background: rgba(239, 68, 68, 0.22);
  color: #991b1b;
  border-color: rgba(239, 68, 68, 0.5);
}

.badge-partial {
  background: rgba(245, 158, 11, 0.22);
  color: #92400e;
  border-color: rgba(245, 158, 11, 0.5);
}

.badge-neutral,
.badge-unknown {
  background: rgba(107, 114, 128, 0.22);
  color: #374151;
  border-color: rgba(107, 114, 128, 0.5);
}

/* Dark theme overrides */
:global(.v-theme--dark) .badge-online {
  background: rgba(16, 185, 129, 0.28);
  color: #34d399;
  border-color: rgba(16, 185, 129, 0.55);
}

:global(.v-theme--dark) .badge-offline {
  background: rgba(239, 68, 68, 0.28);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.55);
}

:global(.v-theme--dark) .badge-partial {
  background: rgba(245, 158, 11, 0.28);
  color: #fbbf24;
  border-color: rgba(245, 158, 11, 0.55);
}

:global(.v-theme--dark) .badge-neutral,
:global(.v-theme--dark) .badge-unknown {
  background: rgba(107, 114, 128, 0.28);
  color: #9ca3af;
  border-color: rgba(107, 114, 128, 0.55);
}

.badge-count {
  font-weight: 700;
}

.badge-label {
    font-weight: 600;
    opacity: 1;
  }
</style>
