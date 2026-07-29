'use strict'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const launcher = require('../tools/home-control-launcher/launcher-supervisor-contract')
const probes = require('../tools/home-control-launcher/launcher-probe-result-binding')

const launcherAuthority = launcher.loadAuthority(ROOT)
const probeAuthority = probes.loadProbeAuthority(ROOT, launcherAuthority)
const OPERATION_ID = 'lop_probe0001'
const DISPATCH_ID = 'ld_0000000000000001'
const CONFIG_SHA256 = 'c'.repeat(64)
const BASE_TIME = '2026-07-29T00:00:00.000Z'

const descriptorFor = (serviceId) => probeAuthority.descriptors.find((item) => item.service_id === serviceId)
const expectedFor = (serviceId = 'home_assistant_bridge') => {
  const descriptor = descriptorFor(serviceId)
  return {
    operation_id: OPERATION_ID,
    supervisor_generation: 3,
    dispatch_id: DISPATCH_ID,
    expected_revision: 17,
    service_id: descriptor.service_id,
    probe_id: descriptor.probe_id,
    graph_sha256: probeAuthority.identities.graphSha256,
    binding_sha256: probeAuthority.identities.bindingSha256,
    descriptor_sha256: descriptor.descriptor_sha256,
    config_sha256: CONFIG_SHA256,
    requested_at: BASE_TIME
  }
}
const observationFor = (expected, values = {}) => ({
  schema_version: 'launcher_probe_observation.v1',
  message_type: 'observation',
  ...expected,
  observed_at: '2026-07-29T00:00:00.200Z',
  source_observed_at: '2026-07-29T00:00:00.100Z',
  semantic_class: 'reachable',
  reason_class: 'none',
  ...values
})
const expectCode = (action, code) => assert.throws(action, (error) =>
  error instanceof probes.LauncherProbeContractError && error.code === code)

test('probe authority is graph/binding-bound, one-to-one, and recursively immutable', () => {
  assert.equal(probeAuthority.descriptors.length, launcherAuthority.graph.services.length)
  assert.equal(probeAuthority.identities.graphSha256, launcherAuthority.identities.graphSha256)
  assert.equal(probeAuthority.identities.bindingSha256, launcherAuthority.identities.bindingSha256)
  assert.equal(probeAuthority.identities.schemaSha256, launcherAuthority.identities.probeSchemaSha256)
  assert.equal(probeAuthority.identities.documentSha256, launcherAuthority.identities.probeDocumentSha256)
  const expectedPairs = launcherAuthority.graph.services.map((service) =>
    `${service.service_id}:${service.readiness.probe_id}`).sort()
  assert.deepEqual(Object.keys(probeAuthority.byPair).sort(), expectedPairs)

  const walk = (value) => {
    if (!value || typeof value !== 'object') return
    assert.equal(Object.isFrozen(value), true)
    for (const child of Object.values(value)) walk(child)
  }
  walk(probeAuthority)
  assert.throws(() => { probeAuthority.descriptors[0].probe_id = 'mutated' }, TypeError)
})

