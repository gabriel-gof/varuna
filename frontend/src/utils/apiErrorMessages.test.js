import test from 'node:test'
import assert from 'node:assert/strict'

import { getApiErrorMessage, translateBackendMessage } from './apiErrorMessages.js'

const makeTranslator = (dictionary = {}) => (key, vars = {}) => {
  const template = dictionary[key] || key
  return template.replace(/\{\{(\w+)\}\}/g, (_match, name) => String(vars[name] ?? ''))
}

test('translateBackendMessage localizes OLT-prefixed timeout details', () => {
  const t = makeTranslator({
    'Timeout while connecting to "{{target}}".': 'Tempo limite esgotado ao tentar conectar a "{{target}}".',
  })

  const message = 'OLT-BSJ-01: Timeout while connecting to "10.10.50.2:161".'
  const translated = translateBackendMessage(message, t)

  assert.equal(translated, 'OLT-BSJ-01: Tempo limite esgotado ao tentar conectar a "10.10.50.2:161".')
})

test('translateBackendMessage handles stale age interpolation', () => {
  const t = makeTranslator({
    'Last Zabbix status sample is stale ({{seconds}}s old).': 'Última amostra de status do Zabbix está desatualizada (há {{seconds}}s).',
  })

  const translated = translateBackendMessage('Last Zabbix status sample is stale (52s old).', t)
  assert.equal(translated, 'Última amostra de status do Zabbix está desatualizada (há 52s).')
})

test('getApiErrorMessage translates detail payload', () => {
  const t = makeTranslator({
    'Collector reported OLT unreachable': 'Coletor reportou OLT inacessível',
  })
  const err = {
    response: {
      data: {
        detail: 'Collector reported OLT unreachable',
      },
    },
  }

  assert.equal(
    getApiErrorMessage(err, 'fallback', t),
    'Coletor reportou OLT inacessível',
  )
})
