import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

const resources = {
  en: {
    translation: {
      VARUNA: 'VARUNA',
      Dashboard: 'Dashboard',
      Topology: 'Topology',
      Search: 'Search by login or ONU serial...',
      'Filter OLTs': 'Filter OLTs',
      Collapse: 'Collapse',
      Alarm: 'Alarm',
      'Alarm Mode': 'Alarm mode',
      'Only show PONs with offline ONUs': 'Only show PONs with offline ONUs',
      'Offline reasons': 'Offline reasons',
      'Minimum ONU count': 'Minimum ONU count',
      All: 'All',
      Clear: 'Clear',
      Close: 'Close',
      Status: 'Status',
      Potência: 'Power',
      Online: 'Online',
      Offline: 'Offline',
      'Dying Gasp': 'Dying Gasp',
      'Link Loss': 'Link Loss',
      Unknown: 'Unknown',
      Port: 'Port',
      Client: 'Client',
      THEME: 'Theme',
      LANGUAGE: 'Language',
      LIGHT: 'Light',
      DARK: 'Dark',
      LOGOUT: 'Logout',
      'No ZTE OLTs found': 'No ZTE OLTs found',
      'No equipment matches your search': 'No equipment matches your search',
      'No PON matches alarm filter': 'No PON matches alarm filter',
      'No ONU data available': 'No ONU data available',
      'Select a PON to view details': 'Select a PON to view details',
      'Power data not available': 'Power data not available'
    }
  },
  pt: {
    translation: {
      VARUNA: 'VARUNA',
      Dashboard: 'Dashboard',
      Topology: 'Topologia',
      Search: 'Buscar por login ou serial da ONU...',
      'Filter OLTs': 'Filtrar OLTs',
      Collapse: 'Recolher',
      Alarm: 'Alarme',
      'Alarm Mode': 'Modo alarme',
      'Only show PONs with offline ONUs': 'Mostrar apenas PONs com ONUs offline',
      'Offline reasons': 'Motivos offline',
      'Minimum ONU count': 'Mínimo de ONUs',
      All: 'Todos',
      Clear: 'Limpar',
      Close: 'Fechar',
      Status: 'Status',
      Potência: 'Potência',
      Online: 'Online',
      Offline: 'Offline',
      'Dying Gasp': 'Queda de Energia',
      'Link Loss': 'Perda de Link',
      Unknown: 'Desconhecido',
      Port: 'Porta',
      Client: 'Cliente',
      THEME: 'Tema',
      LANGUAGE: 'Idioma',
      LIGHT: 'Claro',
      DARK: 'Escuro',
      LOGOUT: 'Sair',
      'No ZTE OLTs found': 'Nenhuma OLT ZTE encontrada',
      'No equipment matches your search': 'Nenhum equipamento corresponde à busca',
      'No PON matches alarm filter': 'Nenhuma PON corresponde ao filtro de alarme',
      'No ONU data available': 'Nenhum dado de ONU disponível',
      'Select a PON to view details': 'Selecione uma PON para ver detalhes',
      'Power data not available': 'Dados de potência indisponíveis'
    }
  }
}

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: 'pt',
    interpolation: {
      escapeValue: false,
    },
  })

export default i18n