test('accepted meanings have explicit proof ceilings and complete service coverage', () => {
  const meanings = {
    home_assistant_bridge: ['reachable', 'home_bridge_reachability_only', ['http_2xx', 'bounded_json', 'service_identity']],
    environment_state_server: ['ready', 'environment_semantic_readiness_no_world_truth', [
      'http_2xx', 'bounded_json', 'service_identity', 'environment_ready_contract',
      'environment_current_schema', 'configured_source_policy', 'world_freshness_advisory'
    ]],
    openai_provider_broker: ['reachable', 'broker_reachability_only', ['http_2xx', 'bounded_json', 'service_identity']],
    thought_core_api: ['reachable', 'thought_core_reachability_only', ['http_2xx', 'bounded_json', 'service_identity']],
    mediapipe_camera_hub_stack: ['ready', 'camera_operational_no_media', [
      'worker_owned_identity', 'registry_identity', 'manifest_shape', 'manifest_lineage',
      'listener_lineage', 'websocket_handshake', 'camera_operational_class'
    ]],
    vision_snapshot_processor: ['reachable', 'vision_input_reachability_no_world_truth', [
      'worker_owned_identity', 'listener_lineage', 'websocket_handshake'
    ]],
    aituber_kit: ['reachable', 'aituber_http_reachability_only', ['http_2xx', 'bounded_text']],
    thought_core_watcher: ['ready', 'watcher_current_operation_liveness_only', [
      'worker_owned_identity', 'module_status_shape', 'module_status_current_operation', 'module_status_freshness'
    ]],
    touchdesigner_control_gui: ['reachable', 'display_gui_reachability_only', ['http_2xx', 'bounded_text']],
    voicevox: ['external_ready', 'voicevox_version_reachability_only', [
      'http_2xx', 'bounded_text', 'no_touch_external'
    ]]
  }
  assert.deepEqual(Object.keys(meanings).sort(), probeAuthority.serviceIds.slice().sort())
  for (const [serviceId, [semanticClass, proofCeiling, checks]] of Object.entries(meanings)) {
    const descriptor = descriptorFor(serviceId)
    assert.equal(descriptor.success_semantic_classes[0], semanticClass)
    assert.equal(descriptor.proof_ceiling, proofCeiling)
    assert.deepEqual(descriptor.checks, checks)
    assert.equal(descriptor.world_freshness_startup_authoritative, false)
  }
  assert.equal(descriptorFor('voicevox').no_touch, true)
  assert.equal(descriptorFor('mediapipe_camera_hub_stack').success_semantic_classes.includes('degraded_operational'), true)
  assert.equal(descriptorFor('vision_snapshot_processor').success_semantic_classes.includes('degraded_operational'), true)
})

test('each service binds its accepted observation to a bounded fresh public result', () => {
  for (const descriptor of probeAuthority.descriptors) {
    const expected = expectedFor(descriptor.service_id)
    const observation = observationFor(expected, { semantic_class: descriptor.success_semantic_classes[0] })
    const result = probes.bindProbeResult(probeAuthority, expected, observation, {
      now: '2026-07-29T00:00:00.300Z'
    })
    assert.equal(result.ready, true, descriptor.service_id)
    assert.equal(result.freshness_class, 'fresh', descriptor.service_id)
    assert.equal(result.proof_ceiling, descriptor.proof_ceiling, descriptor.service_id)
    assert.equal(Object.isFrozen(result), true)
    assert.deepEqual(Object.keys(result).sort(), [
      'schema_version', 'message_type', 'operation_id', 'supervisor_generation', 'dispatch_id',
      'expected_revision', 'service_id', 'probe_id', 'graph_sha256', 'binding_sha256',
      'descriptor_sha256', 'config_sha256', 'requested_at', 'observed_at', 'source_observed_at',
      'freshness_class', 'semantic_class', 'reason_class', 'ready', 'proof_ceiling'
    ].sort())
  }
})

test('optional degraded and bounded not-ready meanings bind without overclaiming readiness', () => {
  for (const serviceId of ['mediapipe_camera_hub_stack', 'vision_snapshot_processor']) {
    const expected = expectedFor(serviceId)
    const degraded = probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
      semantic_class: 'degraded_operational', reason_class: 'optional_degraded'
    }), { now: '2026-07-29T00:00:00.300Z' })
    assert.equal(degraded.ready, true)
    assert.equal(degraded.reason_class, 'optional_degraded')
  }
  const expected = expectedFor()
  const notReady = probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    semantic_class: 'not_ready', reason_class: 'health_unavailable'
  }), { now: '2026-07-29T00:00:00.300Z' })
  assert.equal(notReady.ready, false)
  assert.equal(notReady.proof_ceiling, 'home_bridge_reachability_only')
})

