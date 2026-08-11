'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const {
  projectLauncherServiceStatus
} = require('../tools/home-control-launcher/launcher-public-status-projection')

const graphServices = Object.freeze([
  Object.freeze({
    service_id: 'owned_service',
    public_readiness_id: 'owned',
    requirement: 'required',
    ownership: 'owned'
  }),
  Object.freeze({
    service_id: 'external_service',
    public_readiness_id: 'external',
    requirement: 'external',
    ownership: 'external'
  }),
  Object.freeze({
    service_id: 'optional_service',
    public_readiness_id: 'optional',
    requirement: 'optional',
    ownership: 'owned'
  }),
  Object.freeze({
    service_id: 'private_external_service',
    public_readiness_id: null,
    requirement: 'external',
    ownership: 'external'
  })
])

const serviceIdByPublicId = Object.freeze({
  owned: 'owned_service',
  external: 'external_service',
  optional: 'optional_service'
})

const serviceIdForPublicId = (publicId) => {
  if (!Object.hasOwn(serviceIdByPublicId, publicId)) {
    throw new Error('launcher_public_readiness_projection_unmapped')
  }
  return serviceIdByPublicId[publicId]
}

const expectedService = ({ state, enabled, supervisorState, ready, processAlive = false }) => ({
  state,
  enabled,
  supervisor_state: supervisorState,
  readiness_authority: 'node_supervisor',
  processAlive,
  pid: null,
  tcp: { ok: ready, detail: ready ? 'supervisor_ready' : 'supervisor_not_ready' },
  http: { ok: ready, detail: ready ? 'supervisor_ready' : 'supervisor_not_ready' },
  raw_private_publication_flags: false
})

test('one pure projection preserves service order and public readiness semantics', () => {
  const supervisor = {
    phase: 'ready',
    services: [
      { service_id: 'optional_service', state: 'optional_absent' },
      { service_id: 'owned_service', state: 'ready' },
      { service_id: 'external_service', state: 'external_ready' },
      { service_id: 'private_external_service', state: 'external_ready' }
    ]
  }
  const expectedServiceIds = ['owned', 'external', 'optional']
  const before = JSON.parse(JSON.stringify({ supervisor, expectedServiceIds }))

  const projected = projectLauncherServiceStatus({
    supervisor,
    graphServices,
    expectedServiceIds,
    serviceIdForPublicId,
    profileId: 'thought-core-v0'
  })

  assert.deepEqual(Object.keys(projected.services), [
    'owned', 'external', 'optional', 'private_external_service'
  ])
  assert.deepEqual(projected.services, {
    owned: expectedService({
      state: 'OK',
      enabled: true,
      supervisorState: 'ready',
      ready: true,
      processAlive: true
    }),
    external: expectedService({
      state: 'OK_EXTERNAL',
      enabled: true,
      supervisorState: 'external_ready',
      ready: true
    }),
    optional: expectedService({
      state: 'SKIPPED',
      enabled: true,
      supervisorState: 'optional_absent',
      ready: false
    }),
    private_external_service: expectedService({
      state: 'OK_EXTERNAL',
      enabled: true,
      supervisorState: 'external_ready',
      ready: true
    })
  })
  assert.deepEqual(projected.startupTiming, {
    schema_version: 'launcher_startup_timing.v1',
    profileId: 'thought-core-v0',
    status_class: 'ready',
    expectedServiceIds: ['owned', 'external', 'optional'],
    readyServiceIds: ['owned', 'external', 'optional'],
    operational: true,
    readiness_authority: 'node_supervisor',
    raw_private_publication_flags: false
  })
  assert.deepEqual({ supervisor, expectedServiceIds }, before)
})

test('pending, starting and failure states remain presentation-only', () => {
  const projected = projectLauncherServiceStatus({
    supervisor: {
      phase: 'waiting_ready',
      services: [
        { service_id: 'owned_service', state: 'starting' },
        { service_id: 'optional_service', state: 'failed' }
      ]
    },
    graphServices,
    expectedServiceIds: ['owned', 'optional'],
    serviceIdForPublicId,
    profileId: 'thought-core-v0'
  })

  assert.equal(projected.services.owned.state, 'STARTING')
  assert.equal(projected.services.optional.state, 'ERROR')
  assert.equal(projected.services.external.state, 'DOWN')
  assert.deepEqual(projected.startupTiming.readyServiceIds, [])
  assert.equal(projected.startupTiming.operational, false)
})

test('all-skip output stays ordered and an unmapped public id fails closed', () => {
  const allSkip = projectLauncherServiceStatus({
    supervisor: { phase: 'idle', services: [] },
    graphServices,
    expectedServiceIds: [],
    serviceIdForPublicId,
    profileId: 'thought-core-v0'
  })
  assert.deepEqual(allSkip.startupTiming.expectedServiceIds, [])
  assert.deepEqual(allSkip.startupTiming.readyServiceIds, [])
  assert.equal(allSkip.services.external.enabled, true)
  assert.equal(allSkip.services.owned.enabled, false)

  assert.throws(() => projectLauncherServiceStatus({
    supervisor: { phase: 'idle', services: [] },
    graphServices,
    expectedServiceIds: ['constructor'],
    serviceIdForPublicId,
    profileId: 'thought-core-v0'
  }), /launcher_public_readiness_projection_unmapped/)
})

test('projection module has no runtime, process, filesystem, network, cache or lifecycle authority', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'tools', 'home-control-launcher', 'launcher-public-status-projection.js'),
    'utf8'
  )
  for (const forbidden of [
    "require('node:fs')", "require('node:http')", "require('node:https')",
    "require('node:net')", "require('node:child_process')", 'setInterval(',
    'setTimeout(', '.start(', '.stop(', '.apply(', 'writeFile', 'readFile'
  ]) {
    assert.equal(source.includes(forbidden), false, forbidden)
  }
})
