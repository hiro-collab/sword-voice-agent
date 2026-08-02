'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const contract = require('../tools/home-control-launcher/launcher-supervisor-contract')
const rawReducer = require('../tools/home-control-launcher/launcher-supervisor-reducer')
const rawStore = require('../tools/home-control-launcher/launcher-operation-store')

const authority = contract.loadAuthority(ROOT)
const REDUCED_PROFILE_ID = 'core-rehearsal-text-bubble-v0'
const REDUCED_PARENT_PROFILE_SHA256 = '1F8182AD80BA150D696869895D699121397EE5D088A3CCFACE3B5027B7829836'
const CONFIG_IDENTITY = Object.freeze({
  profile_id: authority.graph.profile_id,
  effective_config_sha256: 'e'.repeat(64),
  camera_policy: 'camera_excluded_by_profile'
})
const PLAN_IDENTITY = Object.freeze({
  private_plan_sha256: '1'.repeat(64),
  worker_executable_class: 'powershell_7_program_files',
  worker_executable_sha256: '2'.repeat(64)
})
const PROBE_CONFIG_SHA256 = '0'.repeat(64)
const reducer = {
  ...rawReducer,
  createOperation: (operationId, suppliedAuthority, generation = 1) =>
    rawReducer.createOperation(operationId, suppliedAuthority, CONFIG_IDENTITY, PLAN_IDENTITY, PROBE_CONFIG_SHA256, generation),
  startOperation: (active, operationId, suppliedAuthority, generation = 1) =>
    rawReducer.startOperation(active, operationId, suppliedAuthority, CONFIG_IDENTITY, PLAN_IDENTITY, PROBE_CONFIG_SHA256, generation)
}
const store = {
  ...rawStore,
  startAndPersist: (operationId, suppliedAuthority, root, observer) =>
    rawStore.startAndPersist(operationId, CONFIG_IDENTITY, PLAN_IDENTITY, PROBE_CONFIG_SHA256, suppliedAuthority, root, observer)
}
const OPERATION_ID = 'lop_node0001'
const LEASE_PROOF = `lp_${'a'.repeat(64)}`
const dispatchId = (serviceId, action, sequence = 1) => `ld_${Buffer.from(`${serviceId}:${action}:${sequence}`).toString('hex').slice(0, 32).padEnd(32, '0')}`
const actionForEvent = (eventType) => eventType.startsWith('spawn_') ? 'start' :
  ['probe_requested', 'probe_transport_ready', 'semantic_probe_completed', 'optional_absent', 'readiness_timeout'].includes(eventType) ? 'probe' :
    ['stop_dispatch_requested', 'service_stopped', 'stop_failed'].includes(eventType) ? 'stop' : null
const event = (eventType, serviceId = undefined, operationId = OPERATION_ID, dispatch = undefined) => ({
  event_type: eventType, operation_id: operationId, ...(serviceId ? { service_id: serviceId } : {}),
  ...(serviceId && actionForEvent(eventType) ? { dispatch_id: dispatch || dispatchId(serviceId, actionForEvent(eventType)), ...(eventType.endsWith('_requested') ? { action: actionForEvent(eventType) } : {}) } : {})
})
const workerRequest = (operation, serviceId = 'home_assistant_bridge', action = 'start', adapterClass = 'job_worker_service', deadlineMs = 60000) => ({
  schema_version: 'launcher_worker.v2', message_type: 'request', operation_id: operation.operation_id,
  supervisor_generation: operation.supervisor_generation || 1,
  authority_lease_proof: LEASE_PROOF,
  dispatch_id: operation.services?.find((service) => service.service_id === serviceId)?.pending_dispatch_id || dispatchId(serviceId, action),
  graph_sha256: authority.identities.graphSha256, binding_sha256: authority.identities.bindingSha256,
  service_id: serviceId, action, adapter_class: adapterClass, expected_revision: operation.revision,
  deadline_ms: deadlineMs, worker_nonce: 'lw_0000000000000001'
})
const workerResult = (request, values = {}) => ({
  schema_version: 'launcher_worker.v2', message_type: 'result', operation_id: request.operation_id,
  supervisor_generation: request.supervisor_generation, dispatch_id: request.dispatch_id,
  authority_lease_proof: request.authority_lease_proof,
  service_id: request.service_id, action: request.action, expected_revision: request.expected_revision,
  worker_nonce: request.worker_nonce, result_class: 'accepted', ownership_class: 'matched',
  listener_class: 'not_applicable', descendant_class: 'owned_active',
  termination_class: request.action === 'stop' ? 'forced_only' : 'not_applicable',
  job_query_class: request.action === 'stop' ? 'trusted' : 'not_applicable',
  active_count_after: request.action === 'stop' ? 0 : null,
  post_stop_listener_class: 'not_applicable',
  ...values
})
const serviceCleanupAttempt = (operation, serviceId, values = {}) => ({
  sequence: (operation.cleanup_attempts?.length || 0) + 1,
  target_class: 'service',
  responsible_id: serviceId,
  outcome_class: 'clear',
  reason_class: 'none',
  termination_class: 'forced_only',
  job_query_class: 'trusted',
  active_count_after: 0,
  post_stop_listener_class: 'not_applicable',
  ...values
})
const cleanupEvent = (operation, eventType, serviceId, operationId = OPERATION_ID, dispatch = undefined) => ({
  ...event(eventType, serviceId, operationId, dispatch),
  cleanup_attempt: serviceCleanupAttempt(operation, serviceId, eventType === 'service_stopped'
    ? {}
    : {
        outcome_class: 'failed',
        reason_class: eventType === 'rollback_failed' ? 'rollback_failed' : 'stop_failed',
        termination_class: 'unknown',
        job_query_class: 'unknown',
        active_count_after: null,
        post_stop_listener_class: 'unknown'
      })
})
const privatePlanCleanupEvent = (operation, eventType = 'private_plan_cleanup_completed') => ({
  ...event(eventType),
  cleanup_attempt: {
    sequence: (operation.cleanup_attempts?.length || 0) + 1,
    target_class: 'private_plan',
    responsible_id: 'launcher_supervisor',
    outcome_class: eventType === 'private_plan_cleanup_completed'
      ? 'clear'
      : eventType === 'private_plan_cleanup_failed' ? 'failed' : 'unattempted',
    reason_class: eventType === 'private_plan_cleanup_completed'
      ? 'none'
      : eventType === 'private_plan_cleanup_failed' ? 'private_plan_cleanup_failed' : 'unattempted_transport_unavailable',
    termination_class: 'not_applicable',
    job_query_class: 'not_applicable',
    active_count_after: null,
    post_stop_listener_class: 'not_applicable'
  }
})
const staleLockRecord = (ownerPid, ownerNonce = 'll_00000000000000000000000000000000') => ({
  schema_version: 'launcher_operation_lock.v1', owner_nonce: ownerNonce, owner_pid: ownerPid,
  created_at_ms: Date.now() - store.LOCK_STALE_MS - 1000
})
const abandonedSupervisorLeaseRecord = (ownerPid, generation = 1, ownerNonce = `sl_${'0'.repeat(64)}`) => ({
  schema_version: 'launcher_supervisor_lease.v2', operation_id: OPERATION_ID,
  supervisor_generation: generation, graph_sha256: authority.identities.graphSha256,
  binding_sha256: authority.identities.bindingSha256, owner_nonce: ownerNonce,
  owner_pid: ownerPid, created_at_ms: Date.now() - 1000
})

const expectCode = (action, code) => assert.throws(action, (error) => error instanceof contract.LauncherContractError && error.code === code)

const withRuntimeRoot = (action) => {
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'sword-launcher-node-n0-'))
  fs.writeFileSync(path.join(runtimeRoot, 'parent-sentinel.txt'), 'parent-unchanged', { mode: 0o600 })
  try { return action(runtimeRoot) } finally { fs.rmSync(runtimeRoot, { recursive: true, force: true }) }
}

const withReducedAuthorityFixture = (action) => withRuntimeRoot((runtimeRoot) => {
  const paths = [
    'contracts/launcher/launcher-service-graph.v1.schema.json',
    'contracts/launcher/launcher-operation.v2.schema.json',
    'contracts/launcher/launcher-worker.v2.schema.json',
    'contracts/launcher/launcher-reducer-vectors.v2.json',
    'contracts/launcher/launcher-probe-descriptor.v1.schema.json',
    'contracts/launcher/generated/launcher-service-graph.standard.v2.binding.json',
    'contracts/launcher/generated/launcher-service-graph.core-rehearsal-text-bubble.v2.binding.json',
    'ops/manifests/launcher-service-graph.standard.v1.json',
    'ops/manifests/launcher-probe-descriptors.standard.v1.json',
    'ops/manifests/launcher-service-graph.core-rehearsal-text-bubble.v1.json',
    'ops/manifests/launcher-probe-descriptors.core-rehearsal-text-bubble.v1.json',
    'ops/manifests/profiles/core-rehearsal-text-bubble-v0.json',
    'ops/manifests/profiles/thought-core-v0.json',
    'contracts/turn/ordinary-standard-route.v1.json',
    'tools/home-control-launcher/server.js',
    'ops/scripts/home-control-stack/start-home-control-stack.ps1',
    ...fs.readdirSync(path.join(ROOT, 'ops/manifests/services'))
      .filter((name) => name.endsWith('.json'))
      .map((name) => `ops/manifests/services/${name}`)
  ]
  for (const relative of paths) {
    const target = path.join(runtimeRoot, relative)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.copyFileSync(path.join(ROOT, relative), target)
  }
  return action(runtimeRoot)
})

test('operation identity is immutable, persisted before mutation, and rejects drift', () => withRuntimeRoot((runtimeRoot) => {
  expectCode(
    () => rawReducer.createOperation(OPERATION_ID, authority, null),
    'operation_config_identity_invalid'
  )
  const started = rawStore.startAndPersist(
    OPERATION_ID,
    CONFIG_IDENTITY,
    PLAN_IDENTITY,
    PROBE_CONFIG_SHA256,
    authority,
    runtimeRoot
  )
  assert.equal(started.operation.profile_id, CONFIG_IDENTITY.profile_id)
  assert.equal(
    started.operation.effective_config_sha256,
    CONFIG_IDENTITY.effective_config_sha256
  )
  assert.equal(started.operation.camera_policy, CONFIG_IDENTITY.camera_policy)
  assert.equal(started.operation.private_plan_sha256, PLAN_IDENTITY.private_plan_sha256)
  assert.equal(started.operation.worker_executable_class, PLAN_IDENTITY.worker_executable_class)
  assert.equal(started.operation.worker_executable_sha256, PLAN_IDENTITY.worker_executable_sha256)
  assert.equal(started.operation.probe_config_sha256, PROBE_CONFIG_SHA256)
  const joinedWithNewPlan = rawReducer.startOperation(
    started.operation,
    'lop_planrefresh01',
    authority,
    CONFIG_IDENTITY,
    {
      private_plan_sha256: '3'.repeat(64),
      worker_executable_class: 'windows_powershell_system32',
      worker_executable_sha256: '4'.repeat(64)
    },
    PROBE_CONFIG_SHA256,
    started.operation.supervisor_generation + 1
  )
  assert.equal(joinedWithNewPlan.joined_existing, true)
  assert.equal(joinedWithNewPlan.operation.joined_existing, false)
  assert.equal(joinedWithNewPlan.operation.revision, started.operation.revision)
  assert.equal(joinedWithNewPlan.operation.private_plan_sha256, PLAN_IDENTITY.private_plan_sha256)
  assert.equal(joinedWithNewPlan.operation.worker_executable_class, PLAN_IDENTITY.worker_executable_class)
  assert.equal(joinedWithNewPlan.operation.worker_executable_sha256, PLAN_IDENTITY.worker_executable_sha256)
  expectCode(
    () => rawReducer.validateSnapshot({ ...started.operation, private_plan_sha256: 'x'.repeat(64) }, authority),
    'operation_plan_identity_invalid'
  )
  const serialized = JSON.stringify(started.operation)
  assert.equal(serialized.includes('MediapipeCameraName'), false)
  assert.equal(serialized.includes('MediapipeCameraSelectionKey'), false)
  expectCode(
    () => rawReducer.startOperation(
      started.operation,
      'lop_configdrift01',
      authority,
      { ...CONFIG_IDENTITY, effective_config_sha256: 'f'.repeat(64) },
      PLAN_IDENTITY,
      PROBE_CONFIG_SHA256,
      started.operation.supervisor_generation
    ),
    'operation_active_identity_mismatch'
  )
  expectCode(
    () => rawReducer.startOperation(
      started.operation,
      'lop_probeconfigdrift01',
      authority,
      CONFIG_IDENTITY,
      PLAN_IDENTITY,
      'f'.repeat(64),
      started.operation.supervisor_generation
    ),
    'operation_active_identity_mismatch'
  )
  expectCode(
    () => rawReducer.startOperation(
      started.operation,
      'lop_configdrift02',
      authority,
      { ...CONFIG_IDENTITY, camera_policy: 'required' },
      PLAN_IDENTITY,
      PROBE_CONFIG_SHA256,
      started.operation.supervisor_generation
    ),
    'operation_active_identity_mismatch'
  )
  rawStore.releaseSupervisorLease(started.supervisorLease, authority)
}))

