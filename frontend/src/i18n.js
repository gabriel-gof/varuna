import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

const resources = {
  en: {
    translation: {
      VARUNA: 'VARUNA',
      Dashboard: 'Dashboard',
      Topology: 'Topology',
      Search: 'Search devices, IPs or serial numbers...',
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
      Search: 'Buscar dispositivos, IPs ou números de série...',
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
