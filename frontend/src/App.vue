<template>
  <v-app :theme="currentTheme">
    <!-- Navigation Drawer -->
    <v-navigation-drawer 
      v-model="drawer" 
      app 
      :width="sidebarCollapsed ? 64 : 200" 
      class="nav-drawer" 
      permanent
      :rail="sidebarCollapsed"
      rail-width="64"
      :expand-on-hover="sidebarCollapsed"
    >
      <!-- Logo / Brand -->
      <div class="drawer-header" :class="{ 'collapsed': sidebarCollapsed }">
        <div class="brand-logo">
          <img :src="varunaIcon" alt="Varuna" class="brand-mark" />
        </div>

        <transition name="fade">
          <div class="brand-info" v-if="!sidebarCollapsed">
            <div class="brand-name">{{ t('app.name') }}</div>
          </div>
        </transition>

        <v-tooltip :text="sidebarCollapsed ? t('ui.expandSidebar') : t('ui.collapseSidebar')" location="end">
          <template #activator="{ props: tooltipProps }">
            <v-btn 
              v-bind="tooltipProps"
              icon 
              variant="text" 
              size="small" 
              class="drawer-toggle"
              @click="toggleSidebar"
            >
              <v-icon size="20">{{ sidebarCollapsed ? 'mdi-chevron-right' : 'mdi-chevron-left' }}</v-icon>
            </v-btn>
          </template>
        </v-tooltip>
      </div>

      <v-divider></v-divider>

      <!-- Navigation -->
      <v-list density="compact" nav class="px-2 py-3">
        <v-tooltip 
          v-for="item in navItems"
          :key="item.to"
          :text="item.title"
          location="end"
          :disabled="!sidebarCollapsed"
        >
          <template #activator="{ props: tooltipProps }">
            <v-list-item
              :to="item.to"
              :value="item.to"
              color="primary"
              rounded="lg"
              class="nav-item mb-1"
              v-bind="tooltipProps"
              :active="route.path === item.to"
            >
              <template #prepend>
                <span class="nav-icon" :class="{ 'is-active': route.path === item.to }">
                  <v-icon v-if="item.iconType === 'mdi'" size="20">{{ item.icon }}</v-icon>
                  <img v-else :src="item.icon" alt="" />
                </span>
              </template>
              <v-list-item-title>{{ item.title }}</v-list-item-title>
            </v-list-item>
          </template>
        </v-tooltip>
      </v-list>

      <template #append>
        <v-divider></v-divider>
        <div class="drawer-footer" :class="{ 'collapsed': sidebarCollapsed }">
          <!-- Notifications -->
          <v-tooltip :text="t('ui.notifications')" location="end" :disabled="!sidebarCollapsed">
            <template #activator="{ props: tooltipProps }">
              <v-btn 
                variant="text" 
                class="sidebar-btn" 
                :class="{ 'icon-only': sidebarCollapsed }"
                v-bind="tooltipProps"
              >
                <v-badge color="error" content="3" dot offset-x="-4" offset-y="-4">
                  <v-icon size="20">mdi-bell-outline</v-icon>
                </v-badge>
                <span class="ml-3 btn-label" v-if="!sidebarCollapsed">{{ t('ui.notifications') }}</span>
                <v-spacer v-if="!sidebarCollapsed"></v-spacer>
              </v-btn>
            </template>
          </v-tooltip>
          
          <!-- User Profile -->
          <v-menu location="top end" :close-on-content-click="false">
            <template #activator="{ props: menuProps }">
              <v-tooltip :text="t('ui.profile')" location="end" :disabled="!sidebarCollapsed">
                <template #activator="{ props: tooltipProps }">
                  <v-btn 
                    variant="text" 
                    class="sidebar-btn profile-btn" 
                    :class="{ 'icon-only': sidebarCollapsed }"
                    v-bind="{ ...menuProps, ...tooltipProps }"
                  >
                    <v-avatar color="primary" size="28">
                      <v-icon size="18">mdi-account</v-icon>
                    </v-avatar>
                    <span class="ml-3 btn-label" v-if="!sidebarCollapsed">Admin</span>
                    <v-spacer v-if="!sidebarCollapsed"></v-spacer>
                  </v-btn>
                </template>
              </v-tooltip>
            </template>
            <v-card min-width="260" class="profile-menu">
              <v-card-text class="pa-4">
                <!-- Language Selection -->
                <div class="menu-section">
                  <div class="menu-label">{{ t('settings.language') }}</div>
                  <v-btn-toggle 
                    v-model="selectedLocale" 
                    mandatory 
                    density="compact" 
                    class="locale-toggle w-100"
                    @update:model-value="changeLocale"
                  >
                    <v-btn value="pt-BR" class="flex-grow-1">PT-BR</v-btn>
                    <v-btn value="en" class="flex-grow-1">EN</v-btn>
                  </v-btn-toggle>
                </div>
                
                <!-- Theme Selection -->
                <div class="menu-section mt-4">
                  <div class="menu-label">{{ t('settings.theme') }}</div>
                  <v-btn-toggle 
                    v-model="themeMode" 
                    mandatory 
                    density="compact" 
                    class="theme-toggle w-100"
                  >
                    <v-btn value="light" class="flex-grow-1">
                      <v-icon start size="16">mdi-white-balance-sunny</v-icon>
                      {{ t('settings.lightTheme') }}
                    </v-btn>
                    <v-btn value="dark" class="flex-grow-1">
                      <v-icon start size="16">mdi-weather-night</v-icon>
                      {{ t('settings.darkTheme') }}
                    </v-btn>
                  </v-btn-toggle>
                </div>
                
                <v-divider class="my-4"></v-divider>
                <v-btn variant="text" block class="justify-start text-error" prepend-icon="mdi-logout">
                  {{ t('ui.logout') }}
                </v-btn>
              </v-card-text>
            </v-card>
          </v-menu>
          
          <!-- Version -->
          <transition name="fade">
            <div class="version-info" v-if="!sidebarCollapsed">{{ t('app.version') }}</div>
          </transition>
        </div>
      </template>
    </v-navigation-drawer>

    <!-- Main Content (no app bar) -->
    <v-main class="main-content">
      <router-view />
    </v-main>
  </v-app>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { t, setLocale, currentLocale } from '@/i18n'