const startLifecycle = () => {
  let operation = reducer.createOperation(OPERATION_ID, authority)
  for (const nextEvent of [event('preflight_started'), event('preflight_passed'), event('start_requested')]) {
    operation = reducer.reduce(operation, nextEvent, authority)
  }
  return operation
}

const boundProbeResult = (operation, serviceId, ready = true) => {
  const service = operation.services.find((candidate) => candidate.service_id === serviceId)
  const descriptor = authority.probeDocument.descriptors.find((candidate) => candidate.service_id === serviceId)
  assert.ok(service)
  assert.ok(descriptor)
  assert.equal(service.pending_action, 'probe')
  return {
    schema_version: 'launcher_probe_result.v1',
    message_type: 'result',
    operation_id: operation.operation_id,
    supervisor_generation: operation.supervisor_generation,
    dispatch_id: service.pending_dispatch_id,
    expected_revision: service.probe_expected_revision,
    service_id: serviceId,
    probe_id: descriptor.probe_id,
    graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    descriptor_sha256: contract.canonicalJsonSha256(descriptor),
    config_sha256: PROBE_CONFIG_SHA256,
    requested_at: '2026-07-29T00:00:00.000Z',
    source_observed_at: '2026-07-29T00:00:00.100Z',
    observed_at: '2026-07-29T00:00:00.200Z',
    freshness_class: 'fresh',
    semantic_class: ready ? descriptor.success_semantic_classes[0] : 'not_ready',
    reason_class: ready ? 'none' : 'health_unavailable',
    ready,
    proof_ceiling: descriptor.proof_ceiling
  }
}

