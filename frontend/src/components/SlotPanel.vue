<template>
  <v-expansion-panels variant="accordion" multiple>
    <v-expansion-panel v-for="(slotItem, idx) in slotList" :key="slotItem.slot_key || idx">
      <v-expansion-panel-title>
        <div class="d-flex align-center justify-space-between w-100">
          <span>{{ slotItem.slot_name || `Slot ${slotItem.slot_id}` }}</span>
          <div class="ml-4">
            <v-chip size="small" color="green" class="mr-1">{{ slotItem.online_count || 0 }} online</v-chip>
            <v-chip size="small" color="red" v-if="slotItem.offline_count > 0">{{ slotItem.offline_count }} offline</v-chip>
          </div>
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <slot name="content" :slot="slotItem">
          <p class="text-caption">No PONs discovered</p>
        </slot>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ slots: { type: [Array, Object], default: () => [] } })

const slotList = computed(() => {
  if (Array.isArray(props.slots)) return props.slots
  return Object.values(props.slots || {})
})
</script>
