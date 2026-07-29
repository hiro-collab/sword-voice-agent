'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const { loadAuthority } = require('../tools/home-control-launcher/launcher-supervisor-contract')
const { LauncherProbeContractError, loadProbeAuthority } = require('../tools/home-control-launcher/launcher-probe-result-binding')
const {
  LauncherProbeExecutor,
  LauncherProbeExecutorError,
  MAX_BODY_BYTES
} = require('../tools/home-control-launcher/launcher-probe-executor')

const ROOT = path.resolve(__dirname, '..')
const launcherAuthority = loadAuthority(ROOT)
const probeAuthority = loadProbeAuthority(ROOT, launcherAuthority)
const BASE_MS = Date.parse('2026-07-29T12:00:00.000Z')
const CONFIG_SHA256 = 'c'.repeat(64)
const ENVIRONMENT_READY_IDENTITY = Object.freeze({
  profile_id: 'thought-core-v0',
  effective_config_sha256: 'e'.repeat(64),
  camera_policy: 'camera_excluded_by_profile'
})
const descriptorFor = (serviceId) => probeAuthority.descriptors.find((item) => item.service_id === serviceId)
const expectedFor = (serviceId = 'home_assistant_bridge', values = {}) => {
  const descriptor = descriptorFor(serviceId)
  return {
    operation_id: 'lop_executor0001',
    supervisor_generation: 7,
    dispatch_id: 'ld_executor0000000001',
    expected_revision: 23,
    service_id: descriptor.service_id,
    probe_id: descriptor.probe_id,
    graph_sha256: probeAuthority.identities.graphSha256,
    binding_sha256: probeAuthority.identities.bindingSha256,
    descriptor_sha256: descriptor.descriptor_sha256,
    config_sha256: CONFIG_SHA256,
    requested_at: new Date(BASE_MS).toISOString(),
    ...values
  }
}

const jsonResponse = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json', ...headers }
})
const textResponse = (value, status = 200, headers = {}) => new Response(value, { status, headers })

const endpointPorts = Object.freeze({
  home_assistant_bridge_http: 8787,
  environment_state_http: 8790,
  provider_broker_http: 18786,
  thought_core_http: 18787,
  mediapipe_websocket: 8765,
  vision_snapshot_websocket: 8776,
  aituber_http: 3000,
  touchdesigner_gui_http: 8788,
  voicevox_http: 50021
})

const resolver = (secret = null) => async (target) => ({
  url: `${target.transport === 'websocket' ? 'ws' : 'http'}://127.0.0.1:${endpointPorts[target.endpoint_ref]}${target.path}`,
  headers: target.auth_class === 'private_secret_ref' ? { Authorization: `Bearer ${secret}` } : {}
})

const makeExecutor = ({ fetchImpl, observers = {}, now = BASE_MS + 200, secret = 'PRIVATE_HEADER_SENTINEL' }) => new LauncherProbeExecutor({
  probeAuthority,
  environmentReadyIdentity: ENVIRONMENT_READY_IDENTITY,
  privateTargetResolver: resolver(secret),
  fetchImpl,
  observers,
  clock: () => now,
  monotonicClock: () => 10
})

const assertBoundResult = (result, expected, semanticClass) => {
  assert.equal(result.schema_version, 'launcher_probe_result.v1')
  assert.equal(result.message_type, 'result')
  assert.equal(result.semantic_class, semanticClass, expected.service_id)
  assert.equal(result.operation_id, expected.operation_id)
  assert.equal(result.supervisor_generation, expected.supervisor_generation)
  assert.equal(result.dispatch_id, expected.dispatch_id)
  assert.equal(result.expected_revision, expected.expected_revision)
  assert.equal(result.service_id, expected.service_id)
  assert.equal(result.probe_id, expected.probe_id)
  assert.equal(result.graph_sha256, expected.graph_sha256)
  assert.equal(result.binding_sha256, expected.binding_sha256)
  assert.equal(result.descriptor_sha256, expected.descriptor_sha256)
  assert.equal(result.config_sha256, expected.config_sha256)
  assert.equal(Object.isFrozen(result), true)
}