const fullReady = () => {
  let operation = startLifecycle()
  const specs = new Map(authority.graph.services.map((service) => [service.service_id, service]))
  for (const serviceId of authority.bindingDocument.binding.service_order) {
    const spec = specs.get(serviceId)
    if (spec.requirement === 'external') {
      operation = reducer.reduce(operation, event('probe_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('probe_transport_ready', serviceId), authority)
      operation = reducer.reduce(operation, {
        ...event('semantic_probe_completed', serviceId),
        probe_result: boundProbeResult(operation, serviceId)
      }, authority)
    } else if (spec.requirement === 'optional') {
      operation = reducer.reduce(operation, event('probe_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('optional_absent', serviceId), authority)
    } else {
      operation = reducer.reduce(operation, event('spawn_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('spawn_succeeded', serviceId), authority)
      operation = reducer.reduce(operation, event('probe_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('probe_transport_ready', serviceId), authority)
      operation = reducer.reduce(operation, {
        ...event('semantic_probe_completed', serviceId),
        probe_result: boundProbeResult(operation, serviceId)
      }, authority)
    }
    reducer.validateSnapshot(operation, authority)
  }
  assert.equal(operation.phase, 'ready')
  return operation
}

const fullStopped = () => {
  let operation = fullReady()
  operation = reducer.reduce(operation, event('stop_requested'), authority)
  for (const service of authority.graph.services.filter((item) => item.ownership === 'owned' && item.requirement !== 'optional')) {
    operation = reducer.reduce(operation, event('stop_dispatch_requested', service.service_id), authority)
    operation = reducer.reduce(operation, cleanupEvent(operation, 'service_stopped', service.service_id), authority)
  }
  operation = reducer.reduce(operation, privatePlanCleanupEvent(operation), authority)
  assert.equal(operation.phase, 'stopped')
  assert.equal(operation.cleanup, 'clear')
  return operation
}

const withCleanupAttempts = (operation, attempts) => ({
  ...operation,
  cleanup_attempts: attempts.map((attempt, index) => ({ ...attempt, sequence: index + 1 }))
})

test('authority is canonical, hash-bound, drift-checked, and LF-stable', () => {
  assert.equal(authority.bindingDocument.binding.text_hash_mode, 'utf8_lf_v1')
  assert.equal(authority.identities.bindingSha256, authority.bindingDocument.binding_sha256)
  const sample = '{\r\n  "ok": true\r\n}\r\n'
  assert.equal(contract.canonicalLfSha256(sample), contract.canonicalLfSha256(sample.replaceAll('\r\n', '\n')))
  const rendered = contract.renderBindingDocument({ graph: authority.graph, identities: authority.identities })
  assert.deepEqual(JSON.parse(rendered), authority.bindingDocument)
})

test('reduced authority binds the exact Parent seed to one ordered four-service graph', () => {
  const expectedOrder = [
    'openai_provider_broker',
    'thought_core_api',
    'thought_core_watcher',
    'aituber_kit'
  ]
  const graphSource = fs.readFileSync(
    path.join(ROOT, 'ops/manifests/launcher-service-graph.core-rehearsal-text-bubble.v1.json'),
    'utf8'
  )
  const graph = contract.validateGraph(
    JSON.parse(graphSource),
    contract.serviceIdPatternFromOperationSchema(JSON.parse(fs.readFileSync(
      path.join(ROOT, 'contracts/launcher/launcher-operation.v2.schema.json'),
      'utf8'
    )))
  )
  const probeSource = fs.readFileSync(
    path.join(ROOT, 'ops/manifests/launcher-probe-descriptors.core-rehearsal-text-bubble.v1.json'),
    'utf8'
  )
  const bindingDocument = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'contracts/launcher/generated/launcher-service-graph.core-rehearsal-text-bubble.v2.binding.json'),
    'utf8'
  ))
  assert.equal(graph.profile_id, REDUCED_PROFILE_ID)
  assert.deepEqual(contract.topologicalOrder(graph.services), expectedOrder)
  assert.equal(bindingDocument.binding.profile_id, REDUCED_PROFILE_ID)
  assert.equal(bindingDocument.binding.graph_sha256, contract.canonicalLfSha256(graphSource))
  assert.equal(bindingDocument.binding.probe_document_sha256, contract.canonicalLfSha256(probeSource))
  assert.equal(bindingDocument.binding_sha256, contract.canonicalJsonSha256(bindingDocument.binding))

  const reduced = contract.loadAuthority(ROOT, { profileId: REDUCED_PROFILE_ID })
  assert.equal(reduced.graph.profile_id, REDUCED_PROFILE_ID)
  assert.deepEqual(contract.topologicalOrder(reduced.graph.services), expectedOrder)
  assert.deepEqual(reduced.bindingDocument.binding.service_order, expectedOrder)
  assert.deepEqual(reduced.graph.services.map((service) => service.service_id), expectedOrder)
  assert.deepEqual(reduced.probeDocument.descriptors.map((descriptor) => descriptor.service_id), expectedOrder)
  assert.deepEqual(
    reduced.graph.services.map((service) => service.dependencies),
    [[], ['openai_provider_broker'], ['thought_core_api'], ['thought_core_watcher']]
  )
  assert.ok(reduced.graph.services.every((service) => (
    service.requirement === 'required' &&
    service.ownership === 'owned' &&
    service.start.adapter_id === 'job_worker_service' &&
    service.stop.adapter_id === 'job_worker_job_close'
  )))
  assert.equal(reduced.profileDocument.parent_profile.source_sha256, REDUCED_PARENT_PROFILE_SHA256)
  assert.equal(reduced.profileDocument.execution_contract.actions, 'disabled_action0')
  assert.equal(reduced.profileDocument.watcher_contract.turn_admission, 'held')
  assert.equal(
    reduced.probeDocument.descriptors.find((descriptor) => descriptor.service_id === 'aituber_kit').proof_ceiling,
    'aituber_http_reachability_only'
  )
})

test('reduced authority fails closed on duplicate membership and source or binding hash drift', () => {
  const graph = JSON.parse(fs.readFileSync(
    path.join(ROOT, 'ops/manifests/launcher-service-graph.core-rehearsal-text-bubble.v1.json'),
    'utf8'
  ))
  graph.services.push(structuredClone(graph.services[0]))
  expectCode(() => contract.validateGraph(graph, authority.serviceIdPattern), 'graph_service_duplicate')

  withReducedAuthorityFixture((runtimeRoot) => {
    const profilePath = path.join(runtimeRoot, 'ops/manifests/profiles/core-rehearsal-text-bubble-v0.json')
    const originalProfile = fs.readFileSync(profilePath, 'utf8')
    const duplicateProfile = JSON.parse(originalProfile)
    duplicateProfile.services[3] = duplicateProfile.services[0]
    fs.writeFileSync(profilePath, `${JSON.stringify(duplicateProfile)}\n`, 'utf8')
    expectCode(
      () => contract.loadAuthority(runtimeRoot, { profileId: REDUCED_PROFILE_ID }),
      'profile_services_invalid'
    )

    const parentDrift = JSON.parse(originalProfile)
    parentDrift.parent_profile.source_sha256 = 'f'.repeat(64)
    fs.writeFileSync(profilePath, `${JSON.stringify(parentDrift)}\n`, 'utf8')
    expectCode(
      () => contract.loadAuthority(runtimeRoot, { profileId: REDUCED_PROFILE_ID }),
      'profile_parent_source_invalid'
    )
    fs.writeFileSync(profilePath, originalProfile, 'utf8')

    const bindingPath = path.join(
      runtimeRoot,
      'contracts/launcher/generated/launcher-service-graph.core-rehearsal-text-bubble.v2.binding.json'
    )
    const driftedBinding = JSON.parse(fs.readFileSync(bindingPath, 'utf8'))
    driftedBinding.binding.graph_sha256 = 'f'.repeat(64)
    driftedBinding.binding_sha256 = contract.canonicalJsonSha256(driftedBinding.binding)
    fs.writeFileSync(bindingPath, `${JSON.stringify(driftedBinding)}\n`, 'utf8')
    expectCode(
      () => contract.loadAuthority(runtimeRoot, { profileId: REDUCED_PROFILE_ID }),
      'binding_source_drift'
    )
  })
})

test('operation schema is the single service-id and revision authority', () => {
  assert.equal(authority.serviceIdPattern, '^[a-z][a-z0-9_]{0,63}$')
  const operationMaximum = authority.operationSchema.properties.revision.maximum
  assert.equal(operationMaximum, Number.MAX_SAFE_INTEGER)
  assert.equal(authority.workerSchema.$defs.request.properties.expected_revision.maximum, operationMaximum)
  assert.equal(authority.workerSchema.$defs.result.properties.expected_revision.maximum, operationMaximum)
  const graph = structuredClone(authority.graph)
  graph.services[0].service_id = 'bad-service'
  expectCode(() => contract.validateGraph(graph, authority.serviceIdPattern), 'graph_service_id_invalid')
  const drifted = structuredClone(authority.operationSchema)
  drifted.$defs.service_id.pattern = '^[a-z][a-z0-9_-]{0,63}$'
  expectCode(() => contract.serviceIdPatternFromOperationSchema(drifted), 'operation_service_id_pattern_invalid')
})

test('validated authority is defensive-copied and recursively immutable', () => {
  const walk = (value) => {
    if (!value || typeof value !== 'object') return
    assert.equal(Object.isFrozen(value), true)
    for (const child of Object.values(value)) walk(child)
  }
  for (const value of [authority.graph, authority.bindingDocument, authority.graphSchema, authority.operationSchema, authority.workerSchema, authority.reducerVectors, authority.identities]) walk(value)
  const originalId = authority.graph.services[0].service_id
  assert.throws(() => { authority.graph.services[0].service_id = 'mutated' }, TypeError)
  assert.throws(() => { authority.bindingDocument.binding.service_order.push('mutated') }, TypeError)
  assert.equal(authority.graph.services[0].service_id, originalId)
  assert.equal(reducer.createOperation(OPERATION_ID, authority).services[0].service_id, originalId)
})

test('bounded strict UTF-8 reads reject oversized and malformed fixtures before parsing', () => withRuntimeRoot((runtimeRoot) => {
  const oversized = path.join(runtimeRoot, 'oversized.json')
  const malformed = path.join(runtimeRoot, 'malformed.json')
  fs.writeFileSync(oversized, Buffer.alloc(9, 0x20))
  fs.writeFileSync(malformed, Buffer.from([0xc3, 0x28]))
  expectCode(() => contract.readBoundedUtf8Text(oversized, 8, 'fixture_read_failed', 'fixture_oversized'), 'fixture_oversized')
  expectCode(() => contract.readBoundedUtf8Text(malformed, 8, 'fixture_utf8_invalid', 'fixture_oversized'), 'fixture_utf8_invalid')
}))

test('graph pins the future Windows worker adapters and external no-op boundary', () => {
  for (const service of authority.graph.services) {
    if (service.ownership === 'owned') {
      assert.equal(service.start.adapter_id, 'job_worker_service')
      assert.equal(service.stop.adapter_id, 'job_worker_job_close')
      assert.equal(service.stop.escalation, 'owned_only')
    } else {
      assert.equal(service.start.adapter_id, 'external_probe_only')
      assert.equal(service.stop.adapter_id, 'external_noop')
      assert.equal(service.stop.escalation, 'none')
    }
  }
})

test('graph and reducer-vector collection counts are bounded', () => {
  const graph = structuredClone(authority.graph)
  graph.services = Array.from({ length: 65 }, () => structuredClone(authority.graph.services[0]))
  expectCode(() => contract.validateGraph(graph, authority.serviceIdPattern), 'graph_services_invalid')
  const vectors = structuredClone(authority.reducerVectors)
  vectors.vectors = Array.from({ length: 257 }, () => structuredClone(authority.reducerVectors.vectors[0]))
  expectCode(() => contract.validateReducerVectors(vectors, authority.serviceIdPattern), 'reducer_vectors_invalid')
})

test('strict worker protocol rejects unknown and mismatched shapes', () => {
  const request = workerRequest({ operation_id: OPERATION_ID, revision: 4 })
  assert.equal(contract.validateWorkerMessage(request, authority), request)
  assert.equal(contract.validateWorkerMessage({ ...request, action: 'probe' }, authority).adapter_class, 'job_worker_service')
  assert.equal(contract.validateWorkerMessage({ ...request, expected_revision: Number.MAX_SAFE_INTEGER }, authority).expected_revision, Number.MAX_SAFE_INTEGER)
  expectCode(() => contract.validateWorkerMessage({ ...request, command: 'PRIVATE_SENTINEL' }, authority), 'worker_request_shape_invalid')
  expectCode(() => contract.validateWorkerMessage({ ...request, adapter_class: 'external_probe_only' }, authority), 'worker_action_adapter_mismatch')
  expectCode(() => contract.validateWorkerMessage({ ...request, deadline_ms: 300001 }, authority), 'worker_deadline_invalid')
  expectCode(() => contract.validateWorkerMessage({ ...request, expected_revision: Number.MAX_SAFE_INTEGER + 1 }, authority), 'worker_revision_invalid')
  expectCode(() => contract.validateWorkerMessage({ ...request, service_id: 'home-assistant-bridge' }, authority), 'worker_service_id_invalid')
})

test('worker request deadlines are derived from the exact service and action authority', () => {
  const services = new Map(authority.graph.services.map((service) => [service.service_id, service]))
  let startOperation = startLifecycle()
  startOperation = reducer.reduce(startOperation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const startSpec = services.get('home_assistant_bridge')
  const startRequest = workerRequest(startOperation, 'home_assistant_bridge', 'start', startSpec.start.adapter_id, startSpec.ready_deadline_ms)
  assert.equal(contract.validateWorkerRequestAgainstAuthority(startRequest, authority), startRequest)
  expectCode(() => contract.validateWorkerRequestAgainstAuthority({ ...startRequest, deadline_ms: 1 }, authority), 'worker_request_deadline_mismatch')
  const startResult = {
    schema_version: 'launcher_worker.v2', message_type: 'result', operation_id: startOperation.operation_id,
    supervisor_generation: startRequest.supervisor_generation, dispatch_id: startRequest.dispatch_id,
    service_id: 'home_assistant_bridge', action: 'start', expected_revision: startOperation.revision,
    worker_nonce: startRequest.worker_nonce, result_class: 'accepted', ownership_class: 'matched',
    listener_class: 'not_applicable', descendant_class: 'owned_active'
  }
  expectCode(() => reducer.workerResultToEvent(startResult, startOperation, { ...startRequest, deadline_ms: 1 }, authority), 'worker_request_deadline_mismatch')

  const probeRequest = workerRequest(startOperation, 'home_assistant_bridge', 'probe', 'job_worker_service', startSpec.ready_deadline_ms)
  assert.equal(contract.validateWorkerRequestAgainstAuthority(probeRequest, authority), probeRequest)
  expectCode(() => contract.validateWorkerRequestAgainstAuthority({ ...probeRequest, deadline_ms: startSpec.ready_deadline_ms - 1 }, authority), 'worker_request_deadline_mismatch')

  let stopping = fullReady()
  stopping = reducer.reduce(stopping, event('stop_requested'), authority)
  const stopRequest = workerRequest(stopping, 'home_assistant_bridge', 'stop', startSpec.stop.adapter_id, startSpec.stop.graceful_timeout_ms)
  assert.equal(contract.validateWorkerRequestAgainstAuthority(stopRequest, authority), stopRequest)
  expectCode(() => contract.validateWorkerRequestAgainstAuthority({ ...stopRequest, deadline_ms: startSpec.stop.graceful_timeout_ms - 1 }, authority), 'worker_request_deadline_mismatch')

  const external = services.get('voicevox')
  const externalProbe = workerRequest(startOperation, 'voicevox', 'probe', 'external_probe_only', external.ready_deadline_ms)
  const externalStop = workerRequest(stopping, 'voicevox', 'stop', 'external_noop', 0)
  assert.equal(contract.validateWorkerRequestAgainstAuthority(externalProbe, authority), externalProbe)
  assert.equal(contract.validateWorkerRequestAgainstAuthority(externalStop, authority), externalStop)
  expectCode(() => contract.validateWorkerRequestAgainstAuthority({ ...externalProbe, deadline_ms: 0 }, authority), 'worker_request_deadline_mismatch')
  expectCode(() => contract.validateWorkerRequestAgainstAuthority({ ...externalStop, deadline_ms: 1 }, authority), 'worker_request_deadline_mismatch')
})

test('worker result correlation protects revision, nonce, ownership, PID, and listener identity', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const request = workerRequest(operation)
  const result = {
    schema_version: 'launcher_worker.v2', message_type: 'result', operation_id: OPERATION_ID,
    supervisor_generation: request.supervisor_generation, authority_lease_proof: request.authority_lease_proof,
    dispatch_id: request.dispatch_id,
    service_id: 'home_assistant_bridge', action: 'start', expected_revision: operation.revision,
    worker_nonce: 'lw_0000000000000001', result_class: 'accepted', ownership_class: 'matched',
    listener_class: 'matched', descendant_class: 'owned_active', termination_class: 'not_applicable',
    job_query_class: 'not_applicable', active_count_after: null, post_stop_listener_class: 'not_applicable'
  }
  assert.equal(reducer.workerResultToEvent(result, operation, request, authority).event_type, 'spawn_succeeded')
  expectCode(() => reducer.workerResultToEvent({ ...result, expected_revision: operation.revision + 1 }, operation, request, authority), 'worker_result_correlation_mismatch')
  expectCode(() => reducer.workerResultToEvent({ ...result, worker_nonce: 'lw_0000000000000002' }, operation, request, authority), 'worker_result_correlation_mismatch')
  expectCode(() => reducer.workerResultToEvent({ ...result, action: 'probe' }, operation, request, authority), 'worker_result_correlation_mismatch')
  expectCode(() => reducer.workerResultToEvent(result, operation, { ...request, adapter_class: 'external_probe_only' }, authority), 'worker_action_adapter_mismatch')
  expectCode(() => reducer.workerResultToEvent({ ...result, result_class: 'stopped' }, operation, request, authority), 'worker_result_action_incompatible')
  const mismatch = reducer.workerResultToEvent({ ...result, ownership_class: 'mismatch' }, operation, request, authority)
  assert.equal(mismatch.event_type, 'listener_mismatch')
  assert.equal(reducer.workerResultToEvent({ ...result, ownership_class: 'unknown' }, operation, request, authority).event_type, 'listener_mismatch')
  operation = reducer.reduce(operation, reducer.workerResultToEvent(result, operation, request, authority), authority)
  operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
  const probeRequest = workerRequest(operation, 'home_assistant_bridge', 'probe', 'job_worker_service')
  const probeResult = {
    ...result, action: 'probe', result_class: 'ready', listener_class: 'matched',
    expected_revision: probeRequest.expected_revision, dispatch_id: probeRequest.dispatch_id
  }
  assert.equal(reducer.workerResultToEvent(probeResult, operation, probeRequest, authority).event_type, 'probe_transport_ready')
})

test('Stop proof is incomplete unless termination Job count and post-stop listener are all clear', () => {
  const stopDeadline = authority.graph.services.find((service) => service.service_id === 'home_assistant_bridge').stop.graceful_timeout_ms
  const cases = [
    ['termination_unknown', { termination_class: 'unknown' }],
    ['job_failed', { job_query_class: 'failed', active_count_after: null }],
    ['job_unknown', { job_query_class: 'unknown', active_count_after: null }],
    ['job_nonzero', { active_count_after: 1 }],
    ['listener_foreign', { post_stop_listener_class: 'foreign_present' }],
    ['listener_unknown', { post_stop_listener_class: 'unknown' }]
  ]
  for (const [name, proof] of cases) {
    let operation = fullReady()
    operation = reducer.reduce(operation, event('stop_requested'), authority)
    operation = reducer.reduce(operation, event('stop_dispatch_requested', 'home_assistant_bridge'), authority)
    const request = workerRequest(operation, 'home_assistant_bridge', 'stop', 'job_worker_job_close', stopDeadline)
    const result = workerResult(request, {
      result_class: 'stopped',
      ownership_class: 'matched',
      descendant_class: 'owned_clear',
      ...proof
    })
    const failure = reducer.workerResultToEvent(result, operation, request, authority)
    assert.equal(failure.event_type, 'stop_failed', name)
    operation = reducer.reduce(operation, failure, authority)
    assert.equal(operation.phase, 'residue', name)
    assert.notEqual(operation.cleanup, 'clear', name)
    assert.deepEqual(operation.cleanup_attempts.at(-1), failure.cleanup_attempt, name)
  }

  let operation = fullReady()
  operation = reducer.reduce(operation, event('stop_requested'), authority)
  operation = reducer.reduce(operation, event('stop_dispatch_requested', 'home_assistant_bridge'), authority)
  const request = workerRequest(operation, 'home_assistant_bridge', 'stop', 'job_worker_job_close', stopDeadline)
  const missing = workerResult(request, { result_class: 'stopped', descendant_class: 'owned_clear' })
  delete missing.post_stop_listener_class
  expectCode(() => reducer.workerResultToEvent(missing, operation, request, authority), 'worker_result_shape_invalid')
})

test('service cleanup clear rows require the complete forced-or-already-clear proof tuple', () => {
  const base = serviceCleanupAttempt({ cleanup_attempts: [] }, 'home_assistant_bridge')
  const malformed = [
    { termination_class: 'unknown' },
    { termination_class: 'graceful' },
    { termination_class: 'not_applicable' },
    { job_query_class: 'failed', active_count_after: null },
    { job_query_class: 'unknown', active_count_after: null },
    { job_query_class: 'not_applicable', active_count_after: null },
    { active_count_after: null },
    { active_count_after: 1 },
    { post_stop_listener_class: 'foreign_present' },
    { post_stop_listener_class: 'unknown' }
  ]
  for (const mutation of malformed) {
    assert.throws(() => contract.validateCleanupAttempt({ ...base, ...mutation }, authority.serviceIdPattern))
  }
  assert.equal(contract.validateCleanupAttempt(base, authority.serviceIdPattern), base)
  assert.equal(contract.validateCleanupAttempt({ ...base, termination_class: 'already_clear' }, authority.serviceIdPattern).termination_class, 'already_clear')
  assert.equal(contract.validateCleanupAttempt({ ...base, post_stop_listener_class: 'clear' }, authority.serviceIdPattern).post_stop_listener_class, 'clear')
})

test('terminal cleanup clear requires each participating owned service final row to be complete', () => {
  const stopped = fullStopped()
  const participatingId = stopped.services.find((service) => service.state === 'stopped' && service.attempt_sequence > 0).service_id
  const serviceRows = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'service')
  const privatePlanRows = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'private_plan')
  const participantRows = serviceRows.filter((attempt) => attempt.responsible_id === participatingId)
  const otherServiceRows = serviceRows.filter((attempt) => attempt.responsible_id !== participatingId)
  const earlierClear = participantRows.at(-1)
  const failed = {
    ...earlierClear,
    outcome_class: 'failed',
    reason_class: 'stop_failed',
    active_count_after: 1,
    post_stop_listener_class: 'foreign_present'
  }
  const unattempted = {
    ...earlierClear,
    outcome_class: 'unattempted',
    reason_class: 'unattempted_transport_unavailable',
    termination_class: 'unknown',
    job_query_class: 'unknown',
    active_count_after: null,
    post_stop_listener_class: 'unknown'
  }
  const incomplete = { ...earlierClear, active_count_after: 1 }
  const invalidTerminalRecords = [
    withCleanupAttempts(stopped, privatePlanRows),
    withCleanupAttempts(stopped, [...otherServiceRows, ...privatePlanRows]),
    withCleanupAttempts(stopped, [...serviceRows, failed, ...privatePlanRows]),
    withCleanupAttempts(stopped, [...serviceRows, unattempted, ...privatePlanRows]),
    withCleanupAttempts(stopped, [...serviceRows, incomplete, ...privatePlanRows])
  ]
  for (const record of invalidTerminalRecords) {
    expectCode(() => reducer.validateSnapshot(record, authority), 'operation_store_record_invalid')
  }
  assert.equal(reducer.validateSnapshot(stopped, authority), stopped)
})

test('terminal cleanup clear requires the final private-plan row except for exact preflight no-side-effect failure', () => {
  let preflight = reducer.createOperation(OPERATION_ID, authority)
  preflight = reducer.reduce(preflight, event('preflight_started'), authority)
  preflight = reducer.reduce(preflight, event('preflight_failed'), authority)
  assert.equal(reducer.hasTerminalPrivatePlanProof(preflight), true)
  assert.equal(reducer.isClearTerminalFailure(preflight, authority), true)
  assert.equal(reducer.validateSnapshot(preflight, authority), preflight)

  const stopped = fullStopped()
  const serviceRows = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'service')
  const privatePlanClear = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'private_plan').at(-1)
  const privatePlanFailed = {
    ...privatePlanClear,
    outcome_class: 'failed',
    reason_class: 'private_plan_cleanup_failed'
  }
  const privatePlanUnattempted = {
    ...privatePlanClear,
    outcome_class: 'unattempted',
    reason_class: 'unattempted_transport_unavailable'
  }
  const nonPreflightFailure = {
    ...withCleanupAttempts(stopped, serviceRows),
    intent: 'start',
    phase: 'failed',
    reason: 'spawn_failed',
    primary_result: { class: 'spawn_failed', responsible_id: 'home_assistant_bridge', action_certainty: 'may_have_occurred' }
  }
  const invalidTerminalRecords = [
    withCleanupAttempts(stopped, serviceRows),
    withCleanupAttempts(stopped, [...stopped.cleanup_attempts, privatePlanFailed]),
    withCleanupAttempts(stopped, [...stopped.cleanup_attempts, privatePlanUnattempted]),
    nonPreflightFailure
  ]
  for (const record of invalidTerminalRecords) {
    assert.equal(reducer.hasTerminalPrivatePlanProof(record), false)
    expectCode(() => reducer.validateSnapshot(record, authority), 'operation_store_record_invalid')
  }

  const firstOwnedIndex = preflight.services.findIndex((service) => service.state === 'stopped')
  const nearMisses = [
    {
      ...preflight,
      primary_result: { ...preflight.primary_result, action_certainty: 'may_have_occurred' }
    },
    {
      ...preflight,
      reason: 'spawn_failed',
      primary_result: { class: 'spawn_failed', responsible_id: 'launcher_supervisor', action_certainty: 'not_attempted' }
    },
    {
      ...preflight,
      services: preflight.services.map((service, index) => index === firstOwnedIndex
        ? { ...service, attempt_sequence: 1 }
        : { ...service })
    },
    withCleanupAttempts(preflight, [{ ...privatePlanFailed, sequence: 1 }])
  ]
  for (const record of nearMisses) {
    assert.equal(reducer.hasTerminalPrivatePlanProof(record), false)
    expectCode(() => reducer.validateSnapshot(record, authority), 'operation_store_record_invalid')
  }
})

test('recovery and residue completion cannot clear an absent or superseded private-plan proof', () => {
  const stopped = fullStopped()
  const privatePlanClear = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'private_plan').at(-1)
  const serviceRows = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'service')
  const privatePlanFailed = {
    ...privatePlanClear,
    outcome_class: 'failed',
    reason_class: 'private_plan_cleanup_failed'
  }
  const privatePlanUnattempted = {
    ...privatePlanClear,
    outcome_class: 'unattempted',
    reason_class: 'unattempted_transport_unavailable'
  }
  const recovering = (intent, attempts) => withCleanupAttempts({
    ...stopped,
    intent,
    phase: 'recovering',
    reason: 'supervisor_crash',
    cleanup: 'unknown',
    cleanup_result: { class: 'unknown', responsible_id: 'launcher_supervisor' },
    primary_result: { class: 'supervisor_crash', responsible_id: 'launcher_supervisor', action_certainty: 'may_have_occurred' },
    recovery_required: true
  }, attempts)

  const successful = reducer.reduce(recovering('start', stopped.cleanup_attempts), event('recovery_completed'), authority)
  assert.equal(successful.phase, 'failed')
  assert.equal(successful.cleanup, 'clear')
  reducer.validateSnapshot(successful, authority)

  for (const attempts of [
    serviceRows,
    [...stopped.cleanup_attempts, privatePlanFailed],
    [...stopped.cleanup_attempts, privatePlanUnattempted]
  ]) {
    for (const intent of ['start', 'stop']) {
      const result = reducer.reduce(recovering(intent, attempts), event('recovery_completed'), authority)
      assert.notEqual(result.cleanup, 'clear')
      reducer.validateSnapshot(result, authority)
    }
  }

  const residueServiceId = stopped.services.find((service) => service.state === 'stopped' && service.attempt_sequence > 0).service_id
  const residue = {
    ...recovering('stop', [...stopped.cleanup_attempts, privatePlanFailed]),
    phase: 'residue',
    cleanup: 'residue',
    services: stopped.services.map((service) => service.service_id === residueServiceId
      ? { ...service, state: 'residue' }
      : { ...service }),
    residue_service_ids: [residueServiceId],
    cleanup_result: { class: 'stop_failed', responsible_id: residueServiceId }
  }
  reducer.validateSnapshot(residue, authority)
  const residueCleared = reducer.reduce(residue, event('residue_cleared', residueServiceId), authority)
  assert.notEqual(residueCleared.cleanup, 'clear')
  reducer.validateSnapshot(residueCleared, authority)
})

