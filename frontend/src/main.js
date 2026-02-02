import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import axios from 'axios'
import App from './App.vue'

import 'vuetify/styles'
import '@mdi/font/css/materialdesignicons.css'
import './styles/theme.css'

// Routes
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { 
      path: '/', 
      name: 'dashboard', 
      component: () => import('./views/DashboardNew.vue'),
      meta: { title: 'Painel' }
    },
    { 
      path: '/settings', 
      name: 'settings', 
      component: () => import('./views/Settings.vue'),
      meta: { title: 'Configurações' }
    },
    // Legacy routes - redirect to new pages
    { path: '/olt-management', redirect: '/settings' },
    { path: '/offline-onus', redirect: '/' },
  ]
})

// Pinia state management
const pinia = createPinia()

// Vuetify configuration
const vuetify = createVuetify({
  components,
  directives,
  theme: {
    defaultTheme: 'light',
    themes: {
      light: {
        dark: false,
        colors: {
          primary: '#1F2A2E',
          secondary: '#7D8C90',
          accent: '#B0874B',
          error: '#B24C43',
          warning: '#C98B3F',
          info: '#3D6F79',
          success: '#2F7D6A',
          background: '#F6F5F2',
          surface: '#FFFFFF',
          'surface-variant': '#F0EEE9',
        },
      },
      dark: {
        dark: true,
        colors: {
          primary: '#8FB3B8',
          secondary: '#8A9AA0',
          accent: '#C99C5E',
          error: '#D36A5F',
          warning: '#D3A45A',
          info: '#6FA6AE',
          success: '#4AA58B',
          background: '#0F1317',
          surface: '#151A1F',
          'surface-variant': '#1C232A',
        },
      },
    },
  },
  defaults: {
    VCard: {
      elevation: 0,
      rounded: 'lg',
      border: true,
    },
    VBtn: {
      rounded: 'lg',
    },
    VTextField: {
      variant: 'outlined',
      density: 'comfortable',
    },
    VSelect: {
      variant: 'outlined',
      density: 'comfortable',
    },
  },
})

// Axios configuration
axios.defaults.baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'

// Create and mount app
const app = createApp(App)
  .use(router)
  .use(pinia)
  .use(vuetify)
  .mount('#app')

export default app