test('generic HTTP JSON and text probes bind reachable results', async () => {
  for (const [serviceId, response, semanticClass] of [
    ['home_assistant_bridge', jsonResponse({ ok: true, status: 'ok' }), 'reachable'],
    ['aituber_kit', textResponse('<html>ready</html>'), 'reachable'],
    ['voicevox', textResponse('0.25.1'), 'external_ready']
  ]) {
    const requests = []
    const expected = expectedFor(serviceId)
    const executor = makeExecutor({
      fetchImpl: async (url, options) => {
        requests.push({ url, options })
        return response.clone()
      }
    })
    const result = await executor.execute(expected)
    assertBoundResult(result, expected, semanticClass)
    assert.equal(result.ready, true)
    assert.equal(requests.length, 1)
    assert.equal(requests[0].options.method, 'GET')
    assert.equal(requests[0].options.redirect, 'error')
    if (serviceId === 'voicevox') assert.deepEqual(requests[0].options.headers, {})
  }
})

test('composite HTTP probe applies descriptor checks without a service switch', async () => {
  const expected = expectedFor('environment_state_server')
  const sentinel = 'PRIVATE_ENVIRONMENT_CURRENT_SENTINEL'
  const requests = []
  const bodies = {
    '/health': { ok: true, status: 'ok' },
    '/ready': {
      ok: true,
      ready: true,
      status: 'ready',
      camera_requirement: {
        requirement_id: 'camera_hub',
        profile_id: ENVIRONMENT_READY_IDENTITY.profile_id,
        effective_config_sha256: ENVIRONMENT_READY_IDENTITY.effective_config_sha256,
        policy: ENVIRONMENT_READY_IDENTITY.camera_policy,
        requirement: 'not_required',
        result: 'camera_excluded_by_profile'
      }
    },
    '/environment/current': {
      schema_version: 1,
      snapshot_id: 'environment-snapshot-0001',
      sequence: 17,
      stale: false,
      age_ms: 100,
      observed_at: new Date(BASE_MS + 100).toISOString(),
      capabilities: {},
      actions: [],
      sources: { home: { configured: true, available: true, stale: false, private_detail: sentinel } }
    }
  }
  const executor = makeExecutor({
    fetchImpl: async (url) => {
      requests.push(new URL(url).pathname)
      return jsonResponse(bodies[new URL(url).pathname])
    }
  })
  const result = await executor.execute(expected)
  assertBoundResult(result, expected, 'ready')
  assert.equal(result.ready, true)
  assert.equal(result.reason_class, 'none')
  assert.equal(result.freshness_class, 'fresh')
  assert.equal(result.source_observed_at, new Date(BASE_MS + 100).toISOString())
  assert.deepEqual(requests, ['/health', '/ready', '/environment/current'])
  assert.equal(JSON.stringify(result).includes(sentinel), false)
  assert.equal(bodies['/ready'].camera_requirement.result, 'camera_excluded_by_profile')
  assert.equal(bodies['/environment/current'].sources.home.stale, false)
  assert.equal(bodies['/environment/current'].stale, false)
  assert.equal(bodies['/environment/current'].observed_at, new Date(BASE_MS + 100).toISOString())
})