test('ordinary Stop rollback recovery and residue events cannot clear a partial cleanup ledger', () => {
  const stopped = fullStopped()
  const participating = stopped.services.find((service) => service.state === 'stopped' && service.attempt_sequence > 0)
  const clearRow = stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'service' && attempt.responsible_id === participating.service_id).at(-1)
  const failedRow = {
    ...clearRow,
    sequence: stopped.cleanup_attempts.length + 1,
    outcome_class: 'failed',
    reason_class: 'stop_failed',
    active_count_after: 1,
    post_stop_listener_class: 'foreign_present'
  }
  const partialAttempts = [...stopped.cleanup_attempts, failedRow]

  let ordinary = fullReady()
  ordinary = reducer.reduce(ordinary, event('stop_requested'), authority)
  const omittedId = participating.service_id
  for (const service of authority.graph.services.filter((item) => item.ownership === 'owned' && item.requirement !== 'optional')) {
    ordinary = reducer.reduce(ordinary, event('stop_dispatch_requested', service.service_id), authority)
    if (service.service_id === omittedId) {
      ordinary = {
        ...ordinary,
        services: ordinary.services.map((current) => current.service_id === omittedId
          ? { ...current, state: 'stopped', pending_dispatch_id: null, pending_action: null }
          : { ...current })
      }
    } else {
      ordinary = reducer.reduce(ordinary, cleanupEvent(ordinary, 'service_stopped', service.service_id), authority)
    }
  }
  ordinary = reducer.reduce(ordinary, privatePlanCleanupEvent(ordinary), authority)
  assert.notEqual(ordinary.cleanup, 'clear')
  reducer.validateSnapshot(ordinary, authority)

  const rollback = {
    ...stopped,
    intent: 'start',
    phase: 'rolling_back',
    reason: 'spawn_failed',
    cleanup: 'in_progress',
    cleanup_attempts: partialAttempts,
    primary_result: { class: 'spawn_failed', responsible_id: participating.service_id, action_certainty: 'may_have_occurred' },
    cleanup_result: { class: 'in_progress', responsible_id: 'launcher_supervisor' },
    rollback_required: true,
    recovery_required: false
  }
  reducer.validateSnapshot(rollback, authority)
  const rolledBack = reducer.reduce(rollback, event('rollback_completed'), authority)
  assert.notEqual(rolledBack.cleanup, 'clear')
  reducer.validateSnapshot(rolledBack, authority)

  const recovering = {
    ...rollback,
    phase: 'recovering',
    cleanup: 'unknown',
    cleanup_result: { class: 'unknown', responsible_id: 'launcher_supervisor' },
    rollback_required: false,
    recovery_required: true
  }
  reducer.validateSnapshot(recovering, authority)
  const recovered = reducer.reduce(recovering, event('recovery_completed'), authority)
  assert.notEqual(recovered.cleanup, 'clear')
  reducer.validateSnapshot(recovered, authority)

  const residue = {
    ...recovering,
    phase: 'residue',
    cleanup: 'residue',
    services: recovering.services.map((service) => service.service_id === participating.service_id ? { ...service, state: 'residue' } : { ...service }),
    residue_service_ids: [participating.service_id],
    cleanup_result: { class: 'stop_failed', responsible_id: participating.service_id }
  }
  reducer.validateSnapshot(residue, authority)
  const residueCleared = reducer.reduce(residue, event('residue_cleared', participating.service_id), authority)
  assert.notEqual(residueCleared.cleanup, 'clear')
  reducer.validateSnapshot(residueCleared, authority)

  const missingPlanRecovery = withCleanupAttempts({
    ...stopped,
    phase: 'recovering',
    reason: 'supervisor_crash',
    cleanup: 'unknown',
    primary_result: { class: 'supervisor_crash', responsible_id: 'launcher_supervisor', action_certainty: 'may_have_occurred' },
    cleanup_result: { class: 'unknown', responsible_id: 'launcher_supervisor' },
    recovery_required: true
  }, stopped.cleanup_attempts.filter((attempt) => attempt.target_class === 'service'))
  reducer.validateSnapshot(missingPlanRecovery, authority)
  const missingPlanResult = reducer.reduce(missingPlanRecovery, event('recovery_completed'), authority)
  assert.notEqual(missingPlanResult.cleanup, 'clear')
  reducer.validateSnapshot(missingPlanResult, authority)
})

