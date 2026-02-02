/**
 * Internationalization (i18n) System for Varuna
 * Default language: Portuguese (Brazil) - pt-BR
 * Supported languages: pt-BR, en
 */
import { ref, computed } from 'vue'

// Translations
const translations = {
  'pt-BR': {
    // App
    app: {
      name: 'Varuna',
      subtitle: 'Sistema de Monitoramento de ONUs',
      version: 'v1.0',
    },
    
    // Navigation
    nav: {
      dashboard: 'Painel',
      settings: 'Configurações',
    },

    // UI
    ui: {
      collapseSidebar: 'Recolher menu',
      expandSidebar: 'Expandir menu',
      notifications: 'Notificações',
      profile: 'Perfil',
      logout: 'Sair',
    },
    
    // Dashboard
    dashboard: {
      title: 'Topologia de Rede',
      subtitle: 'Visualização em tempo real da infraestrutura GPON',
      refresh: 'Atualizar',
      lastUpdate: 'Última atualização',
      loading: 'Carregando topologia...',
      noOlts: 'Nenhuma OLT Configurada',
      noOltsDesc: 'Adicione sua primeira OLT para começar a monitorar a rede.',
      addOlt: 'Adicionar OLT',
      expandAll: 'Expandir Tudo',
      collapseAll: 'Recolher Tudo',
      showOfflineOnly: 'Apenas Inativos',
      search: 'Buscar ONU por nome ou serial...',
      filterByOlt: 'Filtrar por OLT',
      allOlts: 'Todas as OLTs',
      selectPonTitle: 'Selecione um PON',
      selectPonSubtitle: 'Escolha um PON na lista para ver os detalhes.',
    },

    // Offline ONUs
    offline: {
      title: 'ONUs Inativas',
      subtitle: 'Visualize ONUs inativas com motivos e timestamps.',
      filterByReason: 'Filtrar por motivo',
      searchByName: 'Buscar por nome ou serial',
      refresh: 'Atualizar',
      totalOffline: 'Total Inativas',
      linkLoss: 'Sem Sinal (LOS)',
      dyingGasp: 'Sem Energia',
      location: 'Localização',
      reason: 'Motivo',
      offlineSince: 'Inativo desde',
      allOnline: 'Todas as ONUs estão ativas!',
    },
    
    // Topology
    topology: {
      olt: 'OLT',
      slot: 'Slot',
      pon: 'PON',
      onu: 'ONU',
      onus: 'ONUs',
      slots: 'Slots',
      pons: 'PONs',
      online: 'Ativo',
      offline: 'Inativo',
      partial: 'Parcial',
      unknown: 'Desconhecido',
      total: 'Total',
      of: 'de',
    },
    
    // Status
    status: {
      online: 'Ativo',
      offline: 'Inativo',
      partial: 'Parcial',
      unknown: 'Desconhecido',
      active: 'Ativo',
      inactive: 'Inativo',
      linkLoss: 'Sem Sinal',
      dyingGasp: 'Sem Energia',
      unknownReason: 'Desconhecido',
    },
    
    // Settings
    settings: {
      title: 'Configurações',
      subtitle: 'Gerencie OLTs, perfis de fabricantes e preferências do sistema',
      general: 'Geral',
      olts: 'OLTs',
      vendors: 'Perfis de Fabricantes',
      language: 'Idioma',
      theme: 'Tema',
      lightTheme: 'Claro',
      darkTheme: 'Escuro',
      autoRefresh: 'Atualização Automática',
      refreshInterval: 'Intervalo de Atualização',
      seconds: 'segundos',
    },
    
    // OLT Management
    olt: {
      title: 'Gerenciamento de OLTs',
      add: 'Adicionar OLT',
      edit: 'Editar OLT',
      delete: 'Excluir OLT',
      name: 'Nome',
      ipAddress: 'Endereço IP',
      vendor: 'Fabricante',
      model: 'Modelo',
      snmpCommunity: 'Comunidade SNMP',
      snmpPort: 'Porta SNMP',
      snmpVersion: 'Versão SNMP',
      discoveryEnabled: 'Descoberta Habilitada',
      pollingEnabled: 'Polling Habilitado',
      save: 'Salvar',
      cancel: 'Cancelar',
      runDiscovery: 'Executar Descoberta',
      viewTopology: 'Ver Topologia',
      lastDiscovery: 'Última Descoberta',
      lastPoll: 'Último Polling',
    },
    
    // Messages
    messages: {
      success: 'Sucesso',
      error: 'Erro',
      warning: 'Atenção',
      info: 'Informação',
      saved: 'Salvo com sucesso',
      deleted: 'Excluído com sucesso',
      loadError: 'Falha ao carregar dados',
      saveError: 'Falha ao salvar',
      confirmDelete: 'Tem certeza que deseja excluir?',
      noData: 'Nenhum dado disponível',
      tryAgain: 'Tentar Novamente',
    },
    
    // Time
    time: {
      now: 'agora',
      minutesAgo: '{n} min atrás',
      hoursAgo: '{n}h atrás',
      daysAgo: '{n}d atrás',
      seconds: 'segundos',
      minutes: 'minutos',
      hours: 'horas',
      days: 'dias',
    },
  },
  
  'en': {
    // App
    app: {
      name: 'Varuna',
      subtitle: 'ONU Monitoring System',
      version: 'v1.0',
    },
    
    // Navigation
    nav: {
      dashboard: 'Dashboard',
      settings: 'Settings',
    },

    // UI
    ui: {
      collapseSidebar: 'Collapse sidebar',
      expandSidebar: 'Expand sidebar',
      notifications: 'Notifications',
      profile: 'Profile',
      logout: 'Logout',
    },
    
    // Dashboard
    dashboard: {
      title: 'Network Topology',
      subtitle: 'Real-time GPON infrastructure visualization',
      refresh: 'Refresh',
      lastUpdate: 'Last update',
      loading: 'Loading topology...',
      noOlts: 'No OLTs Configured',
      noOltsDesc: 'Add your first OLT to start monitoring the network.',
      addOlt: 'Add OLT',
      expandAll: 'Expand All',
      collapseAll: 'Collapse All',
      showOfflineOnly: 'Offline Only',
      search: 'Search ONU by name or serial...',
      filterByOlt: 'Filter by OLT',
      allOlts: 'All OLTs',
      selectPonTitle: 'Select a PON',
      selectPonSubtitle: 'Choose a PON from the list to see details.',
    },

    // Offline ONUs
    offline: {
      title: 'Offline ONUs',
      subtitle: 'View currently offline ONUs with reasons and timestamps.',
      filterByReason: 'Filter by Reason',
      searchByName: 'Search by name or serial',
      refresh: 'Refresh',
      totalOffline: 'Total Offline',
      linkLoss: 'Link Loss (LOS)',
      dyingGasp: 'Dying Gasp',
      location: 'Location',
      reason: 'Reason',
      offlineSince: 'Offline Since',
      allOnline: 'All ONUs are online!',
    },
    
    // Topology
    topology: {
      olt: 'OLT',
      slot: 'Slot',
      pon: 'PON',
      onu: 'ONU',
      onus: 'ONUs',
      slots: 'Slots',
      pons: 'PONs',
      online: 'Online',
      offline: 'Offline',
      partial: 'Partial',
      unknown: 'Unknown',
      total: 'Total',
      of: 'of',
    },
    
    // Status
    status: {
      online: 'Online',
      offline: 'Offline',
      partial: 'Partial',
      unknown: 'Unknown',
      active: 'Active',
      inactive: 'Inactive',
      linkLoss: 'Link Loss',
      dyingGasp: 'Dying Gasp',
      unknownReason: 'Unknown Reason',
    },
    
    // Settings
    settings: {
      title: 'Settings',
      subtitle: 'Manage OLTs, vendor profiles and system preferences',
      general: 'General',
      olts: 'OLTs',
      vendors: 'Vendor Profiles',
      language: 'Language',
      theme: 'Theme',
      lightTheme: 'Light',
      darkTheme: 'Dark',
      autoRefresh: 'Auto Refresh',
      refreshInterval: 'Refresh Interval',
      seconds: 'seconds',
    },
    
    // OLT Management
    olt: {
      title: 'OLT Management',
      add: 'Add OLT',
      edit: 'Edit OLT',
      delete: 'Delete OLT',
      name: 'Name',
      ipAddress: 'IP Address',
      vendor: 'Vendor',
      model: 'Model',
      snmpCommunity: 'SNMP Community',
      snmpPort: 'SNMP Port',
      snmpVersion: 'SNMP Version',
      discoveryEnabled: 'Discovery Enabled',
      pollingEnabled: 'Polling Enabled',
      save: 'Save',
      cancel: 'Cancel',
      runDiscovery: 'Run Discovery',
      viewTopology: 'View Topology',
      lastDiscovery: 'Last Discovery',
      lastPoll: 'Last Poll',
    },
    
    // Messages
    messages: {
      success: 'Success',
      error: 'Error',
      warning: 'Warning',
      info: 'Information',
      saved: 'Saved successfully',
      deleted: 'Deleted successfully',
      loadError: 'Failed to load data',
      saveError: 'Failed to save',
      confirmDelete: 'Are you sure you want to delete?',
      noData: 'No data available',
      tryAgain: 'Try Again',
    },
    
    // Time
    time: {
      now: 'now',
      minutesAgo: '{n} min ago',
      hoursAgo: '{n}h ago',
      daysAgo: '{n}d ago',
      seconds: 'seconds',
      minutes: 'minutes',
      hours: 'hours',
      days: 'days',
    },
  },
}

