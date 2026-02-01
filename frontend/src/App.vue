<template>
  <v-app>
    <!-- Navigation Drawer -->
    <v-navigation-drawer v-model="drawer" app>
      <v-list-item class="px-4 py-3">
        <v-list-item-title class="text-h6">
          <v-icon color="primary" class="mr-2">mdi-waves</v-icon>
          Varuna
        </v-list-item-title>
        <v-list-item-subtitle class="text-caption">ONU Monitoring System</v-list-item-subtitle>
      </v-list-item>

      <v-divider></v-divider>

      <v-list density="compact" nav>
        <v-list-item
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          :prepend-icon="item.icon"
          :title="item.title"
          color="primary"
        ></v-list-item>
      </v-list>
    </v-navigation-drawer>

    <!-- App Bar -->
    <v-app-bar app color="primary" density="comfortable">
      <v-app-bar-nav-icon @click="drawer = !drawer"></v-app-bar-nav-icon>
      <v-toolbar-title>Varuna</v-toolbar-title>
      <v-spacer></v-spacer>
      <v-btn icon>
        <v-icon>mdi-bell-outline</v-icon>
      </v-btn>
      <v-btn icon>
        <v-icon>mdi-account-circle</v-icon>
      </v-btn>
    </v-app-bar>

    <!-- Main Content -->
    <v-main>
      <router-view />
    </v-main>

    <!-- Footer -->
    <v-footer app class="text-caption text-grey pa-2">
      <span>Varuna v1.0 - ONU Monitoring System</span>
      <v-spacer></v-spacer>
      <span>{{ currentTime }}</span>
    </v-footer>
  </v-app>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const drawer = ref(true)
const currentTime = ref('')

const navItems = [
  { title: 'Dashboard', icon: 'mdi-view-dashboard', to: '/' },
  { title: 'OLT Management', icon: 'mdi-server-network', to: '/olt-management' },
  { title: 'Offline ONUs', icon: 'mdi-alert-circle-outline', to: '/offline-onus' },
]

const updateTime = () => {
  currentTime.value = new Date().toLocaleString('pt-BR')
}

let timeInterval = null

onMounted(() => {
  updateTime()
  timeInterval = setInterval(updateTime, 1000)
})

onUnmounted(() => {
  if (timeInterval) {
    clearInterval(timeInterval)
  }
})
</script>

<style>
html {
  overflow-y: auto !important;
}
</style>
