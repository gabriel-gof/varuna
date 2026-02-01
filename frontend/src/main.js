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

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue') },
    { path: '/olt-management', name: 'olt-management', component: () => import('./views/OLTManagement.vue') },
    { path: '/offline-onus', name: 'offline-onus', component: () => import('./views/OfflineONUs.vue') },
  ]
})

const pinia = createPinia()
const vuetify = createVuetify({
  components,
  directives,
})

axios.defaults.baseURL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'

const app = createApp(App).use(router).use(pinia).use(vuetify).mount('#app')

export default app