test('every operation and authority identity field is bound one-to-one', () => {
  const expected = expectedFor()
  const observation = observationFor(expected)
  const replacements = {
    operation_id: 'lop_probe0002',
    supervisor_generation: 4,
    dispatch_id: 'ld_0000000000000002',
    expected_revision: 18,
    service_id: 'thought_core_api',
    probe_id: 'thought_core_health',
    graph_sha256: '1'.repeat(64),
    binding_sha256: '2'.repeat(64),
    descriptor_sha256: '3'.repeat(64),
    config_sha256: '4'.repeat(64),
    requested_at: '2026-07-29T00:00:00.001Z'
  }
  for (const [field, replacement] of Object.entries(replacements)) {
    expectCode(() => probes.bindProbeResult(probeAuthority, { ...expected, [field]: replacement }, observation, {
      now: '2026-07-29T00:00:00.300Z'
    }), 'probe_observation_identity_mismatch')
  }

  for (const [field, replacement] of [
    ['graph_sha256', '1'.repeat(64)],
    ['binding_sha256', '2'.repeat(64)]
  ]) {
    const drifted = { ...expected, [field]: replacement }
    expectCode(() => probes.bindProbeResult(probeAuthority, drifted, observationFor(drifted), {
      now: '2026-07-29T00:00:00.300Z'
    }), 'probe_expected_authority_drift')
  }
  const descriptorDrift = { ...expected, descriptor_sha256: '3'.repeat(64) }
  expectCode(() => probes.bindProbeResult(probeAuthority, descriptorDrift, observationFor(descriptorDrift), {
    now: '2026-07-29T00:00:00.300Z'
  }), 'probe_expected_descriptor_mismatch')
})

test('time ordering, request deadline, and source freshness fail closed', () => {
  const expected = expectedFor()
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    source_observed_at: '2026-07-28T23:59:59.999Z'
  }), { now: '2026-07-29T00:00:00.300Z' }), 'probe_observation_time_order_invalid')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    observed_at: '2026-07-29T00:00:02.000Z', source_observed_at: '2026-07-29T00:00:01.900Z'
  }), { now: '2026-07-29T00:00:00.300Z' }), 'probe_observation_time_order_invalid')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    observed_at: '2026-07-29T00:00:11.000Z', source_observed_at: '2026-07-29T00:00:10.900Z'
  }), { now: '2026-07-29T00:00:11.000Z' }), 'probe_observation_deadline_exceeded')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected), {
    now: '2026-07-29T00:00:15.101Z'
  }), 'probe_observation_stale')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    observed_at: '2026-07-29T00:00:00Z'
  }), { now: '2026-07-29T00:00:00.300Z' }), 'probe_observed_at_invalid')
})

test('Environment observed snapshots may predate the request only within their dedicated freshness contract', () => {
  const expected = expectedFor('environment_state_server')
  const fresh = probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    semantic_class: 'ready',
    source_observed_at: '2026-07-28T23:59:59.900Z'
  }), { now: '2026-07-29T00:00:00.200Z' })
  assert.equal(fresh.ready, true)
  assert.equal(fresh.freshness_class, 'fresh')
  assert.equal(fresh.source_observed_at, '2026-07-28T23:59:59.900Z')

  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    semantic_class: 'ready',
    source_observed_at: '2026-07-28T23:59:30.199Z'
  }), { now: '2026-07-29T00:00:00.200Z' }), 'probe_observation_stale')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    semantic_class: 'ready',
    source_observed_at: '2026-07-29T00:00:00.201Z'
  }), { now: '2026-07-29T00:00:00.200Z' }), 'probe_observation_time_order_invalid')
  expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, {
    semantic_class: 'ready',
    source_observed_at: '2026-07-28T23:59:59Z'
  }), { now: '2026-07-29T00:00:00.200Z' }), 'probe_source_observed_at_invalid')

  const ordinaryExpected = expectedFor('home_assistant_bridge')
  expectCode(() => probes.bindProbeResult(
    probeAuthority,
    ordinaryExpected,
    observationFor(ordinaryExpected, {
      source_observed_at: '2026-07-28T23:59:59.999Z'
    }),
    { now: '2026-07-29T00:00:00.200Z' }
  ), 'probe_observation_time_order_invalid')
})

test('unsafe raw, private, command, camera, and lease fields are rejected instead of copied', () => {
  const expected = expectedFor()
  for (const field of [
    'raw', 'private_status', 'body', 'path', 'command', 'camera_values', 'authority_lease_proof'
  ]) {
    expectCode(() => probes.bindProbeResult(probeAuthority, expected, {
      ...observationFor(expected), [field]: 'PRIVATE_SENTINEL'
    }, { now: '2026-07-29T00:00:00.300Z' }), 'probe_observation_shape_invalid')
  }
  const result = probes.bindProbeResult(probeAuthority, expected, observationFor(expected), {
    now: '2026-07-29T00:00:00.300Z'
  })
  assert.equal(JSON.stringify(result).includes('PRIVATE_SENTINEL'), false)
})