test('semantic probe result is required, fully correlated, and retained before Ready', () => {
  const prepare = () => {
    let operation = startLifecycle()
    operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
    operation = reducer.reduce(operation, event('spawn_succeeded', 'home_assistant_bridge'), authority)
    operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
    operation = reducer.reduce(operation, event('probe_transport_ready', 'home_assistant_bridge'), authority)
    return operation
  }
  const pending = prepare()
  const pendingService = pending.services.find((service) => service.service_id === 'home_assistant_bridge')
  assert.equal(pending.phase, 'waiting_ready')
  assert.equal(pendingService.state, 'starting')
  assert.equal(pendingService.probe_status, 'transport_ready')
  assert.equal(pendingService.last_probe_result, null)

  const accepted = boundProbeResult(pending, 'home_assistant_bridge')
  const completed = reducer.reduce(pending, {
    ...event('semantic_probe_completed', 'home_assistant_bridge'),
    probe_result: accepted
  }, authority)
  const readyService = completed.services.find((service) => service.service_id === 'home_assistant_bridge')
  assert.equal(readyService.state, 'ready')
  assert.equal(readyService.probe_status, 'ready')
  assert.deepEqual(readyService.last_probe_result, accepted)
  reducer.validateSnapshot(completed, authority)

  const forgedPersisted = {
    ...completed,
    services: completed.services.map((service) => service.service_id === 'home_assistant_bridge'
      ? { ...service, last_probe_result: { ...service.last_probe_result, config_sha256: 'f'.repeat(64) } }
      : { ...service })
  }
  expectCode(() => reducer.validateSnapshot(forgedPersisted, authority), 'operation_store_record_invalid')

  for (const mutation of [
    { dispatch_id: 'ld_ffffffffffffffff' },
    { supervisor_generation: accepted.supervisor_generation + 1 },
    { expected_revision: accepted.expected_revision + 1 },
    { descriptor_sha256: 'f'.repeat(64) },
    { config_sha256: 'f'.repeat(64) }
  ]) {
    const invalid = reducer.reduce(prepare(), {
      ...event('semantic_probe_completed', 'home_assistant_bridge'),
      probe_result: { ...accepted, ...mutation }
    }, authority)
    assert.equal(invalid.reason, 'invalid_event')
    assert.equal(invalid.services.find((service) => service.service_id === 'home_assistant_bridge').state, 'starting')
    reducer.validateSnapshot(invalid, authority)
  }

  const privateExtra = reducer.reduce(prepare(), {
    ...event('semantic_probe_completed', 'home_assistant_bridge'),
    probe_result: { ...accepted, raw_body: 'private-sentinel' }
  }, authority)
  assert.equal(privateExtra.reason, 'invalid_event')
  assert.equal(JSON.stringify(privateExtra).includes('private-sentinel'), false)
})

test('persisted results allow only fresh pre-request Environment snapshots', () => {
  const prepareOwnedProbe = (serviceId, operation = startLifecycle()) => {
    let prepared = operation
    prepared = reducer.reduce(prepared, event('spawn_requested', serviceId), authority)
    prepared = reducer.reduce(prepared, event('spawn_succeeded', serviceId), authority)
    prepared = reducer.reduce(prepared, event('probe_requested', serviceId), authority)
    return reducer.reduce(prepared, event('probe_transport_ready', serviceId), authority)
  }
  const prepareEnvironmentProbe = () => {
    let operation = prepareOwnedProbe('home_assistant_bridge')
    operation = reducer.reduce(operation, {
      ...event('semantic_probe_completed', 'home_assistant_bridge'),
      probe_result: boundProbeResult(operation, 'home_assistant_bridge')
    }, authority)
    return prepareOwnedProbe('environment_state_server', operation)
  }
  const environmentResult = (operation, overrides = {}) => ({
    ...boundProbeResult(operation, 'environment_state_server'),
    requested_at: '2026-07-29T00:00:10.000Z',
    source_observed_at: '2026-07-29T00:00:05.000Z',
    observed_at: '2026-07-29T00:00:10.100Z',
    ...overrides
  })
  const complete = (operation, result) => reducer.reduce(operation, {
    ...event('semantic_probe_completed', result.service_id),
    probe_result: result
  }, authority)

  const pending = prepareEnvironmentProbe()
  const accepted = environmentResult(pending)
  const ready = complete(pending, accepted)
  const service = ready.services.find((candidate) => candidate.service_id === 'environment_state_server')
  assert.equal(ready.reason, 'none')
  assert.equal(service.state, 'ready')
  assert.deepEqual(service.last_probe_result, accepted)
  reducer.validateSnapshot(ready, authority)

  for (const invalidTimes of [
    { source_observed_at: '2026-07-28T23:59:40.099Z' },
    { source_observed_at: '2026-07-29T00:00:10.101Z' },
    { requested_at: '2026-07-29T00:00:10.101Z' },
    { observed_at: '2026-07-29T00:00:25.001Z' },
    { requested_at: 0, source_observed_at: 0, observed_at: 0 },
    { requested_at: {} },
    { source_observed_at: '2026-07-29T00:00:05+00:00' },
    { observed_at: '2026-07-29T00:00:10Z' }
  ]) {
    const candidate = prepareEnvironmentProbe()
    const rejected = complete(candidate, environmentResult(candidate, invalidTimes))
    assert.equal(rejected.reason, 'invalid_event')
    assert.equal(rejected.services.find((candidate) => candidate.service_id === 'environment_state_server').last_probe_result, null)
    reducer.validateSnapshot(rejected, authority)
  }

  const homePending = prepareOwnedProbe('home_assistant_bridge')
  const homePreRequest = {
    ...boundProbeResult(homePending, 'home_assistant_bridge'),
    requested_at: '2026-07-29T00:00:10.000Z',
    source_observed_at: '2026-07-29T00:00:09.999Z',
    observed_at: '2026-07-29T00:00:10.100Z'
  }
  const rejectedHome = complete(homePending, homePreRequest)
  assert.equal(rejectedHome.reason, 'invalid_event')
  assert.equal(rejectedHome.services.find((candidate) => candidate.service_id === 'home_assistant_bridge').last_probe_result, null)
  reducer.validateSnapshot(rejectedHome, authority)
})

test('semantic not-ready result fails closed with its bounded result retained', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('spawn_succeeded', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('probe_transport_ready', 'home_assistant_bridge'), authority)
  const result = boundProbeResult(operation, 'home_assistant_bridge', false)
  operation = reducer.reduce(operation, {
    ...event('semantic_probe_completed', 'home_assistant_bridge'),
    probe_result: result
  }, authority)
  const service = operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge')
  assert.equal(operation.phase, 'rolling_back')
  assert.equal(operation.reason, 'semantic_probe_failed')
  assert.equal(service.state, 'failed')
  assert.equal(service.probe_status, 'not_ready')
  assert.deepEqual(service.last_probe_result, result)
  reducer.validateSnapshot(operation, authority)
})

test('rollback and recovery persist one correlated stop dispatch before cleanup result', () => {
  let rollback = startLifecycle()
  rollback = reducer.reduce(rollback, event('spawn_requested', 'home_assistant_bridge'), authority)
  rollback = reducer.reduce(rollback, event('spawn_succeeded', 'home_assistant_bridge'), authority)
  rollback = reducer.reduce(rollback, event('probe_requested', 'home_assistant_bridge'), authority)
  rollback = reducer.reduce(rollback, event('readiness_timeout', 'home_assistant_bridge'), authority)
  assert.equal(rollback.phase, 'rolling_back')
  const rollbackDispatch = dispatchId('home_assistant_bridge', 'stop', 2)
  rollback = reducer.reduce(rollback, event('stop_dispatch_requested', 'home_assistant_bridge', OPERATION_ID, rollbackDispatch), authority)
  assert.equal(rollback.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id, rollbackDispatch)
  reducer.validateSnapshot(rollback, authority)
  rollback = reducer.reduce(rollback, cleanupEvent(rollback, 'service_stopped', 'home_assistant_bridge', OPERATION_ID, rollbackDispatch), authority)
  assert.equal(rollback.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id, null)
  assert.equal(rollback.services.find((service) => service.service_id === 'home_assistant_bridge').state, 'stopped')
  reducer.validateSnapshot(rollback, authority)

  let recovery = fullReady()
  recovery = reducer.reduce(recovery, event('supervisor_crashed'), authority)
  assert.equal(recovery.phase, 'recovering')
  const recoveryDispatch = dispatchId('home_assistant_bridge', 'stop', 3)
  recovery = reducer.reduce(recovery, event('stop_dispatch_requested', 'home_assistant_bridge', OPERATION_ID, recoveryDispatch), authority)
  recovery = reducer.reduce(recovery, cleanupEvent(recovery, 'service_stopped', 'home_assistant_bridge', OPERATION_ID, recoveryDispatch), authority)
  const recoveredService = recovery.services.find((service) => service.service_id === 'home_assistant_bridge')
  assert.equal(recoveredService.pending_dispatch_id, null)
  assert.equal(recoveredService.state, 'stopped')
  reducer.validateSnapshot(recovery, authority)
})

test('supervisor crash cancels interrupted dispatch before correlated recovery stop', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const interrupted = workerRequest(operation)
  assert.equal(operation.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id, interrupted.dispatch_id)
  operation = reducer.reduce(operation, event('supervisor_crashed'), authority)
  const service = operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge')
  assert.equal(operation.phase, 'recovering')
  assert.equal(service.pending_dispatch_id, null)
  assert.equal(service.pending_action, null)
  expectCode(() => reducer.workerResultToEvent(workerResult(interrupted), operation, interrupted, authority), 'worker_result_correlation_mismatch')
  const recoveryDispatch = dispatchId('home_assistant_bridge', 'stop', 4)
  operation = reducer.reduce(operation, event('stop_dispatch_requested', 'home_assistant_bridge', OPERATION_ID, recoveryDispatch), authority)
  assert.equal(operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge').pending_dispatch_id, recoveryDispatch)
  reducer.validateSnapshot(operation, authority)
})

test('generation fencing and dispatch correlation reject losers without global revision coupling', () => {
  let operation = reducer.createOperation(OPERATION_ID, authority, 7)
  for (const nextEvent of [event('preflight_started'), event('preflight_passed'), event('start_requested')]) operation = reducer.reduce(operation, nextEvent, authority)
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const firstRequest = workerRequest(operation, 'home_assistant_bridge')
  const firstResult = workerResult(firstRequest)
  expectCode(() => reducer.workerResultToEvent({ ...firstResult, supervisor_generation: 6 }, operation, firstRequest, authority), 'worker_result_correlation_mismatch')
  expectCode(() => reducer.workerResultToEvent({ ...firstResult, dispatch_id: 'ld_ffffffffffffffff' }, operation, firstRequest, authority), 'worker_result_correlation_mismatch')

  operation = reducer.reduce(operation, event('spawn_requested', 'aituber_kit'), authority)
  assert.notEqual(operation.revision, firstRequest.expected_revision)
  assert.equal(reducer.workerResultToEvent(firstResult, operation, firstRequest, authority).event_type, 'spawn_succeeded')

  operation = reducer.reduce(operation, reducer.workerResultToEvent(firstResult, operation, firstRequest, authority), authority)
  operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
  expectCode(() => reducer.workerResultToEvent(firstResult, operation, firstRequest, authority), 'worker_result_correlation_mismatch')
})

test('same-service dispatch is single-flight and primary failure survives cleanup failure', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const duplicate = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge', OPERATION_ID, dispatchId('home_assistant_bridge', 'start', 2)), authority)
  assert.equal(duplicate.reason, 'invalid_event')
  assert.equal(duplicate.services.find((service) => service.service_id === 'home_assistant_bridge').attempt_sequence, 1)

  operation = reducer.reduce(operation, event('spawn_failed', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, cleanupEvent(operation, 'rollback_failed', 'home_assistant_bridge'), authority)
  assert.deepEqual(operation.primary_result, {
    class: 'spawn_failed', responsible_id: 'home_assistant_bridge', action_certainty: 'may_have_occurred'
  })
  assert.deepEqual(operation.cleanup_result, { class: 'rollback_failed', responsible_id: 'home_assistant_bridge' })
  assert.equal(operation.reason, 'spawn_failed')
  assert.equal(operation.cleanup, 'residue')
  reducer.validateSnapshot(operation, authority)
})

test('operation store generations increase only when a terminal operation is replaced', () => withRuntimeRoot((runtimeRoot) => {
  const first = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  assert.equal(first.operation.supervisor_generation, 1)
  store.releaseSupervisorLease(first.supervisorLease, authority)
  expectCode(() => store.startAndPersist('lop_node0002', authority, runtimeRoot), 'supervisor_lease_unavailable')
  let current = first.operation
  current = store.reduceAndPersist(current, event('preflight_started'), authority, runtimeRoot)
  current = store.reduceAndPersist(current, event('preflight_failed'), authority, runtimeRoot)
  const replacement = store.startAndPersist('lop_node0003', authority, runtimeRoot)
  assert.equal(replacement.joined_existing, false)
  assert.equal(replacement.operation.supervisor_generation, 2)
  store.releaseSupervisorLease(replacement.supervisorLease, authority)
}))

