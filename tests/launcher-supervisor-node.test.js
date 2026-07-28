'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const contract = require('../tools/home-control-launcher/launcher-supervisor-contract')
const reducer = require('../tools/home-control-launcher/launcher-supervisor-reducer')
const store = require('../tools/home-control-launcher/launcher-operation-store')

const authority = contract.loadAuthority(ROOT)
const OPERATION_ID = 'lop_node0001'
const event = (eventType, serviceId = undefined, operationId = OPERATION_ID) => ({ event_type: eventType, operation_id: operationId, ...(serviceId ? { service_id: serviceId } : {}) })
const workerRequest = (operation, serviceId = 'home_assistant_bridge', action = 'start', adapterClass = 'job_worker_service', deadlineMs = 60000) => ({
  schema_version: 'launcher_worker.v1', message_type: 'request', operation_id: operation.operation_id,
  graph_sha256: authority.identities.graphSha256, binding_sha256: authority.identities.bindingSha256,
  service_id: serviceId, action, adapter_class: adapterClass, expected_revision: operation.revision,
  deadline_ms: deadlineMs, worker_nonce: 'lw_0000000000000001'
})
const staleLockRecord = (ownerPid, ownerNonce = 'll_00000000000000000000000000000000') => ({
  schema_version: 'launcher_operation_lock.v1', owner_nonce: ownerNonce, owner_pid: ownerPid,
  created_at_ms: Date.now() - store.LOCK_STALE_MS - 1000
})

const expectCode = (action, code) => assert.throws(action, (error) => error instanceof contract.LauncherContractError && error.code === code)

const withRuntimeRoot = (action) => {
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'sword-launcher-node-n0-'))
  fs.writeFileSync(path.join(runtimeRoot, 'parent-sentinel.txt'), 'parent-unchanged', { mode: 0o600 })
  try { return action(runtimeRoot) } finally { fs.rmSync(runtimeRoot, { recursive: true, force: true }) }
}

const startLifecycle = () => {
  let operation = reducer.createOperation(OPERATION_ID, authority)
  for (const nextEvent of [event('preflight_started'), event('preflight_passed'), event('start_requested')]) {
    operation = reducer.reduce(operation, nextEvent, authority)
  }
  return operation
}