test('Environment current accepts exactly integer schema version 1 and rejects every alternate shape without retry or disclosure', async () => {
  const expected = expectedFor('environment_state_server')
  const sentinel = 'PRIVATE_INVALID_ENVIRONMENT_SCHEMA_SENTINEL'
  const cases = [
    ['string', 'environment_state.v1'],
    ['integer_zero', 0],
    ['integer_two', 2],
    ['missing', undefined],
    ['null', null],
    ['object', { version: 1 }],
    ['boolean', true]
  ]
  for (const [label, schemaVersion] of cases) {
    const requests = []
    const current = {
      schema_version: schemaVersion,
      snapshot_id: 'environment-snapshot-invalid',
      sequence: 18,
      stale: false,
      age_ms: 100,
      observed_at: new Date(BASE_MS + 100).toISOString(),
      capabilities: {},
      actions: [],
      sources: { home: { configured: true, available: true, stale: false, private_detail: sentinel } }
    }
    if (schemaVersion === undefined) delete current.schema_version
    const bodies = {
      '/health': { ok: true, status: 'ok' },
      '/ready': {
        ok: true,
        ready: true,
        status: 'ready',
        camera_requirement: {
          requirement_id: 'camera_hub',
          profile_id: ENVIRONMENT_READY_IDENTITY.profile_id,
          effective_config_sha256: ENVIRONMENT_READY_IDENTITY.effective_config_sha256,
          policy: ENVIRONMENT_READY_IDENTITY.camera_policy,
          requirement: 'not_required',
          result: 'camera_excluded_by_profile'
        }
      },
      '/environment/current': current
    }
    const result = await makeExecutor({
      secret: sentinel,
      fetchImpl: async (url) => {
        requests.push(new URL(url).pathname)
        return jsonResponse(bodies[new URL(url).pathname])
      }
    }).execute(expected)
    assertBoundResult(result, expected, 'not_ready')
    assert.equal(result.ready, false, label)
    assert.equal(result.reason_class, 'environment_current_invalid', label)
    assert.equal(result.freshness_class, 'fresh', label)
    assert.deepEqual(requests, ['/health', '/ready', '/environment/current'], label)
    assert.equal(new Set(requests).size, 3, label)
    assert.equal(JSON.stringify(result).includes(sentinel), false, label)
    assert.equal(JSON.stringify(result).includes('environment_state.v1'), false, label)
    assert.equal(current.sources.home.stale, false, label)
    assert.equal(current.stale, false, label)
    assert.equal(current.observed_at, new Date(BASE_MS + 100).toISOString(), label)
    assert.equal(bodies['/ready'].camera_requirement.result, 'camera_excluded_by_profile', label)
  }
})

test('Environment readiness fails closed when the operation identity is absent or mismatched', async () => {
  const expected = expectedFor('environment_state_server')
  const current = {
    schema_version: 1,
    stale: false,
    observed_at: new Date(BASE_MS + 100).toISOString(),
    sources: { home: { configured: true, available: true, stale: false } }
  }
  const cases = [
    ['missing', undefined],
    ['profile', {
      requirement_id: 'camera_hub',
      profile_id: 'other-profile',
      effective_config_sha256: ENVIRONMENT_READY_IDENTITY.effective_config_sha256,
      policy: ENVIRONMENT_READY_IDENTITY.camera_policy,
      requirement: 'not_required',
      result: 'camera_excluded_by_profile'
    }],
    ['hash', {
      requirement_id: 'camera_hub',
      profile_id: ENVIRONMENT_READY_IDENTITY.profile_id,
      effective_config_sha256: 'f'.repeat(64),
      policy: ENVIRONMENT_READY_IDENTITY.camera_policy,
      requirement: 'not_required',
      result: 'camera_excluded_by_profile'
    }],
    ['policy', {
      requirement_id: 'camera_hub',
      profile_id: ENVIRONMENT_READY_IDENTITY.profile_id,
      effective_config_sha256: ENVIRONMENT_READY_IDENTITY.effective_config_sha256,
      policy: 'required',
      requirement: 'required',
      result: 'ready'
    }]
  ]
  for (const [label, cameraRequirement] of cases) {
    const bodies = {
      '/health': { ok: true, status: 'ok' },
      '/ready': {
        ok: true,
        ready: true,
        status: 'ready',
        ...(cameraRequirement ? { camera_requirement: cameraRequirement } : {})
      },
      '/environment/current': current
    }
    const result = await makeExecutor({
      fetchImpl: async (url) => jsonResponse(bodies[new URL(url).pathname])
    }).execute(expected)
    assertBoundResult(result, expected, 'not_ready')
    assert.equal(result.ready, false, label)
    assert.equal(result.reason_class, 'environment_not_ready', label)
  }
})