import panelIcon from '@/assets/panel.svg'
import varunaIcon from '@/assets/new_varuna-logo.png'

const route = useRoute()
// State
const drawer = ref(true)
const sidebarCollapsed = ref(false)
const isDark = ref(false)
const themeMode = ref('light')
const selectedLocale = ref(currentLocale.value)

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
}

// Navigation items
const navItems = computed(() => [
  { 
    title: t('nav.dashboard'), 
    icon: panelIcon, 
    to: '/',
    iconType: 'svg'
  },
  { 
    title: t('nav.settings'), 
    icon: 'mdi-cog-outline', 
    to: '/settings',
    iconType: 'mdi'
  },
])

// Theme
const currentTheme = computed(() => isDark.value ? 'dark' : 'light')

// Watch theme mode toggle
watch(themeMode, (newValue) => {
  isDark.value = newValue === 'dark'
  localStorage.setItem('varuna:theme', newValue)
})

// Locale change
const changeLocale = (locale) => {
  setLocale(locale)
}

onMounted(() => {
  // Load saved theme
  const savedTheme = localStorage.getItem('varuna:theme')
  if (savedTheme) {
    isDark.value = savedTheme === 'dark'
    themeMode.value = savedTheme
  }
  
  // Load saved sidebar state
  const savedCollapsed = localStorage.getItem('varuna:sidebar-collapsed')
  if (savedCollapsed !== null) {
    sidebarCollapsed.value = savedCollapsed === 'true'
  }
})

// Watch sidebar collapse state
watch(sidebarCollapsed, (newValue) => {
  localStorage.setItem('varuna:sidebar-collapsed', String(newValue))
})
</script>

<style>
html {
  overflow-y: auto !important;
}

.v-application {
  font-family: var(--varuna-font-body);
}
</style>

