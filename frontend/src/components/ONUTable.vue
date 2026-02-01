<template>
  <div class="onu-table">
    <v-row class="mb-2" dense>
      <v-col cols="12" sm="6">
        <v-text-field
          v-model="search"
          prepend-inner-icon="mdi-magnify"
          label="Search ONUs"
          density="compact"
          clearable
          hide-details
        ></v-text-field>
      </v-col>
      <v-col cols="12" sm="6">
        <v-select
          v-model="statusFilter"
          :items="statusOptions"
          item-title="label"
          item-value="value"
          label="Status"
          density="compact"
          clearable
          hide-details
        ></v-select>
      </v-col>
    </v-row>

    <v-data-table :items="filteredOnus" :headers="headers" :search="search" density="compact" hover>
      <template #item.name="{ item }">
        {{ item.name || item.serial || `ONU ${item.onu_id}` }}
      </template>
      <template #item.status="{ item }">
        <v-chip :color="statusColor(normalizedStatus(item))" size="small">
          {{ statusLabel(normalizedStatus(item)) }}
        </v-chip>
      </template>
      <template #item.disconnect_reason="{ item }">
        <span v-if="normalizedStatus(item) !== 'online' && item.disconnect_reason" class="text-red">
          {{ reasonLabel(item.disconnect_reason) }}
        </span>
      </template>
    </v-data-table>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ onus: { type: Array, default: () => [] } })

const onuList = computed(() => props.onus || [])

const search = ref('')
const statusFilter = ref(null)

const statusOptions = [
  { label: 'Online', value: 'online' },
  { label: 'Offline', value: 'offline' },
  { label: 'Unknown', value: 'unknown' }
]

const normalizedStatus = (item) => {
  if (item.status) return item.status
  if (item.online === true) return 'online'
  if (item.online === false) return 'offline'
  return 'unknown'
}

const filteredOnus = computed(() => {
  if (!statusFilter.value) return onuList.value
  return onuList.value.filter(item => normalizedStatus(item) === statusFilter.value)
})

const headers = [
  { title: 'ONU ID', key: 'onu_id', width: '80px' },
  { title: 'Name', key: 'name' },
  { title: 'Serial', key: 'serial' },
  { title: 'Status', key: 'status', width: '100px' },
  { title: 'Reason', key: 'disconnect_reason', width: '120px' },
]

const statusColor = (status) => {
  if (status === 'online') return 'green'
  if (status === 'offline') return 'red'
  return 'grey'
}

const statusLabel = (status) => {
  if (status === 'online') return 'Online'
  if (status === 'offline') return 'Offline'
  return 'Unknown'
}

const reasonLabel = (reason) => {
  const reasons = {
    link_loss: 'Link Loss',
    dying_gasp: 'Dying Gasp',
    unknown: 'Unknown'
  }
  return reasons[reason] || reason || ''
}
</script>