test('semantic and reason classes cannot exceed the descriptor meaning', () => {
  const expected = expectedFor()
  for (const [values, code] of [
    [{ semantic_class: 'ready' }, 'probe_observation_semantic_mismatch'],
    [{ semantic_class: 'reachable', reason_class: 'health_unavailable' }, 'probe_observation_reason_mismatch'],
    [{ semantic_class: 'not_ready', reason_class: 'none' }, 'probe_observation_reason_mismatch'],
    [{ semantic_class: 'not_ready', reason_class: 'optional_degraded' }, 'probe_observation_reason_mismatch'],
    [{ reason_class: 'PRIVATE_SENTINEL' }, 'probe_observation_reason_invalid']
  ]) {
    expectCode(() => probes.bindProbeResult(probeAuthority, expected, observationFor(expected, values), {
      now: '2026-07-29T00:00:00.300Z'
    }), code)
  }
  const cameraExpected = expectedFor('mediapipe_camera_hub_stack')
  expectCode(() => probes.bindProbeResult(probeAuthority, cameraExpected, observationFor(cameraExpected, {
    semantic_class: 'degraded_operational', reason_class: 'none'
  }), { now: '2026-07-29T00:00:00.300Z' }), 'probe_observation_degraded_mismatch')
})

test('schema enums and manifest coverage/identity mutations are rejected', () => {
  const schema = structuredClone(probeAuthority.schema)
  schema.$defs.reason_class.enum.push('private_reason')
  expectCode(() => probes.validateSchemaAuthority(schema), 'probe_schema_enums_invalid')

  const networkPathSchema = structuredClone(probeAuthority.schema)
  networkPathSchema.$defs.target.properties.path.oneOf[0].pattern = '^/[^\\u0000\\r\\n]{0,255}$'
  expectCode(() => probes.validateSchemaAuthority(networkPathSchema), 'probe_schema_shape_invalid')

  const missing = structuredClone(probeAuthority.document)
  missing.descriptors.pop()
  expectCode(() => probes.validateProbeDocument(missing, launcherAuthority), 'probe_document_coverage_invalid')

  const duplicate = structuredClone(probeAuthority.document)
  duplicate.descriptors[1] = structuredClone(duplicate.descriptors[0])
  expectCode(() => probes.validateProbeDocument(duplicate, launcherAuthority), 'probe_descriptor_duplicate')

  const drifted = structuredClone(probeAuthority.document)
  drifted.graph_sha256 = '1'.repeat(64)
  expectCode(() => probes.validateProbeDocument(drifted, launcherAuthority), 'probe_document_authority_drift')

  const weakened = structuredClone(probeAuthority.document)
  weakened.descriptors.find((item) => item.service_id === 'mediapipe_camera_hub_stack').checks = ['websocket_handshake']
  expectCode(() => probes.validateProbeDocument(weakened, launcherAuthority), 'probe_document_authority_drift')

  const transportMismatch = structuredClone(probeAuthority.document)
  const homeTarget = transportMismatch.descriptors.find((item) => item.service_id === 'home_assistant_bridge').targets[0]
  Object.assign(homeTarget, { transport: 'websocket', method: 'CONNECT', response_class: 'websocket_handshake' })
  expectCode(() => probes.validateProbeDocument(transportMismatch, launcherAuthority), 'probe_descriptor_transport_mismatch')

  const networkPath = structuredClone(probeAuthority.document)
  networkPath.descriptors.find((item) => item.service_id === 'home_assistant_bridge').targets[0].path = '//foreign.example/health'
  expectCode(() => probes.validateProbeDocument(networkPath, launcherAuthority), 'probe_http_target_invalid')

  const externalAuth = structuredClone(probeAuthority.document)
  externalAuth.descriptors.find((item) => item.service_id === 'voicevox').targets[0].auth_class = 'private_secret_ref'
  expectCode(() => probes.validateProbeDocument(externalAuth, launcherAuthority), 'probe_descriptor_external_target_invalid')
})

test('unvalidated lookalike authority is rejected', () => {
  const expected = expectedFor()
  expectCode(() => probes.bindProbeResult(Object.freeze({
    byPair: probeAuthority.byPair, identities: probeAuthority.identities
  }), expected, observationFor(expected), { now: '2026-07-29T00:00:00.300Z' }), 'probe_authority_not_validated')
})
