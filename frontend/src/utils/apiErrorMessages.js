const BACKEND_MESSAGE_MAP = {
  'Power refresh scheduled in background.': 'Power refresh scheduled in background.',
  'Discovery scheduled in background.': 'Discovery scheduled in background.',
  'Polling scheduled in background.': 'Polling scheduled in background.',
  'Another maintenance task is already running for this OLT.': 'Another maintenance task is already running for this OLT.',
  'Vendor profile is inactive.': 'Vendor profile is inactive.',
  'Vendor profile does not support this action.': 'Vendor profile does not support this action.',
  'Vendor profile is missing required OID templates.': 'Vendor profile is missing required OID templates.',
  'Name cannot be empty.': 'Name cannot be empty.',
  'An active OLT with this name already exists.': 'An active OLT with this name already exists.',
  'Only SNMP protocol is supported.': 'Only SNMP protocol is supported.',
  'SNMP community cannot be empty.': 'SNMP community cannot be empty.',
  'SNMP port must be an integer.': 'SNMP port must be an integer.',
  'SNMP port must be between 1 and 65535.': 'SNMP port must be between 1 and 65535.',
  'Only SNMP v2c is currently supported.': 'Only SNMP v2c is currently supported.',
  'history_days must be between 7 and 30.': 'history_days must be between 7 and 30.',
  'Discovery interval must be greater than 0 minutes.': 'Discovery interval must be greater than 0 minutes.',
  'Polling interval must be greater than 0 seconds.': 'Polling interval must be greater than 0 seconds.',
  'Power interval must be greater than 0 seconds.': 'Power interval must be greater than 0 seconds.',
  'Insufficient permissions for this action.': 'Insufficient permissions for this action.',
  'Zabbix host not found.': 'Zabbix host not found.',
  'No SNMP interface status available in Zabbix.': 'No SNMP interface status available in Zabbix.',
  'No recent status collection in Zabbix.': 'No recent status collection in Zabbix.',
  'Collector reported OLT unreachable': 'Collector reported OLT unreachable',
  'Zabbix reported OLT unreachable': 'Zabbix reported OLT unreachable',
}

const BACKEND_PREFIX_PATTERNS = [
  { prefix: 'Discovery interval must be <=', key: 'Discovery interval exceeds maximum' },
  { prefix: 'Polling interval must be <=', key: 'Polling interval exceeds maximum' },
  { prefix: 'Power interval must be <=', key: 'Power interval exceeds maximum' },
  { prefix: 'Vendor profile does not support', key: 'Vendor profile does not support this action.' },
]

const BACKEND_REGEX_PATTERNS = [
  {
    regex: /^Timeout while connecting to ["']?([^"']+)["']?\.?$/i,
    translate: (match, t) => t('Timeout while connecting to "{{target}}".', { target: match[1] }),
  },
  {
    regex: /^Last SNMP availability sample is stale \((\d+)s old\)\.?$/i,
    translate: (match, t) => t('Last SNMP availability sample is stale ({{seconds}}s old).', { seconds: match[1] }),
  },
  {
    regex: /^Last Zabbix status sample is stale \((\d+)s old\)\.?$/i,
    translate: (match, t) => t('Last Zabbix status sample is stale ({{seconds}}s old).', { seconds: match[1] }),
  },
  {
    regex: /^Connection refused\.?$/i,
    translate: (_match, t) => t('Connection refused.'),
  },
  {
    regex: /^No route to host\.?$/i,
    translate: (_match, t) => t('No route to host.'),
  },
  {
    regex: /^Network is unreachable\.?$/i,
    translate: (_match, t) => t('Network is unreachable.'),
  },
  {
    regex: /^Name or service not known\.?$/i,
    translate: (_match, t) => t('Name or service not known.'),
  },
]

const asTranslatedValue = (value, t) => {
  if (value === null || value === undefined) return ''
  return translateBackendMessage(String(value), t)
}

export const translateBackendMessage = (message, t) => {
  if (!message || typeof t !== 'function') return message

  const normalized = String(message).trim()
  if (!normalized) return normalized

  if (normalized.includes(';')) {
    const parts = normalized.split(/\s*;\s*/).filter(Boolean)
    if (parts.length > 1) {
      return parts.map((part) => translateBackendMessage(part, t)).join('; ')
    }
  }

  const prefixed = normalized.match(/^([^:]+):\s+(.+)$/)
  if (prefixed) {
    const prefix = prefixed[1].trim()
    const detail = prefixed[2].trim()
    return `${prefix}: ${translateBackendMessage(detail, t)}`
  }

  const exactKey = BACKEND_MESSAGE_MAP[normalized]
  if (exactKey) return t(exactKey)

  for (const { prefix, key } of BACKEND_PREFIX_PATTERNS) {
    if (normalized.startsWith(prefix)) return t(key)
  }

  for (const { regex, translate } of BACKEND_REGEX_PATTERNS) {
    const match = normalized.match(regex)
    if (match) return translate(match, t)
  }

  return normalized
}

export const getApiErrorMessage = (error, fallback, t) => {
  const data = error?.response?.data
  if (typeof data === 'string' && data.trim()) return translateBackendMessage(data.trim(), t)
  if (data?.detail) return translateBackendMessage(String(data.detail), t)

  if (data && typeof data === 'object') {
    const parts = Object.entries(data)
      .map(([key, value]) => {
        if (Array.isArray(value)) {
          const values = value.map((item) => asTranslatedValue(item, t)).join(', ')
          return `${key}: ${values}`
        }
        if (value && typeof value === 'object') {
          return `${key}: ${JSON.stringify(value)}`
        }
        return `${key}: ${asTranslatedValue(value, t)}`
      })
      .filter(Boolean)
    if (parts.length) return parts.join(' | ')
  }

  const fallbackValue = error?.message || fallback || ''
  return translateBackendMessage(String(fallbackValue), t)
}