test('clear terminal failure stops without owned dispatch and permits a fresh changed-config generation', () => {
  let failed = reducer.createOperation(OPERATION_ID, authority)
  failed = reducer.reduce(failed, event('preflight_started'), authority)
  failed = reducer.reduce(failed, event('preflight_failed'), authority)
  const failedServices = failed.services.map((service) => ({ ...service }))

  assert.equal(rawReducer.isClearTerminalFailure(failed, authority), true)
  let stopped = reducer.reduce(failed, event('stop_requested'), authority)
  assert.equal(stopped.phase, 'stopping')
  assert.equal(stopped.cleanup, 'in_progress')
  stopped = reducer.reduce(stopped, privatePlanCleanupEvent(stopped), authority)
  assert.equal(stopped.phase, 'stopped')
  assert.equal(stopped.cleanup, 'clear')
  assert.equal(stopped.intent, 'stop')
  assert.equal(stopped.revision, failed.revision + 2)
  assert.deepEqual(stopped.services, failedServices)
  assert.equal(stopped.services.some((service) => service.pending_action === 'stop'), false)
  rawReducer.validateSnapshot(stopped, authority)

  const replacementConfig = Object.freeze({
    ...CONFIG_IDENTITY,
    effective_config_sha256: 'f'.repeat(64)
  })
  const replacementPlan = Object.freeze({
    private_plan_sha256: '3'.repeat(64),
    worker_executable_class: 'windows_powershell_system32',
    worker_executable_sha256: '4'.repeat(64)
  })
  const replacement = rawReducer.startOperation(
    failed,
    'lop_clearreplace01',
    authority,
    replacementConfig,
    replacementPlan,
    '5'.repeat(64),
    failed.supervisor_generation + 1
  )
  assert.equal(replacement.joined_existing, false)
  assert.equal(replacement.operation.operation_id, 'lop_clearreplace01')
  assert.equal(replacement.operation.supervisor_generation, failed.supervisor_generation + 1)
  assert.equal(replacement.operation.effective_config_sha256, replacementConfig.effective_config_sha256)
  assert.equal(replacement.operation.private_plan_sha256, replacementPlan.private_plan_sha256)
  assert.equal(replacement.operation.probe_config_sha256, '5'.repeat(64))

  const ownedServiceIndex = failed.services.findIndex((service) => service.service_id === 'home_assistant_bridge')
  const forgedCases = [
    {
      name: 'start_dispatch',
      changes: {
        pending_action: 'start',
        pending_dispatch_id: dispatchId('home_assistant_bridge', 'start', 91)
      }
    },
    {
      name: 'probe_dispatch',
      changes: {
        pending_action: 'probe',
        pending_dispatch_id: dispatchId('home_assistant_bridge', 'probe', 92),
        probe_status: 'pending',
        probe_expected_revision: failed.revision
      }
    }
  ]
  for (const forgedCase of forgedCases) {
    const forged = {
      ...failed,
      services: failed.services.map((service, index) => index === ownedServiceIndex
        ? { ...service, ...forgedCase.changes }
        : { ...service })
    }
    assert.doesNotThrow(() => rawReducer.validateSnapshot(forged, authority), forgedCase.name)
    assert.equal(rawReducer.isClearTerminalFailure(forged, authority), false, forgedCase.name)
  }
})

test('private supervisor lifetime lease rejects a competing nonterminal Start without record mutation', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const binding = store.getSupervisorLeaseBinding(started.supervisorLease, authority)
  assert.match(binding.authority_lease_proof, /^lp_[a-f0-9]{64}$/u)
  assert.deepEqual(Object.keys(binding).sort(), ['authority_lease_proof', 'operation_id', 'supervisor_generation'])
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  const leasePath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.SUPERVISOR_LEASE_FILE)
  const recordBefore = fs.readFileSync(recordPath)
  const leaseBefore = fs.readFileSync(leasePath)
  expectCode(() => store.startAndPersist('lop_competing01', authority, runtimeRoot), 'supervisor_lease_unavailable')
  assert.deepEqual(fs.readFileSync(recordPath), recordBefore)
  assert.deepEqual(fs.readFileSync(leasePath), leaseBefore)
  assert.equal(store.readOperation(authority, runtimeRoot).revision, 0)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('private supervisor lifetime lease rejects a competing terminal replacement without generation mutation', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  let terminal = store.reduceAndPersist(started.operation, event('preflight_started'), authority, runtimeRoot)
  terminal = store.reduceAndPersist(terminal, event('preflight_failed'), authority, runtimeRoot)
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  const leasePath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.SUPERVISOR_LEASE_FILE)
  const recordBefore = fs.readFileSync(recordPath)
  const leaseBefore = fs.readFileSync(leasePath)
  expectCode(() => store.startAndPersist('lop_competing02', authority, runtimeRoot), 'supervisor_lease_unavailable')
  assert.deepEqual(fs.readFileSync(recordPath), recordBefore)
  assert.deepEqual(fs.readFileSync(leasePath), leaseBefore)
  const persisted = store.readOperation(authority, runtimeRoot)
  assert.equal(persisted.phase, 'failed')
  assert.equal(persisted.supervisor_generation, 1)
  assert.equal(persisted.operation_id, terminal.operation_id)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('first-operation publish has no fallible access step after its atomic rename', () => withRuntimeRoot((runtimeRoot) => {
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const temporaryPath = path.join(child, store.TEMP_FILE)
  const recordPath = path.join(child, store.RECORD_FILE)
  const originalRename = fs.renameSync
  const originalChmod = fs.chmodSync
  let recordCommitted = false
  let postCommitAccessAttempts = 0
  fs.renameSync = (source, destination) => {
    const result = originalRename(source, destination)
    if (path.resolve(source) === path.resolve(temporaryPath) && path.resolve(destination) === path.resolve(recordPath)) recordCommitted = true
    return result
  }
  fs.chmodSync = (target, mode) => {
    if (recordCommitted && path.resolve(target) === path.resolve(recordPath)) {
      postCommitAccessAttempts++
      throw new Error('PRIVATE_SENTINEL')
    }
    return originalChmod(target, mode)
  }
  let started
  try { started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot) } finally {
    fs.renameSync = originalRename
    fs.chmodSync = originalChmod
  }
  assert.equal(recordCommitted, true)
  assert.equal(postCommitAccessAttempts, 0)
  assert.equal(store.readOperation(authority, runtimeRoot).supervisor_generation, 1)
  assert.equal(fs.existsSync(path.join(child, store.SUPERVISOR_LEASE_FILE)), true)
  assert.equal(store.getSupervisorLeaseBinding(started.supervisorLease, authority).supervisor_generation, 1)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('terminal replacement publish has no fallible access step after its atomic rename', () => withRuntimeRoot((runtimeRoot) => {
  const first = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  let terminal = store.reduceAndPersist(first.operation, event('preflight_started'), authority, runtimeRoot)
  terminal = store.reduceAndPersist(terminal, event('preflight_failed'), authority, runtimeRoot)
  store.releaseSupervisorLease(first.supervisorLease, authority)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const temporaryPath = path.join(child, store.TEMP_FILE)
  const recordPath = path.join(child, store.RECORD_FILE)
  const originalRename = fs.renameSync
  const originalChmod = fs.chmodSync
  let recordCommitted = false
  let postCommitAccessAttempts = 0
  fs.renameSync = (source, destination) => {
    const result = originalRename(source, destination)
    if (path.resolve(source) === path.resolve(temporaryPath) && path.resolve(destination) === path.resolve(recordPath)) recordCommitted = true
    return result
  }
  fs.chmodSync = (target, mode) => {
    if (recordCommitted && path.resolve(target) === path.resolve(recordPath)) {
      postCommitAccessAttempts++
      throw new Error('PRIVATE_SENTINEL')
    }
    return originalChmod(target, mode)
  }
  let replacement
  try { replacement = store.startAndPersist('lop_replacement01', authority, runtimeRoot) } finally {
    fs.renameSync = originalRename
    fs.chmodSync = originalChmod
  }
  assert.equal(recordCommitted, true)
  assert.equal(postCommitAccessAttempts, 0)
  assert.equal(replacement.operation.supervisor_generation, 2)
  assert.equal(store.readOperation(authority, runtimeRoot).supervisor_generation, 2)
  assert.equal(fs.existsSync(path.join(child, store.SUPERVISOR_LEASE_FILE)), true)
  assert.equal(store.getSupervisorLeaseBinding(replacement.supervisorLease, authority).supervisor_generation, 2)
  store.releaseSupervisorLease(replacement.supervisorLease, authority)
}))

test('post-commit transient lock release failure returns the committed result and an idempotent cleanup route', () => withRuntimeRoot((runtimeRoot) => {
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const temporaryPath = path.join(child, store.TEMP_FILE)
  const recordPath = path.join(child, store.RECORD_FILE)
  const lockPath = path.join(child, store.LOCK_FILE)
  const originalRename = fs.renameSync
  const originalUnlink = fs.unlinkSync
  let recordCommitted = false
  let injectedFailures = 0
  fs.renameSync = (source, destination) => {
    const result = originalRename(source, destination)
    if (path.resolve(source) === path.resolve(temporaryPath) && path.resolve(destination) === path.resolve(recordPath)) recordCommitted = true
    return result
  }
  fs.unlinkSync = (target) => {
    if (recordCommitted && path.resolve(target) === path.resolve(lockPath) && injectedFailures === 0) {
      injectedFailures++
      throw new Error('PRIVATE_SENTINEL')
    }
    return originalUnlink(target)
  }
  let started
  try { started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot) } finally {
    fs.renameSync = originalRename
    fs.unlinkSync = originalUnlink
  }
  assert.equal(recordCommitted, true)
  assert.equal(injectedFailures, 1)
  assert.equal(started.operation_lock_cleanup_class, 'operation_lock_release_failed')
  assert.equal(started.operation.supervisor_generation, 1)
  assert.equal(fs.existsSync(recordPath), true)
  assert.equal(fs.existsSync(path.join(child, store.SUPERVISOR_LEASE_FILE)), true)
  assert.equal(fs.existsSync(lockPath), true)
  assert.equal(store.getSupervisorLeaseBinding(started.supervisorLease, authority).supervisor_generation, 1)
  const publicRecord = fs.readFileSync(recordPath, 'utf8')
  assert.equal(publicRecord.includes('operation_lock_cleanup_class'), false)
  assert.equal(JSON.stringify(started).includes('PRIVATE_SENTINEL'), false)
  assert.equal(store.retryStartCleanup(started, authority), 'clear')
  assert.equal(store.retryStartCleanup(started, authority), 'clear')
  assert.equal(fs.existsSync(lockPath), false)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('stale recovery may clear a retained post-commit lock without orphaning the lifetime lease', () => withRuntimeRoot((runtimeRoot) => {
  const first = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  let terminal = store.reduceAndPersist(first.operation, event('preflight_started'), authority, runtimeRoot)
  terminal = store.reduceAndPersist(terminal, event('preflight_failed'), authority, runtimeRoot)
  store.releaseSupervisorLease(first.supervisorLease, authority)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const temporaryPath = path.join(child, store.TEMP_FILE)
  const recordPath = path.join(child, store.RECORD_FILE)
  const lockPath = path.join(child, store.LOCK_FILE)
  const originalRename = fs.renameSync
  const originalUnlink = fs.unlinkSync
  let recordCommitted = false
  let injectedFailures = 0
  fs.renameSync = (source, destination) => {
    const result = originalRename(source, destination)
    if (path.resolve(source) === path.resolve(temporaryPath) && path.resolve(destination) === path.resolve(recordPath)) recordCommitted = true
    return result
  }
  fs.unlinkSync = (target) => {
    if (recordCommitted && path.resolve(target) === path.resolve(lockPath) && injectedFailures === 0) {
      injectedFailures++
      throw new Error('PRIVATE_SENTINEL')
    }
    return originalUnlink(target)
  }
  let replacement
  try { replacement = store.startAndPersist('lop_replacement02', authority, runtimeRoot) } finally {
    fs.renameSync = originalRename
    fs.unlinkSync = originalUnlink
  }
  assert.equal(replacement.operation_lock_cleanup_class, 'operation_lock_release_failed')
  const stale = JSON.parse(fs.readFileSync(lockPath, 'utf8'))
  stale.created_at_ms = Date.now() - store.LOCK_STALE_MS - 1000
  fs.writeFileSync(lockPath, `${JSON.stringify(stale)}\n`)
  const recovered = store.readOperation(authority, runtimeRoot, () => 'absent')
  assert.equal(recovered.supervisor_generation, 2)
  assert.equal(fs.existsSync(lockPath), false)
  assert.equal(store.retryStartCleanup(replacement, authority), 'clear')
  assert.equal(store.retryStartCleanup(replacement, authority), 'clear')
  assert.equal(store.getSupervisorLeaseBinding(replacement.supervisorLease, authority).supervisor_generation, 2)
  store.releaseSupervisorLease(replacement.supervisorLease, authority)
}))

test('supervisor lease fails closed for live reused denied unknown malformed and old-generation owners', () => {
  for (const ownerState of ['alive', 'pid_reused', 'access_denied', 'unknown']) {
    withRuntimeRoot((runtimeRoot) => {
      const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
      store.releaseSupervisorLease(started.supervisorLease, authority)
      const leasePath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.SUPERVISOR_LEASE_FILE)
      const bytes = Buffer.from(`${JSON.stringify(abandonedSupervisorLeaseRecord(424242))}\n`)
      fs.writeFileSync(leasePath, bytes, { mode: 0o600 })
      expectCode(() => store.acquireSupervisorLease({
        operationId: OPERATION_ID, supervisorGeneration: started.operation.supervisor_generation,
        authority, authorizedPrivateRuntimeRoot: runtimeRoot, ownerLivenessObserver: () => ownerState
      }), 'supervisor_lease_unavailable')
      assert.deepEqual(fs.readFileSync(leasePath), bytes)
    })
  }
  withRuntimeRoot((runtimeRoot) => {
    const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
    store.releaseSupervisorLease(started.supervisorLease, authority)
    const leasePath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.SUPERVISOR_LEASE_FILE)
    fs.writeFileSync(leasePath, 'malformed-private-lease', { mode: 0o600 })
    expectCode(() => store.acquireSupervisorLease({
      operationId: OPERATION_ID, supervisorGeneration: started.operation.supervisor_generation,
      authority, authorizedPrivateRuntimeRoot: runtimeRoot, ownerLivenessObserver: () => 'absent'
    }), 'supervisor_lease_unavailable')
  })
  withRuntimeRoot((runtimeRoot) => {
    const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
    store.releaseSupervisorLease(started.supervisorLease, authority)
    expectCode(() => store.acquireSupervisorLease({
      operationId: OPERATION_ID, supervisorGeneration: started.operation.supervisor_generation + 1,
      authority, authorizedPrivateRuntimeRoot: runtimeRoot
    }), 'supervisor_lease_operation_mismatch')
  })
})

test('absent owner liveness never authorizes supervisor lease takeover', () => {
  withRuntimeRoot((runtimeRoot) => {
    const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
    store.releaseSupervisorLease(started.supervisorLease, authority)
    const leasePath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.SUPERVISOR_LEASE_FILE)
    const bytes = Buffer.from(`${JSON.stringify(abandonedSupervisorLeaseRecord(424242))}\n`)
    fs.writeFileSync(leasePath, bytes, { mode: 0o600 })
    expectCode(() => store.acquireSupervisorLease({
      operationId: OPERATION_ID, supervisorGeneration: started.operation.supervisor_generation,
      authority, authorizedPrivateRuntimeRoot: runtimeRoot, ownerLivenessObserver: () => 'absent'
    }), 'supervisor_lease_unavailable')
    assert.deepEqual(fs.readFileSync(leasePath), bytes)
  })
})

test('missing supervisor lease cannot create authority for an active operation', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  store.releaseSupervisorLease(started.supervisorLease, authority)
  expectCode(() => store.acquireSupervisorLease({
    operationId: OPERATION_ID, supervisorGeneration: started.operation.supervisor_generation,
    authority, authorizedPrivateRuntimeRoot: runtimeRoot, ownerLivenessObserver: () => 'absent'
  }), 'supervisor_lease_unavailable')
  expectCode(() => store.startAndPersist('lop_competing03', authority, runtimeRoot, () => 'absent'), 'supervisor_lease_unavailable')
  assert.equal(store.readOperation(authority, runtimeRoot).operation_id, OPERATION_ID)
}))

