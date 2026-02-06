import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

const resources = {
  en: {
    translation: {
      "VARUNA": "VARUNA",
      "Dashboard": "Dashboard",
      "Topology": "Topology",
      "System Configurations": "System Configurations",
      "Settings": "Settings",
      "Logout": "Logout",
      "Search": "Search devices, IPs or serial numbers...",
      "Status": "Status",
      "Potência": "Power",
      "Online": "Online",
      "Offline": "Offline",
      "Dying Gasp": "Dying Gasp",
      "Link Loss": "Link Loss",
      "Unknown": "Unknown",
      "Porta": "Port",
      "Cliente": "Client",
      "Motivo": "Reason",
      "Desconectado em": "Disconnected at",
      "Light": "Light",
      "Dark": "Dark"
    }
  },
  pt: {
    translation: {
      "VARUNA": "VARUNA",
      "Dashboard": "Dashboard",
      "Topology": "Topologia",
      "System Configurations": "Configurações do Sistema",
      "Settings": "Configurações",
      "Logout": "Sair",
      "Search": "Buscar dispositivos, IPs ou números de série...",
      "Status": "Status",
      "Potência": "Potência",
      "Online": "Online",
      "Offline": "Offline",
      "Dying Gasp": "Queda de Energia",
      "Link Loss": "Perda de Link",
      "Unknown": "Desconhecido",
      "Porta": "Porta",
      "Cliente": "Cliente",
      "Motivo": "Motivo",
      "Desconectado em": "Desconectado em",
      "Light": "Claro",
      "Dark": "Escuro"
    }
  }
};

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: 'en',
    interpolation: {
      escapeValue: false
    }
  });

export default i18n;