// Current locale state
const STORAGE_KEY = 'varuna:locale'
const DEFAULT_LOCALE = 'pt-BR'
const SUPPORTED_LOCALES = ['pt-BR', 'en']

// Get initial locale from localStorage or use default
const getInitialLocale = () => {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && SUPPORTED_LOCALES.includes(stored)) {
      return stored
    }
  }
  return DEFAULT_LOCALE
}

const currentLocale = ref(getInitialLocale())

// Translation function
const t = (key, params = {}) => {
  const keys = key.split('.')
  let value = translations[currentLocale.value]
  
  for (const k of keys) {
    if (value && typeof value === 'object' && k in value) {
      value = value[k]
    } else {
      // Fallback to English if key not found
      value = translations['en']
      for (const fk of keys) {
        if (value && typeof value === 'object' && fk in value) {
          value = value[fk]
        } else {
          return key // Return key if not found anywhere
        }
      }
      break
    }
  }
  
  if (typeof value !== 'string') {
    return key
  }
  
  // Replace parameters like {n}
  return value.replace(/\{(\w+)\}/g, (_, p) => params[p] ?? `{${p}}`)
}

// Set locale
const setLocale = (locale) => {
  if (SUPPORTED_LOCALES.includes(locale)) {
    currentLocale.value = locale
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, locale)
    }
  }
}