<style scoped>
.nav-drawer {
  background: var(--varuna-panel-strong);
  border-right: 1px solid var(--varuna-card-border);
  box-shadow: var(--varuna-shadow-sm);
  transition: width 0.2s ease;
}

.drawer-header {
  display: flex;
  align-items: center;
  padding: 12px;
  gap: 10px;
  border-bottom: 1px solid var(--varuna-line);
  position: relative;
  min-height: 56px;
}

.drawer-header.collapsed {
  padding: 10px;
  gap: 8px;
}

.drawer-toggle {
  margin-left: auto;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  opacity: 0.6;
  transition: opacity 0.2s ease;
}

.drawer-toggle:hover {
  opacity: 1;
}

.brand-logo {
  width: 44px;
  height: 44px;
  min-width: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.drawer-header.collapsed .brand-logo {
  width: 36px;
  height: 36px;
  min-width: 36px;
}

.brand-mark {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.brand-info {
  flex: 1;
  min-width: 0;
}

.brand-name {
  font-size: 18px;
  font-weight: 700;
  color: rgb(var(--v-theme-on-surface));
  line-height: 1.2;
  letter-spacing: 0.01em;
  font-family: var(--varuna-font-display);
  white-space: nowrap;
}

.drawer-footer {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.drawer-footer.collapsed {
  padding: 12px 8px;
  align-items: center;
}

.sidebar-btn {
  justify-content: flex-start;
  text-transform: none;
  font-weight: 400;
  font-size: 13px;
  color: rgb(var(--v-theme-on-surface));
  padding: 8px 12px;
  width: 100%;
  min-height: 40px;
}

.sidebar-btn.icon-only {
  justify-content: center;
  padding: 8px;
  min-width: 48px;
  width: 48px;
}

.sidebar-btn .btn-label {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-btn:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.profile-btn {
  margin-top: 4px;
}


.profile-menu {
  border: 1px solid var(--varuna-line-strong);
  box-shadow: var(--varuna-shadow-md);
  background: var(--varuna-panel-strong);
}

.menu-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.menu-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.75;
}

.locale-toggle,
.theme-toggle {
  border: 1px solid var(--varuna-line-strong);
  border-radius: 10px;
  background: var(--varuna-surface-2);
}

.locale-toggle .v-btn,
.theme-toggle .v-btn {
  font-size: 13px;
  font-weight: 600;
  color: rgb(var(--v-theme-on-surface));
}

.locale-toggle :deep(.v-btn--active),
.theme-toggle :deep(.v-btn--active) {
  background: rgba(var(--v-theme-primary), 0.16) !important;
  color: rgb(var(--v-theme-on-surface)) !important;
}

.profile-menu :deep(.v-divider) {
  border-color: var(--varuna-line-strong);
}

.nav-item {
  border: 1px solid transparent;
  background: transparent;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}

.nav-item:hover {
  background: rgba(var(--v-theme-primary), 0.06);
  border-color: rgba(var(--v-theme-primary), 0.12);
}

.nav-item.v-list-item--active {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.22);
}

.version-info {
  font-size: 11px;
  color: rgb(var(--v-theme-on-surface-variant));
  text-align: center;
  margin-top: 8px;
  opacity: 0.7;
}


.nav-icon {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.nav-icon img {
  width: 20px;
  height: 20px;
  display: block;
  opacity: 0.65;
  transition: opacity 0.2s ease;
}

.nav-item:hover .nav-icon img,
.nav-icon.is-active img {
  opacity: 1;
}

.v-theme--dark .nav-icon img {
  filter: invert(1);
  opacity: 0.8;
}

.v-theme--dark .nav-item:hover .nav-icon img,
.v-theme--dark .nav-icon.is-active img {
  opacity: 1;
}

.nav-item :deep(.v-list-item__prepend) {
  margin-inline-end: 12px;
}

.nav-drawer.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) :deep(.v-list-item__content) {
  display: none;
}

.nav-drawer.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) :deep(.v-list-item) {
  justify-content: center;
  padding-inline: 0;
}

.nav-drawer.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) :deep(.v-list-item__prepend) {
  margin-inline-end: 0;
}

.main-content {
  background: transparent;
}

/* Fade transition */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
