<template>
  <v-expansion-panels variant="accordion" multiple>
    <v-expansion-panel v-for="(pon, idx) in ponList" :key="pon.pon_key || idx">
      <v-expansion-panel-title>
        <div class="d-flex align-center justify-space-between w-100">
          <span>{{ pon.pon_name || `PON ${pon.pon_id}` }}</span>
          <div class="ml-4">
            <v-chip size="small" color="green" class="mr-1">{{ pon.online_count || 0 }} online</v-chip>
            <v-chip size="small" color="red" v-if="pon.offline_count > 0">{{ pon.offline_count }} offline</v-chip>
          </div>
        </div>
      </v-expansion-panel-title>
      <v-expansion-panel-text>
        <slot name="content" :pon="pon">
          <p class="text-caption">No ONUs discovered</p>
        </slot>
      </v-expansion-panel-text>
    </v-expansion-panel>
  </v-expansion-panels>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ pons: { type: [Array, Object], default: () => [] } })

const ponList = computed(() => {
  if (Array.isArray(props.pons)) return props.pons
  return Object.values(props.pons || {})
})
</script>