// Get available locales
const getLocales = () => SUPPORTED_LOCALES.map(code => ({
  code,
  name: code === 'pt-BR' ? 'Português (Brasil)' : 'English',
  flag: code === 'pt-BR' ? '🇧🇷' : '🇺🇸',
}))

// Computed locale info
const localeInfo = computed(() => {
  const locales = getLocales()
  return locales.find(l => l.code === currentLocale.value) || locales[0]
})

// Format relative time
const formatRelativeTime = (timestamp) => {
  if (!timestamp) return ''
  
  const date = new Date(timestamp)
  const now = new Date()
  const diffMs = now - date
  const diffMinutes = Math.floor(diffMs / (1000 * 60))
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)
  
  if (diffMinutes < 1) return t('time.now')
  if (diffMinutes < 60) return t('time.minutesAgo', { n: diffMinutes })
  if (diffHours < 24) return t('time.hoursAgo', { n: diffHours })
  return t('time.daysAgo', { n: diffDays })
}

export {
  t,
  setLocale,
  getLocales,
  currentLocale,
  localeInfo,
  formatRelativeTime,
  SUPPORTED_LOCALES,
  DEFAULT_LOCALE,
}

export default {
  t,
  setLocale,
  getLocales,
  currentLocale,
  localeInfo,
  formatRelativeTime,
}