test('external probe timeout becomes a bounded failure without fake external readiness', () => {
  let operation = startLifecycle()
  const externalSpec = authority.graph.services.find((service) => service.service_id === 'voicevox')
  operation = reducer.reduce(operation, event('probe_requested', 'voicevox'), authority)
  const request = workerRequest(operation, 'voicevox', 'probe', 'external_probe_only', externalSpec.ready_deadline_ms)
  const result = {
    schema_version: 'launcher_worker.v2', message_type: 'result', operation_id: OPERATION_ID,
    supervisor_generation: request.supervisor_generation, authority_lease_proof: request.authority_lease_proof,
    dispatch_id: request.dispatch_id,
    service_id: 'voicevox', action: 'probe', expected_revision: operation.revision,
    worker_nonce: request.worker_nonce, result_class: 'readiness_timeout', ownership_class: 'not_applicable',
    listener_class: 'not_applicable', descendant_class: 'not_applicable', termination_class: 'not_applicable',
    job_query_class: 'not_applicable', active_count_after: null, post_stop_listener_class: 'not_applicable'
  }
  const timeoutEvent = reducer.workerResultToEvent(result, operation, request, authority)
  assert.deepEqual(timeoutEvent, { event_type: 'readiness_timeout', operation_id: OPERATION_ID, service_id: 'voicevox', dispatch_id: request.dispatch_id })
  operation = reducer.reduce(operation, timeoutEvent, authority)
  assert.equal(operation.phase, 'rolling_back')
  assert.equal(operation.reason, 'readiness_timeout')
  assert.equal(operation.rollback_required, true)
  assert.equal(operation.services.find((service) => service.service_id === 'voicevox').state, 'failed')
  reducer.validateSnapshot(operation, authority)
  operation = reducer.reduce(operation, privatePlanCleanupEvent(operation), authority)
  operation = reducer.reduce(operation, event('rollback_completed'), authority)
  assert.equal(operation.phase, 'failed')
  assert.equal(operation.cleanup, 'clear')
  assert.equal(operation.services.find((service) => service.service_id === 'voicevox').state, 'failed')
  reducer.validateSnapshot(operation, authority)
})

test('every correlated external probe failure consumes dispatch and rolls back without external stop authority', () => {
  const externalSpec = authority.graph.services.find((service) => service.service_id === 'voicevox')
  const cases = [
    ['invalid_request', 'probe_failed', 'semantic_probe_failed'],
    ['internal_failure', 'probe_failed', 'semantic_probe_failed'],
    ['cancelled', 'probe_failed', 'semantic_probe_failed'],
    ['early_exit', 'probe_failed', 'semantic_probe_failed'],
    ['listener_mismatch', 'listener_mismatch', 'listener_mismatch']
  ]
  for (const [resultClass, eventType, reason] of cases) {
    let operation = startLifecycle()
    operation = reducer.reduce(operation, event('probe_requested', 'voicevox'), authority)
    const request = workerRequest(operation, 'voicevox', 'probe', 'external_probe_only', externalSpec.ready_deadline_ms)
    const result = workerResult(request, {
      result_class: resultClass,
      ownership_class: 'not_applicable',
      listener_class: resultClass === 'listener_mismatch' ? 'mismatch' : 'not_applicable',
      descendant_class: 'not_applicable'
    })
    const failureEvent = reducer.workerResultToEvent(result, operation, request, authority)
    assert.equal(failureEvent.event_type, eventType, resultClass)
    operation = reducer.reduce(operation, failureEvent, authority)
    assert.equal(operation.phase, 'rolling_back', resultClass)
    assert.equal(operation.reason, reason, resultClass)
    assert.equal(operation.rollback_required, true, resultClass)
    const external = operation.services.find((service) => service.service_id === 'voicevox')
    assert.equal(external.state, 'failed', resultClass)
    assert.equal(external.pending_dispatch_id, null, resultClass)
    assert.equal(external.pending_action, null, resultClass)
    reducer.validateSnapshot(operation, authority)
  }
})

test('all immutable reducer vectors execute and preserve valid snapshots', () => {
  const coverage = new Set()
  for (const vector of authority.reducerVectors.vectors) {
    let operation = reducer.createOperation(OPERATION_ID, authority)
    for (const vectorEvent of vector.events) {
      operation = reducer.reduce(operation, {
        ...event(vectorEvent.event_type, vectorEvent.service_id, vector.event_operation_id || OPERATION_ID),
        ...(vectorEvent.dispatch_id ? { dispatch_id: vectorEvent.dispatch_id } : {}),
        ...(vectorEvent.action ? { action: vectorEvent.action } : {}),
        ...(vectorEvent.cleanup_attempt ? { cleanup_attempt: vectorEvent.cleanup_attempt } : {})
      }, authority)
      reducer.validateSnapshot(operation, authority)
    }
    assert.equal(operation.phase, vector.expected.phase, vector.vector_id)
    assert.equal(operation.reason, vector.expected.reason, vector.vector_id)
    assert.equal(operation.cleanup, vector.expected.cleanup, vector.vector_id)
    assert.deepEqual(operation.residue_service_ids, vector.expected.residue_service_ids, vector.vector_id)
    assert.deepEqual(operation.cleanup_attempts, vector.expected.cleanup_attempts, vector.vector_id)
    vector.coverage.forEach((item) => coverage.add(item))
  }
  for (const required of ['primary_result', 'cleanup_result', 'cleanup_attempts', 'dispatch', 'operation_identity', 'replay', 'residue']) {
    assert.ok(coverage.has(required), required)
  }
})

test('full graph reaches Ready and repeats ten Start/Stop cycles', () => {
  for (let cycle = 0; cycle < 10; cycle += 1) {
    let operation = fullReady()
    operation = reducer.reduce(operation, event('stop_requested'), authority)
    for (const service of authority.graph.services.filter((item) => item.ownership === 'owned' && item.requirement !== 'optional')) {
      operation = reducer.reduce(operation, event('stop_dispatch_requested', service.service_id), authority)
      operation = reducer.reduce(operation, cleanupEvent(operation, 'service_stopped', service.service_id), authority)
    }
    operation = reducer.reduce(operation, privatePlanCleanupEvent(operation), authority)
    assert.equal(operation.phase, 'stopped')
    assert.equal(operation.cleanup, 'clear')
    assert.deepEqual(operation.residue_service_ids, [])
    assert.strictEqual(reducer.reduce(operation, event('stop_requested'), authority), operation)
  }
})

test('operation revision uses the same safe-integer ceiling as both schemas', () => {
  const maximum = { ...reducer.createOperation(OPERATION_ID, authority), revision: Number.MAX_SAFE_INTEGER }
  assert.equal(reducer.validateSnapshot(maximum, authority).revision, Number.MAX_SAFE_INTEGER)
  const conflict = reducer.startOperation(maximum, 'lop_node0002', authority)
  assert.equal(conflict.joined_existing, true)
  assert.equal(conflict.operation.joined_existing, false)
  assert.equal(conflict.operation.revision, Number.MAX_SAFE_INTEGER)
})

test('first failure survives rollback and recovery', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('spawn_failed', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('supervisor_crashed'), authority)
  assert.equal(operation.reason, 'spawn_failed')
  const cleanupDispatch = dispatchId('home_assistant_bridge', 'stop', 2)
  operation = reducer.reduce(operation, event('stop_dispatch_requested', 'home_assistant_bridge', OPERATION_ID, cleanupDispatch), authority)
  operation = reducer.reduce(operation, cleanupEvent(operation, 'service_stopped', 'home_assistant_bridge', OPERATION_ID, cleanupDispatch), authority)
  operation = reducer.reduce(operation, privatePlanCleanupEvent(operation), authority)
  operation = reducer.reduce(operation, event('recovery_completed'), authority)
  assert.equal(operation.reason, 'spawn_failed')
  assert.equal(operation.phase, 'failed')
  assert.equal(operation.cleanup, 'clear')
})

test('foreign operation event is a pure no-op before store access', () => {
  const operation = reducer.createOperation(OPERATION_ID, authority)
  const foreign = { event_type: 'preflight_started', operation_id: 'lop_foreign00' }
  assert.strictEqual(reducer.reduce(operation, foreign, authority), operation)
  const nonexistent = path.join(os.tmpdir(), `missing-${Date.now()}-${Math.random()}`)
  assert.strictEqual(store.reduceAndPersist(operation, foreign, authority, nonexistent), operation)
  assert.equal(fs.existsSync(nonexistent), false)
})

test('store persists planned before preflight and refuses a missing-lease join', () => withRuntimeRoot((runtimeRoot) => {
  const first = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  assert.equal(first.operation.phase, 'planned')
  assert.equal(first.operation.revision, 0)
  assert.equal(first.joined_existing, false)
  store.releaseSupervisorLease(first.supervisorLease, authority)
  expectCode(() => store.startAndPersist('lop_node0002', authority, runtimeRoot), 'supervisor_lease_unavailable')
  const current = store.readOperation(authority, runtimeRoot)
  assert.equal(current.operation_id, OPERATION_ID)
  assert.equal(current.revision, 0)
  assert.equal(fs.readFileSync(path.join(runtimeRoot, 'parent-sentinel.txt'), 'utf8'), 'parent-unchanged')
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  assert.deepEqual(fs.readdirSync(child).sort(), [store.RECORD_FILE])
  assert.equal(fs.readdirSync(child).some((name) => name.endsWith('.tmp')), false)
}))