test('HTTP timeout, non-2xx, malformed, oversized and stale outcomes fail closed', async () => {
  const cases = [
    [async () => { throw Object.assign(new Error('private timeout'), { name: 'AbortError' }) }, 'deadline'],
    [async () => jsonResponse({ ok: false, status: 'unavailable' }, 503), 'health_unavailable'],
    [async () => textResponse('{not-json'), 'response_invalid'],
    [async () => textResponse('x'.repeat(MAX_BODY_BYTES + 1)), 'response_invalid']
  ]
  for (const [fetchImpl, reason] of cases) {
    const expected = expectedFor()
    const result = await makeExecutor({ fetchImpl }).execute(expected)
    assertBoundResult(result, expected, 'not_ready')
    assert.equal(result.ready, false)
    assert.equal(result.reason_class, reason)
  }

  const expected = expectedFor('thought_core_watcher')
  const stale = await makeExecutor({
    fetchImpl: async () => { throw new Error('unused') },
    observers: {
      module_status: async () => ({
        ok: true,
        state: 'running',
        operation_id: expected.operation_id,
        timestamp: new Date(BASE_MS - 20000).toISOString()
      })
    }
  }).execute(expected)
  assertBoundResult(stale, expected, 'not_ready')
  assert.equal(stale.reason_class, 'source_stale')
})

test('external probe is read-only and rejects private headers before fetch', async () => {
  const expected = expectedFor('voicevox')
  let fetchCalls = 0
  const executor = new LauncherProbeExecutor({
    probeAuthority,
    environmentReadyIdentity: ENVIRONMENT_READY_IDENTITY,
    privateTargetResolver: async (target) => ({
      url: `http://127.0.0.1:50021${target.path}`,
      headers: { Authorization: 'PRIVATE_SENTINEL' }
    }),
    fetchImpl: async () => {
      fetchCalls += 1
      return textResponse('0.25.1')
    },
    clock: () => BASE_MS + 200,
    monotonicClock: () => 10
  })
  await assert.rejects(() => executor.execute(expected), (error) =>
    error instanceof LauncherProbeExecutorError && error.code === 'probe_executor_target_resolution_invalid')
  assert.equal(fetchCalls, 0)
})

test('injected WebSocket, module status and private status observers bind safe results', async () => {
  const watcherExpected = expectedFor('thought_core_watcher')
  let moduleRequest
  const moduleExecutor = makeExecutor({
    fetchImpl: async () => { throw new Error('unused') },
    observers: {
      module_status: async (request) => {
        moduleRequest = request
        return {
          ok: true,
          state: 'running',
          operation_id: request.operation_id,
          timestamp: new Date(BASE_MS + 100).toISOString()
        }
      }
    }
  })
  const watcher = await moduleExecutor.execute(watcherExpected)
  assertBoundResult(watcher, watcherExpected, 'ready')
  assert.equal(moduleRequest.requested_at, watcherExpected.requested_at)

  const visionExpected = expectedFor('vision_snapshot_processor')
  const websocketExecutor = makeExecutor({
    fetchImpl: async () => { throw new Error('unused') },
    observers: { websocket: async () => ({ ok: true, source_observed_at: new Date(BASE_MS + 100).toISOString() }) }
  })
  const vision = await websocketExecutor.execute(visionExpected)
  assertBoundResult(vision, visionExpected, 'reachable')

  const cameraExpected = expectedFor('mediapipe_camera_hub_stack')
  const cameraExecutor = makeExecutor({
    fetchImpl: async () => { throw new Error('unused') },
    observers: {
      websocket: async () => ({ ok: true }),
      private_status: async () => ({
        ownership_matched: true,
        registry_matched: true,
        manifest_valid: true,
        lineage_matched: true,
        listener_matched: true,
        operational_class: 'degraded',
        source_observed_at: new Date(BASE_MS + 100).toISOString()
      })
    }
  })
  const camera = await cameraExecutor.execute(cameraExpected)
  assertBoundResult(camera, cameraExpected, 'degraded_operational')
  assert.equal(camera.reason_class, 'optional_degraded')
})

