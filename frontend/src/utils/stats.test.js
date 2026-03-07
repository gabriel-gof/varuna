import test from 'node:test'
import assert from 'node:assert/strict'

import { classifyOnu, getOnuStats } from './stats.js'

test('classifyOnu collapses plain offline with unknown cause into unknown', () => {
  const classified = classifyOnu({ status: 'offline', disconnect_reason: 'unknown' })

  assert.equal(classified.status, 'unknown')
  assert.equal(classified.label, 'UNKNOWN')
})

test('classifyOnu preserves mapped offline reasons', () => {
  assert.equal(
    classifyOnu({ status: 'offline', disconnect_reason: 'link_loss' }).status,
    'link_loss',
  )
  assert.equal(
    classifyOnu({ status: 'offline', disconnect_reason: 'dying_gasp' }).status,
    'dying_gasp',
  )
})

test('getOnuStats counts unknown-cause outages inside the unknown bucket', () => {
  const stats = getOnuStats([
    { status: 'online', disconnect_reason: '' },
    { status: 'offline', disconnect_reason: 'unknown' },
    { status: 'offline', disconnect_reason: 'link_loss' },
    { status: 'unknown', disconnect_reason: 'unknown' },
  ])

  assert.equal(stats.total, 4)
  assert.equal(stats.online, 1)
  assert.equal(stats.offline, 3)
  assert.equal(stats.linkLoss, 1)
  assert.equal(stats.dyingGasp, 0)
  assert.equal(stats.unknown, 2)
})