test('store revision CAS rejects stale input without rewriting the record', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  let current = started.operation
  current = store.reduceAndPersist(current, event('preflight_started'), authority, runtimeRoot)
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  const before = fs.readFileSync(recordPath)
  const stale = { ...current, revision: current.revision - 1 }
  expectCode(() => store.reduceAndPersist(stale, event('preflight_passed'), authority, runtimeRoot), 'operation_store_revision_conflict')
  assert.deepEqual(fs.readFileSync(recordPath), before)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('store recovers an exact stale owned lock and complete crash temp deterministically', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  let current = started.operation
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const lockPath = path.join(child, store.LOCK_FILE)
  const tempPath = path.join(child, store.TEMP_FILE)
  const pending = reducer.reduce(current, event('preflight_started'), authority)
  fs.writeFileSync(tempPath, `${JSON.stringify(pending)}\n`)
  fs.writeFileSync(lockPath, `${JSON.stringify(staleLockRecord(424242))}\n`)
  current = store.readOperation(authority, runtimeRoot, () => 'absent')
  assert.equal(current.revision, pending.revision)
  assert.equal(current.phase, pending.phase)
  assert.equal(fs.existsSync(lockPath), false)
  assert.equal(fs.existsSync(tempPath), false)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('store rejects every coupled config drift in a complete crash temp without changing either record', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const current = started.operation
  const pending = reducer.reduce(current, event('preflight_started'), authority)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const recordPath = path.join(child, store.RECORD_FILE)
  const tempPath = path.join(child, store.TEMP_FILE)
  const currentBytes = fs.readFileSync(recordPath)
  const mutations = [
    ['profile_id', 'other-profile'],
    ['effective_config_sha256', 'f'.repeat(64)],
    ['camera_policy', current.camera_policy === 'required' ? 'camera_excluded_by_profile' : 'required'],
    ['probe_config_sha256', 'f'.repeat(64)]
  ]
  for (const [field, value] of mutations) {
    const pendingBytes = Buffer.from(`${JSON.stringify({ ...pending, [field]: value })}\n`, 'utf8')
    fs.writeFileSync(tempPath, pendingBytes)
    expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_recovery_invalid')
    assert.deepEqual(fs.readFileSync(recordPath), currentBytes, field)
    assert.deepEqual(fs.readFileSync(tempPath), pendingBytes, field)
    fs.unlinkSync(tempPath)
  }
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('stale lock held by the current live owner is never renamed or stolen', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const lockPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.LOCK_FILE)
  const bytes = Buffer.from(`${JSON.stringify(staleLockRecord(process.pid))}\n`, 'utf8')
  const descriptor = fs.openSync(lockPath, 'wx')
  fs.writeFileSync(descriptor, bytes)
  fs.fsyncSync(descriptor)
  try {
    expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_lock_unavailable')
    assert.deepEqual(fs.readFileSync(lockPath), bytes)
  } finally {
    fs.closeSync(descriptor)
    fs.unlinkSync(lockPath)
  }
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('stale recovery fails closed for alive, PID reuse, unknown, denied, malformed, and observer errors', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const lockPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.LOCK_FILE)
  const bytes = Buffer.from(`${JSON.stringify(staleLockRecord(424243))}\n`, 'utf8')
  for (const observer of [
    () => 'alive', () => 'pid_reused', () => 'unknown', () => 'access_denied', () => 'malformed',
    () => { throw new Error('PRIVATE_SENTINEL') }
  ]) {
    fs.writeFileSync(lockPath, bytes)
    expectCode(() => store.readOperation(authority, runtimeRoot, observer), 'operation_store_lock_unavailable')
    assert.deepEqual(fs.readFileSync(lockPath), bytes)
    fs.unlinkSync(lockPath)
  }
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('concurrent stale-lock replacement is restored unchanged and never reclaimed', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const lockPath = path.join(child, store.LOCK_FILE)
  const recoveryPath = path.join(child, store.LOCK_RECOVERY_FILE)
  fs.writeFileSync(lockPath, `${JSON.stringify(staleLockRecord(424244))}\n`)
  const replacement = Buffer.from(`${JSON.stringify(staleLockRecord(424245, 'll_11111111111111111111111111111111'))}\n`, 'utf8')
  const originalRename = fs.renameSync
  fs.renameSync = (source, destination) => {
    if (path.resolve(source) === path.resolve(lockPath) && path.resolve(destination) === path.resolve(recoveryPath)) {
      fs.writeFileSync(lockPath, replacement)
    }
    return originalRename(source, destination)
  }
  try {
    expectCode(() => store.readOperation(authority, runtimeRoot, () => 'absent'), 'operation_store_lock_unavailable')
  } finally {
    fs.renameSync = originalRename
  }
  assert.deepEqual(fs.readFileSync(lockPath), replacement)
  assert.equal(fs.existsSync(recoveryPath), false)
  fs.unlinkSync(lockPath)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('recovery-file owner replacement during observation is preserved and never inferred from the old owner', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const recoveryPath = path.join(child, store.LOCK_RECOVERY_FILE)
  const discardPath = path.join(child, store.LOCK_DISCARD_FILE)
  const ownerA = staleLockRecord(424246, 'll_22222222222222222222222222222222')
  const ownerB = staleLockRecord(424247, 'll_33333333333333333333333333333333')
  const ownerBBytes = Buffer.from(`${JSON.stringify(ownerB)}\n`, 'utf8')
  fs.writeFileSync(recoveryPath, `${JSON.stringify(ownerA)}\n`)
  const observedPids = []
  const observer = (owner) => {
    observedPids.push(owner.owner_pid)
    if (observedPids.length === 1) fs.writeFileSync(recoveryPath, ownerBBytes)
    return 'absent'
  }
  expectCode(() => store.readOperation(authority, runtimeRoot, observer), 'operation_store_lock_unavailable')
  assert.deepEqual(observedPids, [ownerA.owner_pid])
  assert.deepEqual(fs.readFileSync(recoveryPath), ownerBBytes)
  assert.equal(fs.existsSync(discardPath), false)
  fs.unlinkSync(recoveryPath)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('store removes only a validated stale owned temp and preserves invalid recovery bytes', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const current = started.operation
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const tempPath = path.join(child, store.TEMP_FILE)
  fs.writeFileSync(tempPath, `${JSON.stringify(current)}\n`)
  assert.equal(store.readOperation(authority, runtimeRoot).revision, current.revision)
  assert.equal(fs.existsSync(tempPath), false)
  store.releaseSupervisorLease(started.supervisorLease, authority)
  fs.writeFileSync(tempPath, 'foreign-incomplete')
  expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_recovery_invalid')
  assert.equal(fs.readFileSync(tempPath, 'utf8'), 'foreign-incomplete')
}))

test('action failure remains authoritative when unlock also fails', () => withRuntimeRoot((runtimeRoot) => {
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  fs.mkdirSync(child)
  const lockPath = path.join(child, store.LOCK_FILE)
  const originalUnlink = fs.unlinkSync
  fs.unlinkSync = (target) => {
    if (path.resolve(target) === path.resolve(lockPath)) throw new Error('PRIVATE_SENTINEL')
    return originalUnlink(target)
  }
  try {
    assert.throws(() => store.readOperation(authority, runtimeRoot), (error) => {
      assert.equal(error.code, 'operation_store_record_missing')
      assert.equal(error.cleanup_code, 'operation_store_lock_release_failed')
      assert.deepEqual(JSON.parse(JSON.stringify(error)), {
        name: 'LauncherContractError', code: 'operation_store_record_missing',
        cleanup_code: 'operation_store_lock_release_failed'
      })
      return true
    })
  } finally {
    fs.unlinkSync = originalUnlink
    if (fs.existsSync(lockPath)) fs.unlinkSync(lockPath)
  }
}))

test('operation record read and write enforce the public byte ceiling', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  store.releaseSupervisorLease(started.supervisorLease, authority)
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  fs.writeFileSync(recordPath, Buffer.alloc(store.MAX_OPERATION_RECORD_BYTES + 1, 0x20))
  expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_record_oversized')
  assert.equal(fs.statSync(recordPath).size, store.MAX_OPERATION_RECORD_BYTES + 1)
}))

test('store rejects lock collision and foreign content without touching it', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  store.releaseSupervisorLease(started.supervisorLease, authority)
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  fs.writeFileSync(path.join(child, store.LOCK_FILE), 'foreign-lock')
  expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_lock_unavailable')
  assert.equal(fs.readFileSync(path.join(child, store.LOCK_FILE), 'utf8'), 'foreign-lock')
  fs.unlinkSync(path.join(child, store.LOCK_FILE))
  fs.writeFileSync(path.join(child, 'foreign.txt'), 'foreign-unchanged')
  expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_foreign_content')
  assert.equal(fs.readFileSync(path.join(child, 'foreign.txt'), 'utf8'), 'foreign-unchanged')
}))

test('store rejects a task-owned junction before operation mutation', { skip: process.platform !== 'win32' }, () => withRuntimeRoot((runtimeRoot) => {
  const target = fs.mkdtempSync(path.join(os.tmpdir(), 'sword-launcher-target-'))
  const junction = path.join(runtimeRoot, store.STORE_DIRECTORY)
  try {
    fs.symlinkSync(target, junction, 'junction')
    expectCode(() => store.startAndPersist(OPERATION_ID, authority, runtimeRoot), 'operation_store_reparse_rejected')
    assert.deepEqual(fs.readdirSync(target), [])
  } finally {
    try { fs.unlinkSync(junction) } catch {}
    fs.rmSync(target, { recursive: true, force: true })
  }
}))

test('invalid authority and identity fail before any operation store I/O', () => {
  const nonexistent = path.join(os.tmpdir(), `no-store-${Date.now()}-${Math.random()}`)
  expectCode(() => store.startAndPersist(OPERATION_ID, {}, nonexistent), 'authority_not_validated')
  expectCode(() => store.startAndPersist('bad', authority, nonexistent), 'operation_id_invalid')
  assert.equal(fs.existsSync(nonexistent), false)
})

test('operation record contains only bounded public state', () => withRuntimeRoot((runtimeRoot) => {
  const started = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const record = fs.readFileSync(path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE), 'utf8')
  const prohibited = new Set(['stdout', 'stderr', 'exception', 'command', 'args', 'env', 'token', 'secret', 'path', 'url', 'pid', 'process'])
  const visit = (value) => {
    if (Array.isArray(value)) return value.forEach(visit)
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        assert.equal(prohibited.has(key.toLowerCase()), false, key)
        visit(child)
      }
    }
  }
  visit(JSON.parse(record))
  assert.equal(record.includes('PRIVATE_SENTINEL'), false)
  store.releaseSupervisorLease(started.supervisorLease, authority)
}))

test('N0 modules contain no process execution authority', () => {
  for (const file of ['launcher-supervisor-contract.js', 'launcher-supervisor-reducer.js', 'launcher-operation-store.js']) {
    const source = fs.readFileSync(path.join(ROOT, 'tools', 'home-control-launcher', file), 'utf8')
    assert.equal(source.includes("require('node:child_process')"), false, file)
    assert.equal(/\b(?:spawn|exec|fork|kill)Sync?\s*\(/u.test(source), false, file)
  }
})

test('turn-admission request validator accepts only the exact bounded request-local challenge envelope', () => {
  assert.equal(typeof contract.validateTurnAdmissionRequest, 'function')
  const request = {
    profile_id: REDUCED_PROFILE_ID,
    effective_config_sha256: 'e'.repeat(64),
    request_challenge: `tac_${'a'.repeat(32)}`
  }
  assert.deepEqual(contract.validateTurnAdmissionRequest(request), request)
  for (const malformed of [
    {},
    { ...request, extra: true },
    { ...request, profile_id: 'thought-core-v0' },
    { ...request, effective_config_sha256: 'bad' },
    { ...request, request_challenge: '' },
    { ...request, request_challenge: `tac_${'a'.repeat(33)}` },
    { ...request, request_challenge: 'PRIVATE_TOKEN_SENTINEL' }
  ]) {
    expectCode(
      () => contract.validateTurnAdmissionRequest(malformed),
      'turn_admission_request_invalid'
    )
  }
})
