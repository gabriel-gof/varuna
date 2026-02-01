<template>
  <v-card outlined class="pa-3" @click="$emit('click')">
    <v-row class="align-center">
      <v-col cols="8">
        <div class="text-subtitle-1">{{ olt.name || olt.host }}</div>
        <div class="text-caption">
          {{ olt.vendor_display || olt.vendor_name || 'Unknown' }} {{ olt.model_display || '' }}
        </div>
      </v-col>
      <v-col cols="4" class="text-right">
        <v-badge :color="statusColor" dot>
          <span class="text-body-2">{{ statusLabel }}</span>
        </v-badge>
        <div class="mt-2">
          <v-chip size="small" color="green" class="mr-1">{{ onlineCount }} online</v-chip>
          <v-chip size="small" color="red" v-if="offlineCount > 0">{{ offlineCount }} offline</v-chip>
        </div>
      </v-col>
    </v-row>
    <v-divider class="my-2" />
    <v-row>
      <v-col cols="4"><strong>Slots</strong><div>{{ olt.slot_count || 0 }}</div></v-col>
      <v-col cols="4"><strong>PONs</strong><div>{{ olt.pon_count || 0 }}</div></v-col>
      <v-col cols="4"><strong>ONUs</strong><div>{{ olt.onu_count || 0 }}</div></v-col>
    </v-row>
  </v-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ olt: { type: Object, required: true } })

const statusLabel = computed(() => {
  if (props.olt.is_active === false) return 'Disabled'
  return 'Active'
})
const statusColor = computed(() => {
  if (props.olt.is_active === false) return 'grey'
  return 'green'
})
const onlineCount = computed(() => props.olt.online_count || 0)
const offlineCount = computed(() => props.olt.offline_count || 0)
</script>
