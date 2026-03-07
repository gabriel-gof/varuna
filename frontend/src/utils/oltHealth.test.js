import test from 'node:test'
import assert from 'node:assert/strict'

import { deriveOltHealthState, getPonHealthColorState } from './oltHealth.js'

const NOW_MS = Date.parse('2026-02-24T18:00:00.000Z')

const isoAgoSeconds = (seconds) => new Date(NOW_MS - (seconds * 1000)).toISOString()

test('deriveOltHealthState marks OLT gray after repeated SNMP failures', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: false,
      snmp_failure_count: 2,
      last_poll_at: isoAgoSeconds(120),
      polling_interval_seconds: 300,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'snmp_unreachable')
})

test('deriveOltHealthState marks OLT gray on first explicit SNMP unreachable state', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: false,
      snmp_failure_count: 1,
      last_poll_at: isoAgoSeconds(120),
      polling_interval_seconds: 300,
      online_count: 1,
      offline_count: 0,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'snmp_unreachable')
})

test('deriveOltHealthState marks OLT gray when status polling is stale', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(1200),
      polling_interval_seconds: 300,
      online_count: 1,
      offline_count: 0,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'status_stale')
})

test('deriveOltHealthState keeps OLT gray when status polling is stale even with fresh SNMP check', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(1800),
      last_snmp_check_at: isoAgoSeconds(20),
      polling_interval_seconds: 300,
      online_count: 1,
      offline_count: 0,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'status_stale')
})

test('deriveOltHealthState uses discovery timestamp when polling timestamp is absent', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_discovery_at: isoAgoSeconds(1200),
      polling_interval_seconds: 300,
      online_count: 1,
      offline_count: 0,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'status_stale')
})

test('deriveOltHealthState keeps OLT gray when polling timestamp is absent even with fresh SNMP check', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_discovery_at: isoAgoSeconds(1200),
      last_snmp_check_at: isoAgoSeconds(25),
      polling_interval_seconds: 300,
      online_count: 1,
      offline_count: 0,
    },
    NOW_MS,
  )
  assert.equal(state.state, 'gray')
  assert.equal(state.reason, 'status_stale')
})

test('getPonHealthColorState treats unknown ONUs as offline for color decisions', () => {
  assert.equal(
    getPonHealthColorState({
      is_active: true,
      onus: [
        { status: 'unknown', disconnect_reason: '' },
        { status: 'offline', disconnect_reason: 'unknown' },
      ],
    }),
    'red',
  )
})

test('deriveOltHealthState marks OLT red when ONUs are only unknown', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(60),
      polling_interval_seconds: 300,
      slots: [
        {
          is_active: true,
          pons: [
            {
              is_active: true,
              onus: [
                { status: 'unknown', disconnect_reason: '' },
                { status: 'unknown', disconnect_reason: 'unknown' },
              ],
            },
          ],
        },
      ],
    },
    NOW_MS,
  )
  assert.equal(state.state, 'red')
  assert.equal(state.reason, 'all_slots_red')
})

test('deriveOltHealthState keeps OLT green when at least one ONU is online', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(60),
      polling_interval_seconds: 300,
      slots: [
        {
          is_active: true,
          pons: [
            {
              is_active: true,
              onus: [
                { status: 'online', disconnect_reason: '' },
                { status: 'offline', disconnect_reason: 'link_loss' },
                { status: 'unknown', disconnect_reason: 'unknown' },
              ],
            },
          ],
        },
      ],
    },
    NOW_MS,
  )
  assert.equal(state.state, 'green')
})

test('deriveOltHealthState keeps red when all ONUs are confirmed offline reasons', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(60),
      polling_interval_seconds: 300,
      slots: [
        {
          is_active: true,
          pons: [
            {
              is_active: true,
              onus: [
                { status: 'offline', disconnect_reason: 'link_loss' },
                { status: 'offline', disconnect_reason: 'dying_gasp' },
              ],
            },
          ],
        },
      ],
    },
    NOW_MS,
  )
  assert.equal(state.state, 'red')
})

test('deriveOltHealthState marks OLT yellow when at least one slot is fully offline (red)', () => {
  const state = deriveOltHealthState(
    {
      snmp_reachable: true,
      snmp_failure_count: 0,
      last_poll_at: isoAgoSeconds(60),
      polling_interval_seconds: 300,
      slots: [
        {
          is_active: true,
          pons: [
            {
              is_active: true,
              onus: [
                { status: 'offline', disconnect_reason: 'link_loss' },
                { status: 'offline', disconnect_reason: 'dying_gasp' },
              ],
            },
          ],
        },
        {
          is_active: true,
          pons: [
            {
              is_active: true,
              onus: [
                { status: 'online', disconnect_reason: '' },
                { status: 'offline', disconnect_reason: 'link_loss' },
              ],
            },
          ],
        },
      ],
    },
    NOW_MS,
  )
  assert.equal(state.state, 'yellow')
  assert.equal(state.reason, 'at_least_one_slot_red')
})