const fullReady = () => {
  let operation = startLifecycle()
  const specs = new Map(authority.graph.services.map((service) => [service.service_id, service]))
  for (const serviceId of authority.bindingDocument.binding.service_order) {
    const spec = specs.get(serviceId)
    if (spec.requirement === 'external') {
      operation = reducer.reduce(operation, event('external_ready', serviceId), authority)
    } else if (spec.requirement === 'optional') {
      operation = reducer.reduce(operation, event('optional_absent', serviceId), authority)
    } else {
      operation = reducer.reduce(operation, event('spawn_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('spawn_succeeded', serviceId), authority)
      operation = reducer.reduce(operation, event('service_ready', serviceId), authority)
    }
    reducer.validateSnapshot(operation, authority)
  }
  assert.equal(operation.phase, 'ready')
  return operation
}

test('authority is canonical, hash-bound, drift-checked, and LF-stable', () => {
  assert.equal(authority.bindingDocument.binding.text_hash_mode, 'utf8_lf_v1')
  assert.equal(authority.identities.bindingSha256, authority.bindingDocument.binding_sha256)
  const sample = '{\r\n  "ok": true\r\n}\r\n'
  assert.equal(contract.canonicalLfSha256(sample), contract.canonicalLfSha256(sample.replaceAll('\r\n', '\n')))
  const rendered = contract.renderBindingDocument({ graph: authority.graph, identities: authority.identities })
  assert.deepEqual(JSON.parse(rendered), authority.bindingDocument)
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
    schema_version: 'launcher_worker.v1', message_type: 'result', operation_id: startOperation.operation_id,
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
    schema_version: 'launcher_worker.v1', message_type: 'result', operation_id: OPERATION_ID,
    service_id: 'home_assistant_bridge', action: 'start', expected_revision: operation.revision,
    worker_nonce: 'lw_0000000000000001', result_class: 'accepted', ownership_class: 'matched',
    listener_class: 'matched', descendant_class: 'owned_active'
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
  const probeRequest = workerRequest(operation, 'home_assistant_bridge', 'probe', 'job_worker_service')
  const probeResult = { ...result, action: 'probe', result_class: 'ready', listener_class: 'matched' }
  assert.equal(reducer.workerResultToEvent(probeResult, operation, probeRequest, authority).event_type, 'service_ready')
})

test('external probe timeout becomes a bounded failure without fake external readiness', () => {
  let operation = startLifecycle()
  const externalSpec = authority.graph.services.find((service) => service.service_id === 'voicevox')
  const request = workerRequest(operation, 'voicevox', 'probe', 'external_probe_only', externalSpec.ready_deadline_ms)
  const result = {
    schema_version: 'launcher_worker.v1', message_type: 'result', operation_id: OPERATION_ID,
    service_id: 'voicevox', action: 'probe', expected_revision: operation.revision,
    worker_nonce: request.worker_nonce, result_class: 'readiness_timeout', ownership_class: 'not_applicable',
    listener_class: 'not_applicable', descendant_class: 'not_applicable'
  }
  const timeoutEvent = reducer.workerResultToEvent(result, operation, request, authority)
  assert.deepEqual(timeoutEvent, { event_type: 'readiness_timeout', operation_id: OPERATION_ID, service_id: 'voicevox' })
  operation = reducer.reduce(operation, timeoutEvent, authority)
  assert.equal(operation.phase, 'rolling_back')
  assert.equal(operation.reason, 'readiness_timeout')
  assert.equal(operation.rollback_required, true)
  assert.equal(operation.services.find((service) => service.service_id === 'voicevox').state, 'failed')
  reducer.validateSnapshot(operation, authority)
  operation = reducer.reduce(operation, event('rollback_completed'), authority)
  assert.equal(operation.phase, 'failed')
  assert.equal(operation.cleanup, 'clear')
  assert.equal(operation.services.find((service) => service.service_id === 'voicevox').state, 'failed')
  reducer.validateSnapshot(operation, authority)
})

test('all immutable reducer vectors execute and preserve valid snapshots', () => {
  const coverage = new Set()
  for (const vector of authority.reducerVectors.vectors) {
    let operation = reducer.createOperation(OPERATION_ID, authority)
    for (const vectorEvent of vector.events) {
      operation = reducer.reduce(operation, event(vectorEvent.event_type, vectorEvent.service_id, vector.event_operation_id || OPERATION_ID), authority)
      reducer.validateSnapshot(operation, authority)
    }
    assert.equal(operation.phase, vector.expected.phase, vector.vector_id)
    assert.equal(operation.reason, vector.expected.reason, vector.vector_id)
    assert.equal(operation.cleanup, vector.expected.cleanup, vector.vector_id)
    assert.deepEqual(operation.residue_service_ids, vector.expected.residue_service_ids, vector.vector_id)
    vector.coverage.forEach((item) => coverage.add(item))
  }
  for (const required of ['planned', 'preflight', 'prepared', 'dependency', 'ownership', 'pid_reuse', 'listener', 'deadline', 'rollback', 'recovery', 'stop', 'residue', 'optional_camera', 'external_voicevox', 'stale_no_write']) {
    assert.ok(coverage.has(required), required)
  }
})

test('full graph reaches Ready and repeats ten Start/Stop cycles', () => {
  for (let cycle = 0; cycle < 10; cycle += 1) {
    let operation = fullReady()
    operation = reducer.reduce(operation, event('stop_requested'), authority)
    for (const service of authority.graph.services.filter((item) => item.ownership === 'owned' && item.requirement !== 'optional')) {
      operation = reducer.reduce(operation, event('service_stopped', service.service_id), authority)
    }
    assert.equal(operation.phase, 'stopped')
    assert.equal(operation.cleanup, 'clear')
    assert.deepEqual(operation.residue_service_ids, [])
    assert.strictEqual(reducer.reduce(operation, event('stop_requested'), authority), operation)
  }
})

test('operation revision uses the same safe-integer ceiling as both schemas', () => {
  const maximum = { ...reducer.createOperation(OPERATION_ID, authority), revision: Number.MAX_SAFE_INTEGER }
  assert.equal(reducer.validateSnapshot(maximum, authority).revision, Number.MAX_SAFE_INTEGER)
  expectCode(() => reducer.startOperation(maximum, 'lop_node0002', authority), 'operation_revision_exhausted')
})

test('first failure survives rollback and recovery', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('spawn_failed', 'home_assistant_bridge'), authority)
  operation = reducer.reduce(operation, event('supervisor_crashed'), authority)
  assert.equal(operation.reason, 'spawn_failed')
  operation = reducer.reduce(operation, event('service_stopped', 'home_assistant_bridge'), authority)
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

test('store persists planned before preflight, joins duplicate Start, and preserves parent', () => withRuntimeRoot((runtimeRoot) => {
  const first = store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  assert.equal(first.operation.phase, 'planned')
  assert.equal(first.operation.revision, 0)
  assert.equal(first.joined_existing, false)
  const second = store.startAndPersist('lop_node0002', authority, runtimeRoot)
  assert.equal(second.joined_existing, true)
  assert.equal(second.operation.operation_id, OPERATION_ID)
  assert.equal(second.operation.revision, 1)
  assert.equal(fs.readFileSync(path.join(runtimeRoot, 'parent-sentinel.txt'), 'utf8'), 'parent-unchanged')
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  assert.deepEqual(fs.readdirSync(child).sort(), [store.RECORD_FILE])
  assert.equal(fs.readdirSync(child).some((name) => name.endsWith('.tmp')), false)
}))

test('store revision CAS rejects stale input without rewriting the record', () => withRuntimeRoot((runtimeRoot) => {
  let current = store.startAndPersist(OPERATION_ID, authority, runtimeRoot).operation
  current = store.reduceAndPersist(current, event('preflight_started'), authority, runtimeRoot)
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  const before = fs.readFileSync(recordPath)
  const stale = { ...current, revision: current.revision - 1 }
  expectCode(() => store.reduceAndPersist(stale, event('preflight_passed'), authority, runtimeRoot), 'operation_store_revision_conflict')
  assert.deepEqual(fs.readFileSync(recordPath), before)
}))

test('store recovers an exact stale owned lock and complete crash temp deterministically', () => withRuntimeRoot((runtimeRoot) => {
  let current = store.startAndPersist(OPERATION_ID, authority, runtimeRoot).operation
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
}))

test('stale lock held by the current live owner is never renamed or stolen', () => withRuntimeRoot((runtimeRoot) => {
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
}))

test('stale recovery fails closed for alive, PID reuse, unknown, denied, malformed, and observer errors', () => withRuntimeRoot((runtimeRoot) => {
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
}))

test('concurrent stale-lock replacement is restored unchanged and never reclaimed', () => withRuntimeRoot((runtimeRoot) => {
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
}))

test('recovery-file owner replacement during observation is preserved and never inferred from the old owner', () => withRuntimeRoot((runtimeRoot) => {
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
}))

test('store removes only a validated stale owned temp and preserves invalid recovery bytes', () => withRuntimeRoot((runtimeRoot) => {
  const current = store.startAndPersist(OPERATION_ID, authority, runtimeRoot).operation
  const child = path.join(runtimeRoot, store.STORE_DIRECTORY)
  const tempPath = path.join(child, store.TEMP_FILE)
  fs.writeFileSync(tempPath, `${JSON.stringify(current)}\n`)
  assert.equal(store.readOperation(authority, runtimeRoot).revision, current.revision)
  assert.equal(fs.existsSync(tempPath), false)
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
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
  const recordPath = path.join(runtimeRoot, store.STORE_DIRECTORY, store.RECORD_FILE)
  fs.writeFileSync(recordPath, Buffer.alloc(store.MAX_OPERATION_RECORD_BYTES + 1, 0x20))
  expectCode(() => store.readOperation(authority, runtimeRoot), 'operation_store_record_oversized')
  assert.equal(fs.statSync(recordPath).size, store.MAX_OPERATION_RECORD_BYTES + 1)
}))

test('store rejects lock collision and foreign content without touching it', () => withRuntimeRoot((runtimeRoot) => {
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
  store.startAndPersist(OPERATION_ID, authority, runtimeRoot)
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
}))

test('N0 modules contain no process execution authority', () => {
  for (const file of ['launcher-supervisor-contract.js', 'launcher-supervisor-reducer.js', 'launcher-operation-store.js']) {
    const source = fs.readFileSync(path.join(ROOT, 'tools', 'home-control-launcher', file), 'utf8')
    assert.equal(source.includes("require('node:child_process')"), false, file)
    assert.equal(/\b(?:spawn|exec|fork|kill)Sync?\s*\(/u.test(source), false, file)
  }
})