test('missing or malformed injected observer fails with a fixed executor class', async () => {
  const expected = expectedFor('vision_snapshot_processor')
  for (const observers of [{}, { websocket: async () => null }]) {
    const executor = makeExecutor({
      fetchImpl: async () => { throw new Error('unused') },
      observers
    })
    await assert.rejects(() => executor.execute(expected), (error) =>
      error instanceof LauncherProbeExecutorError && [
        'probe_executor_observer_missing', 'probe_executor_observer_result_invalid'
      ].includes(error.code))
  }
})

test('identity drift is rejected before resolving a target', async () => {
  let resolverCalls = 0
  const expected = expectedFor('home_assistant_bridge', { dispatch_id: 'ld_executor0000000002' })
  const executor = new LauncherProbeExecutor({
    probeAuthority,
    environmentReadyIdentity: ENVIRONMENT_READY_IDENTITY,
    privateTargetResolver: async () => {
      resolverCalls += 1
      return { url: 'http://127.0.0.1:8787/health', headers: {} }
    },
    fetchImpl: async () => jsonResponse({ ok: true, status: 'ok' }),
    clock: () => BASE_MS + 200,
    monotonicClock: () => 10
  })
  const drifted = { ...expected, descriptor_sha256: 'd'.repeat(64) }
  await assert.rejects(() => executor.execute(drifted), (error) =>
    error instanceof LauncherProbeContractError && error.code === 'probe_expected_descriptor_mismatch')
  assert.equal(resolverCalls, 0)
})

test('raw response, URL, headers and private observer values never escape the bound result', async () => {
  const expected = expectedFor('environment_state_server')
  const sentinel = 'PRIVATE_SENTINEL_7c7144'
  const bodies = {
    '/health': { ok: true, status: 'ok', raw: sentinel },
    '/ready': { ok: true, ready: true, status: 'ready', reason: sentinel },
    '/environment/current': {
      schema_version: 1,
      stale: false,
      observed_at: new Date(BASE_MS + 100).toISOString(),
      sources: {},
      secret: sentinel
    }
  }
  let privateHeaderObserved = false
  const result = await makeExecutor({
    secret: sentinel,
    fetchImpl: async (url, options) => {
      if (new URL(url).pathname === '/environment/current') {
        privateHeaderObserved = options.headers.Authorization === `Bearer ${sentinel}`
      }
      return jsonResponse(bodies[new URL(url).pathname])
    }
  }).execute(expected)
  assert.equal(privateHeaderObserved, true)
  const serialized = JSON.stringify(result)
  assert.equal(serialized.includes(sentinel), false)
  assert.equal(serialized.includes('127.0.0.1'), false)
  assert.equal(serialized.includes('/environment/current'), false)
  assert.deepEqual(Object.keys(result).sort(), [
    'schema_version', 'message_type', 'operation_id', 'supervisor_generation', 'dispatch_id',
    'expected_revision', 'service_id', 'probe_id', 'graph_sha256', 'binding_sha256',
    'descriptor_sha256', 'config_sha256', 'requested_at', 'observed_at', 'source_observed_at',
    'freshness_class', 'semantic_class', 'reason_class', 'ready', 'proof_ceiling'
  ].sort())
})

test('executor dispatch is descriptor-driven and contains no service-specific branch', () => {
  const source = fs.readFileSync(path.join(ROOT, 'tools', 'home-control-launcher', 'launcher-probe-executor.js'), 'utf8')
  for (const serviceId of probeAuthority.serviceIds) assert.equal(source.includes(serviceId), false, serviceId)
  assert.match(source, /CLASSIFIERS\[descriptor\.probe_class\]/u)
  assert.match(source, /TRANSPORT_HANDLERS\[target\.transport\]/u)
  assert.doesNotMatch(source, /console\.(?:log|error|warn)/u)
})
