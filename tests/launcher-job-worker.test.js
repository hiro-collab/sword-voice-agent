'use strict'

const assert = require('node:assert/strict')
const { spawn: spawnChild } = require('node:child_process')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { PassThrough, Writable } = require('node:stream')
const test = require('node:test')

const contract = require('../tools/home-control-launcher/launcher-supervisor-contract')
const { loadAuthority } = contract
const rawReducer = require('../tools/home-control-launcher/launcher-supervisor-reducer')
const rawStore = require('../tools/home-control-launcher/launcher-operation-store')
const {
  LauncherJobWorkerClient: RawLauncherJobWorkerClient,
  LauncherJobWorkerError,
  MAX_WORKER_LINE_BYTES,
  PowerShellJsonLineTransport: RawPowerShellJsonLineTransport,
  createOwnerLivenessObserver
} = require('../tools/home-control-launcher/launcher-job-worker-client')

const ROOT = path.resolve(__dirname, '..')
const authority = loadAuthority(ROOT)
const CONFIG_IDENTITY = Object.freeze({
  profile_id: authority.graph.profile_id,
  effective_config_sha256: 'e'.repeat(64),
  camera_policy: 'camera_excluded_by_profile'
})
const reducer = {
  ...rawReducer,
  createOperation: (operationId, suppliedAuthority, generation = 1) =>
    rawReducer.createOperation(operationId, suppliedAuthority, CONFIG_IDENTITY, generation),
  startOperation: (active, operationId, suppliedAuthority, generation = 1) =>
    rawReducer.startOperation(active, operationId, suppliedAuthority, CONFIG_IDENTITY, generation)
}
const store = {
  ...rawStore,
  startAndPersist: (operationId, suppliedAuthority, root, observer) =>
    rawStore.startAndPersist(operationId, CONFIG_IDENTITY, suppliedAuthority, root, observer)
}
const OPERATION_ID = 'lop_n1synthetic01'
const PRIVATE_RUNTIME_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'sword-launcher-worker-lease-'))
const SEED_START = store.startAndPersist('lop_n1seed0001', authority, PRIVATE_RUNTIME_ROOT)
let SEED_OPERATION = SEED_START.operation
SEED_OPERATION = store.reduceAndPersist(SEED_OPERATION, { event_type: 'preflight_started', operation_id: SEED_OPERATION.operation_id }, authority, PRIVATE_RUNTIME_ROOT)
SEED_OPERATION = store.reduceAndPersist(SEED_OPERATION, { event_type: 'preflight_failed', operation_id: SEED_OPERATION.operation_id }, authority, PRIVATE_RUNTIME_ROOT)
store.releaseSupervisorLease(SEED_START.supervisorLease, authority)
const STARTED = store.startAndPersist(OPERATION_ID, authority, PRIVATE_RUNTIME_ROOT)
const STARTED_OPERATION = STARTED.operation
const SUPERVISOR_LEASE = STARTED.supervisorLease
const LEASE_BINDING = store.getSupervisorLeaseBinding(SUPERVISOR_LEASE, authority)

class LauncherJobWorkerClient extends RawLauncherJobWorkerClient {
  constructor ({ authority: suppliedAuthority, transport }) {
    transport.authorityLeaseProof = LEASE_BINDING.authority_lease_proof
    super({ authority: suppliedAuthority, transport, supervisorLease: SUPERVISOR_LEASE })
  }
}

class PowerShellJsonLineTransport extends RawPowerShellJsonLineTransport {
  constructor (options) {
    super({ ...options, authority, supervisorLease: SUPERVISOR_LEASE })
  }
}

test.after(() => {
  store.releaseSupervisorLease(SUPERVISOR_LEASE, authority)
  fs.rmSync(PRIVATE_RUNTIME_ROOT, { recursive: true, force: true })
})
const dispatchId = (serviceId, action, sequence = 1) => `ld_${Buffer.from(`${serviceId}:${action}:${sequence}`).toString('hex').slice(0, 32).padEnd(32, '0')}`
const actionForEvent = (eventType) => eventType.startsWith('spawn_') ? 'start' :
  ['probe_requested', 'probe_transport_ready', 'semantic_probe_completed', 'optional_absent', 'readiness_timeout'].includes(eventType) ? 'probe' :
    ['stop_dispatch_requested', 'service_stopped', 'stop_failed'].includes(eventType) ? 'stop' : null
const event = (eventType, serviceId) => ({
  event_type: eventType,
  operation_id: OPERATION_ID,
  ...(serviceId ? { service_id: serviceId } : {}),
  ...(serviceId && actionForEvent(eventType) ? { dispatch_id: dispatchId(serviceId, actionForEvent(eventType)), ...(eventType.endsWith('_requested') ? { action: actionForEvent(eventType) } : {}) } : {})
})

const startLifecycle = () => {
  let operation = reducer.createOperation(OPERATION_ID, authority)
  for (const next of [event('preflight_started'), event('preflight_passed'), event('start_requested')]) {
    operation = reducer.reduce(operation, next, authority)
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
    config_sha256: '0'.repeat(64),
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

const stoppingLifecycle = () => {
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
    }
    else {
      operation = reducer.reduce(operation, event('spawn_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('spawn_succeeded', serviceId), authority)
      operation = reducer.reduce(operation, event('probe_requested', serviceId), authority)
      operation = reducer.reduce(operation, event('probe_transport_ready', serviceId), authority)
      operation = reducer.reduce(operation, {
        ...event('semantic_probe_completed', serviceId),
        probe_result: boundProbeResult(operation, serviceId)
      }, authority)
    }
  }
  return reducer.reduce(operation, event('stop_requested'), authority)
}

let nonceSequence = 0
const requestFor = (serviceId, action, revision = 1) => {
  const spec = authority.graph.services.find((service) => service.service_id === serviceId)
  assert.ok(spec)
  const adapterClass = action === 'start'
    ? spec.start.adapter_id
    : action === 'stop'
      ? spec.stop.adapter_id
      : spec.ownership === 'external' ? 'external_probe_only' : 'job_worker_service'
  const deadlineMs = action === 'stop' ? spec.stop.graceful_timeout_ms : spec.ready_deadline_ms
  nonceSequence++
  return {
    schema_version: 'launcher_worker.v2',
    message_type: 'request',
    operation_id: OPERATION_ID,
    supervisor_generation: LEASE_BINDING.supervisor_generation,
    authority_lease_proof: LEASE_BINDING.authority_lease_proof,
    dispatch_id: `ld_n1synthetic${String(nonceSequence).padStart(8, '0')}`,
    graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    service_id: serviceId,
    action,
    adapter_class: adapterClass,
    expected_revision: revision,
    deadline_ms: deadlineMs,
    worker_nonce: `lw_n1synthetic${String(nonceSequence).padStart(8, '0')}`
  }
}

const resultFor = (request, values = {}) => ({
  schema_version: 'launcher_worker.v2',
  message_type: 'result',
  operation_id: request.operation_id,
  supervisor_generation: request.supervisor_generation,
  authority_lease_proof: request.authority_lease_proof,
  dispatch_id: request.dispatch_id,
  service_id: request.service_id,
  action: request.action,
  expected_revision: request.expected_revision,
  worker_nonce: request.worker_nonce,
  result_class: 'ready',
  ownership_class: 'matched',
  listener_class: 'matched',
  descendant_class: 'owned_active',
  ...values
})

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))

const waitUntil = async (predicate, code, timeoutMs = 10000) => {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await sleep(50)
  }
  assert.fail(code)
}

const pidAlive = (pid) => {
  if (!Number.isSafeInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error && error.code === 'EPERM'
  }
}

const readOwnedPid = (markerPath) => {
  if (!fs.existsSync(markerPath)) return null
  const value = Number(fs.readFileSync(markerPath, 'utf8').trim())
  return Number.isSafeInteger(value) && value > 0 ? value : null
}

const waitForOwnedPid = async (markerPath) => {
  await waitUntil(() => readOwnedPid(markerPath) !== null, 'owned_pid_marker_missing')
  return readOwnedPid(markerPath)
}

const waitForPidExit = async (pid) => waitUntil(() => !pidAlive(pid), 'owned_pid_residue')

const canConnect = (port) => new Promise((resolve) => {
  const socket = net.createConnection({ host: '127.0.0.1', port })
  let completed = false
  const finish = (value) => {
    if (completed) return
    completed = true
    socket.destroy()
    resolve(value)
  }
  socket.setTimeout(250, () => finish(false))
  socket.once('connect', () => finish(true))
  socket.once('error', () => finish(false))
})

const assertPortFree = async (port) => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.once('error', reject)
  server.listen(port, '127.0.0.1', () => server.close(resolve))
})

class SyntheticJobTransport {
  constructor (scenarios = {}) {
    this.scenarios = { ...scenarios }
    this.jobs = new Map()
    this.externalStopActions = 0
    this.closed = false
  }

  async exchange (line) {
    if (this.closed) throw new Error('closed')
    const request = JSON.parse(line)
    const scenario = this.scenarios[request.service_id] || 'ready'
    if (scenario === 'worker_crash') throw new Error('synthetic_crash')
    if (request.action === 'start') {
      if (scenario === 'optional_absent') {
        return JSON.stringify(resultFor(request, {
          result_class: 'spawn_failed', ownership_class: 'not_applicable',
          listener_class: 'not_applicable', descendant_class: 'owned_clear'
        }))
      }
      if (scenario === 'foreign_listener') {
        return JSON.stringify(resultFor(request, {
          result_class: 'listener_mismatch', ownership_class: 'mismatch',
          listener_class: 'mismatch', descendant_class: 'foreign'
        }))
      }
      if (scenario === 'early_exit') {
        return JSON.stringify(resultFor(request, {
          result_class: 'early_exit', listener_class: 'unknown', descendant_class: 'owned_clear'
        }))
      }
      this.jobs.set(request.service_id, { descendants: scenario === 'wrapper_tree' ? 3 : 1 })
      return JSON.stringify(resultFor(request, {
        result_class: 'accepted', listener_class: 'not_applicable'
      }))
    }
    if (request.action === 'probe') {
      if (scenario === 'optional_absent') {
        return JSON.stringify(resultFor(request, {
          result_class: 'optional_absent', ownership_class: 'not_applicable',
          listener_class: 'not_applicable', descendant_class: 'owned_clear'
        }))
      }
      if (request.service_id === 'voicevox') {
        return JSON.stringify(resultFor(request, {
          result_class: 'external_ready', ownership_class: 'not_applicable',
          listener_class: 'matched', descendant_class: 'not_applicable'
        }))
      }
      if (scenario === 'ready_timeout') {
        return JSON.stringify(resultFor(request, {
          result_class: 'readiness_timeout', listener_class: 'unknown'
        }))
      }
      return JSON.stringify(resultFor(request, this.jobs.has(request.service_id)
        ? {}
        : { result_class: 'early_exit', ownership_class: 'unknown', listener_class: 'unknown', descendant_class: 'owned_clear' }))
    }
    if (request.service_id === 'voicevox') {
      this.externalStopActions++
      return JSON.stringify(resultFor(request, {
        result_class: 'stopped', ownership_class: 'not_applicable',
        listener_class: 'not_applicable', descendant_class: 'not_applicable'
      }))
    }
    if (scenario === 'stop_timeout') {
      return JSON.stringify(resultFor(request, {
        result_class: 'stop_failed', listener_class: 'unknown', descendant_class: 'owned_active'
      }))
    }
    this.jobs.delete(request.service_id)
    return JSON.stringify(resultFor(request, {
      result_class: 'stopped', descendant_class: 'owned_clear'
    }))
  }

  async close () {
    this.jobs.clear()
    this.closed = true
  }
}

test('direct server and wrapper-child-grandchild listener remain one owned lifecycle', async () => {
  const transport = new SyntheticJobTransport({ thought_core_api: 'wrapper_tree' })
  const client = new LauncherJobWorkerClient({ authority, transport })
  const direct = await client.execute(requestFor('home_assistant_bridge', 'start'))
  assert.equal(direct.result_class, 'accepted')
  assert.equal((await client.execute(requestFor('home_assistant_bridge', 'probe', 2))).result_class, 'ready')
  const wrapper = await client.execute(requestFor('thought_core_api', 'start', 2))
  assert.equal(wrapper.result_class, 'accepted')
  assert.equal(transport.jobs.get('thought_core_api').descendants, 3)
  assert.equal((await client.execute(requestFor('thought_core_api', 'probe', 3))).result_class, 'ready')
  assert.equal((await client.execute(requestFor('thought_core_api', 'stop', 3))).descendant_class, 'owned_clear')
  assert.equal((await client.execute(requestFor('home_assistant_bridge', 'stop', 4))).result_class, 'stopped')
  await client.close()
  assert.equal(transport.jobs.size, 0)
})

test('optional camera absence and external VOICEVOX remain non-owning boundaries', async () => {
  const transport = new SyntheticJobTransport({ mediapipe_camera_hub_stack: 'optional_absent' })
  const client = new LauncherJobWorkerClient({ authority, transport })
  const camera = await client.execute(requestFor('mediapipe_camera_hub_stack', 'probe'))
  assert.equal(camera.result_class, 'optional_absent')
  assert.equal(camera.descendant_class, 'owned_clear')
  const external = await client.execute(requestFor('voicevox', 'probe', 2))
  assert.equal(external.result_class, 'external_ready')
  assert.equal(external.ownership_class, 'not_applicable')
  const stopped = await client.execute(requestFor('voicevox', 'stop', 3))
  assert.equal(stopped.result_class, 'stopped')
  assert.equal(transport.externalStopActions, 1)
  assert.equal(transport.jobs.has('voicevox'), false)
  await client.close()
})

test('foreign listener, early exit, readiness timeout, stop timeout, and worker crash stay fixed', async () => {
  for (const [scenario, expected] of [
    ['foreign_listener', 'listener_mismatch'],
    ['early_exit', 'early_exit']
  ]) {
    const transport = new SyntheticJobTransport({ home_assistant_bridge: scenario })
    const client = new LauncherJobWorkerClient({ authority, transport })
    assert.equal((await client.execute(requestFor('home_assistant_bridge', 'start'))).result_class, expected)
    await client.close()
  }
  const readyTimeoutTransport = new SyntheticJobTransport({ home_assistant_bridge: 'ready_timeout' })
  const readyTimeoutClient = new LauncherJobWorkerClient({ authority, transport: readyTimeoutTransport })
  assert.equal((await readyTimeoutClient.execute(requestFor('home_assistant_bridge', 'start'))).result_class, 'accepted')
  assert.equal((await readyTimeoutClient.execute(requestFor('home_assistant_bridge', 'probe'))).result_class, 'readiness_timeout')
  await readyTimeoutClient.close()
  const stopTransport = new SyntheticJobTransport({ home_assistant_bridge: 'stop_timeout' })
  const stopClient = new LauncherJobWorkerClient({ authority, transport: stopTransport })
  assert.equal((await stopClient.execute(requestFor('home_assistant_bridge', 'start'))).result_class, 'accepted')
  assert.equal((await stopClient.execute(requestFor('home_assistant_bridge', 'stop'))).result_class, 'stop_failed')
  await stopClient.close()
  const crashClient = new LauncherJobWorkerClient({ authority, transport: new SyntheticJobTransport({ home_assistant_bridge: 'worker_crash' }) })
  await assert.rejects(crashClient.execute(requestFor('home_assistant_bridge', 'start')), (error) => error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed')
  await crashClient.close()
})

test('ten repeated Start Stop cycles are idempotent and clean', async () => {
  const transport = new SyntheticJobTransport()
  const client = new LauncherJobWorkerClient({ authority, transport })
  for (let index = 0; index < 10; index++) {
    assert.equal((await client.execute(requestFor('environment_state_server', 'start', index * 3))).result_class, 'accepted')
    assert.equal((await client.execute(requestFor('environment_state_server', 'probe', index * 3 + 1))).result_class, 'ready')
    assert.equal((await client.execute(requestFor('environment_state_server', 'stop', index * 3 + 2))).result_class, 'stopped')
    assert.equal(transport.jobs.size, 0)
  }
  await client.close()
})

test('owner liveness distinguishes absent alive reused and unknown without a signal seam', () => {
  const owner = { owner_pid: 100, owner_nonce: 'lock_nonce', created_at_ms: 5000 }
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => ({ status: 'absent' }) })(owner), 'absent')
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => ({ status: 'alive', creation_time_ms: 4000 }) })(owner), 'alive')
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => ({ status: 'alive', creation_time_ms: 6000 }) })(owner), 'pid_reused')
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => ({ status: 'access_denied' }) })(owner), 'access_denied')
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => ({ status: 'unknown' }) })(owner), 'unknown')
  assert.equal(createOwnerLivenessObserver({ inspectProcess: () => { throw new Error('PRIVATE_SENTINEL') } })(owner), 'unknown')
})

test('lease-bound client rejects old generation unknown service and forged same-generation proof before transport mutation', async () => {
  let exchanges = 0
  const transport = {
    authorityLeaseProof: LEASE_BINDING.authority_lease_proof,
    exchange: async (line) => { exchanges++; return JSON.stringify(resultFor(JSON.parse(line))) },
    close: async () => {}
  }
  const client = new RawLauncherJobWorkerClient({ authority, transport, supervisorLease: SUPERVISOR_LEASE })
  const oldGeneration = { ...requestFor('home_assistant_bridge', 'start'), supervisor_generation: LEASE_BINDING.supervisor_generation - 1 }
  await assert.rejects(client.execute(oldGeneration), (error) => error instanceof LauncherJobWorkerError && error.code === 'worker_response_invalid')
  const unknownService = {
    ...requestFor('home_assistant_bridge', 'start'), service_id: 'unknown_service', adapter_class: 'job_worker_service'
  }
  await assert.rejects(client.execute(unknownService), (error) => error instanceof LauncherJobWorkerError && error.code === 'worker_response_invalid')
  assert.equal(exchanges, 0)
  assert.throws(() => new RawLauncherJobWorkerClient({
    authority,
    transport: { ...transport, authorityLeaseProof: `lp_${'f'.repeat(64)}` },
    supervisorLease: SUPERVISOR_LEASE
  }), (error) => error instanceof LauncherJobWorkerError && error.code === 'worker_configuration_invalid')
  assert.equal((await client.execute(requestFor('home_assistant_bridge', 'start'))).result_class, 'ready')
  assert.equal(exchanges, 1)
  await client.close()
})

test('PowerShell worker resolves service membership and adapter before latching its generation lease fence', () => {
  const source = fs.readFileSync(path.join(ROOT, 'ops', 'scripts', 'home-control-stack', 'launcher-job-worker.ps1'), 'utf8')
  const resolveIndex = source.indexOf('$plan = Resolve-LauncherServicePlan')
  const latchIndex = source.indexOf('$ActiveSupervisorGeneration = [long]$request.supervisor_generation')
  const dispatchIndex = source.indexOf('$SeenDispatches[[string]$request.dispatch_id] = $true')
  assert.ok(resolveIndex > 0 && latchIndex > resolveIndex && dispatchIndex > latchIndex)
  assert.match(source, /SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF/u)
  assert.match(source, /\[string\]\$Request\.authority_lease_proof -cne \$ExpectedAuthorityLeaseProof/u)
})

test('correlation, malformed, oversize, and private sentinel responses fail closed', async () => {
  const request = requestFor('aituber_kit', 'start')
  for (const [response, code] of [
    ['not-json', 'worker_response_invalid'],
    ['x'.repeat(MAX_WORKER_LINE_BYTES + 1), 'worker_response_oversized'],
    [JSON.stringify({ ...resultFor(request), supervisor_generation: request.supervisor_generation + 1 }), 'worker_response_mismatch'],
    [JSON.stringify({ ...resultFor(request), dispatch_id: 'ld_ffffffffffffffff' }), 'worker_response_mismatch'],
    [JSON.stringify({ ...resultFor(request), worker_nonce: 'lw_wrongnonce000000' }), 'worker_response_mismatch'],
    [JSON.stringify({ ...resultFor(request), raw_error: 'PRIVATE_SENTINEL' }), 'worker_response_invalid']
  ]) {
    const client = new LauncherJobWorkerClient({ authority, transport: { exchange: async () => response, close: async () => {} } })
    await assert.rejects(client.execute(request), (error) => error instanceof LauncherJobWorkerError && error.code === code)
    await client.close()
  }
})

test('worker-shaped results map only to transport readiness before a correlated semantic result', () => {
  let operation = startLifecycle()
  operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
  const startRequest = { ...requestFor('home_assistant_bridge', 'start', operation.revision), supervisor_generation: operation.supervisor_generation, dispatch_id: operation.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id }
  const accepted = resultFor(startRequest, {
    result_class: 'accepted', listener_class: 'not_applicable', descendant_class: 'owned_active'
  })
  assert.equal(reducer.workerResultToEvent(accepted, operation, startRequest, authority).event_type, 'spawn_succeeded')

  operation = reducer.reduce(operation, reducer.workerResultToEvent(accepted, operation, startRequest, authority), authority)
  operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
  const probeRequest = { ...requestFor('home_assistant_bridge', 'probe', operation.revision), supervisor_generation: operation.supervisor_generation, dispatch_id: operation.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id }
  const noPortReady = resultFor(probeRequest, { listener_class: 'matched' })
  const transportReady = reducer.workerResultToEvent(noPortReady, operation, probeRequest, authority)
  assert.equal(transportReady.event_type, 'probe_transport_ready')
  operation = reducer.reduce(operation, transportReady, authority)
  let service = operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge')
  assert.equal(service.state, 'starting')
  assert.equal(service.probe_status, 'transport_ready')
  assert.equal(service.last_probe_result, null)
  const homeProbeResult = boundProbeResult(operation, 'home_assistant_bridge')
  operation = reducer.reduce(operation, {
    ...event('semantic_probe_completed', 'home_assistant_bridge'),
    probe_result: homeProbeResult
  }, authority)
  service = operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge')
  assert.equal(service.state, 'ready')
  assert.equal(service.probe_status, 'ready')
  assert.deepEqual(service.last_probe_result, homeProbeResult)

  const externalSpec = authority.graph.services.find((service) => service.service_id === 'voicevox')
  let externalOperation = startLifecycle()
  externalOperation = reducer.reduce(externalOperation, event('probe_requested', 'voicevox'), authority)
  const externalRequest = { ...requestFor('voicevox', 'probe', externalOperation.revision), supervisor_generation: externalOperation.supervisor_generation, dispatch_id: externalOperation.services.find((service) => service.service_id === 'voicevox').pending_dispatch_id }
  assert.equal(externalRequest.deadline_ms, externalSpec.ready_deadline_ms)
  const externalReady = resultFor(externalRequest, {
    result_class: 'external_ready', ownership_class: 'not_applicable',
    listener_class: 'matched', descendant_class: 'not_applicable'
  })
  const externalTransportReady = reducer.workerResultToEvent(externalReady, externalOperation, externalRequest, authority)
  assert.equal(externalTransportReady.event_type, 'probe_transport_ready')
  externalOperation = reducer.reduce(externalOperation, externalTransportReady, authority)
  let externalService = externalOperation.services.find((candidate) => candidate.service_id === 'voicevox')
  assert.equal(externalService.state, 'pending')
  assert.equal(externalService.probe_status, 'transport_ready')
  const voicevoxProbeResult = boundProbeResult(externalOperation, 'voicevox')
  externalOperation = reducer.reduce(externalOperation, {
    ...event('semantic_probe_completed', 'voicevox'),
    probe_result: voicevoxProbeResult
  }, authority)
  externalService = externalOperation.services.find((candidate) => candidate.service_id === 'voicevox')
  assert.equal(externalService.state, 'external_ready')
  assert.equal(externalService.probe_status, 'ready')
  assert.deepEqual(externalService.last_probe_result, voicevoxProbeResult)

  let stopping = stoppingLifecycle()
  stopping = reducer.reduce(stopping, event('stop_dispatch_requested', 'home_assistant_bridge'), authority)
  const stopRequest = { ...requestFor('home_assistant_bridge', 'stop', stopping.revision), supervisor_generation: stopping.supervisor_generation, dispatch_id: stopping.services.find((service) => service.service_id === 'home_assistant_bridge').pending_dispatch_id }
  const stopped = resultFor(stopRequest, { result_class: 'stopped', descendant_class: 'owned_clear' })
  const stoppedEvent = reducer.workerResultToEvent(stopped, stopping, stopRequest, authority)
  assert.equal(stoppedEvent.event_type, 'service_stopped')
  assert.notEqual(reducer.reduce(stopping, stoppedEvent, authority).reason, 'invalid_event')
  const stopFailed = resultFor(stopRequest, {
    result_class: 'stop_failed', ownership_class: 'unknown',
    listener_class: 'unknown', descendant_class: 'unknown'
  })
  assert.equal(reducer.workerResultToEvent(stopFailed, stopping, stopRequest, authority).event_type, 'stop_failed')
})

test('semantic readiness rejects mismatched generation, dispatch, revision, and private fields', () => {
  const prepare = () => {
    let operation = startLifecycle()
    operation = reducer.reduce(operation, event('spawn_requested', 'home_assistant_bridge'), authority)
    operation = reducer.reduce(operation, event('spawn_succeeded', 'home_assistant_bridge'), authority)
    operation = reducer.reduce(operation, event('probe_requested', 'home_assistant_bridge'), authority)
    return reducer.reduce(operation, event('probe_transport_ready', 'home_assistant_bridge'), authority)
  }
  const accepted = boundProbeResult(prepare(), 'home_assistant_bridge')
  for (const mutation of [
    { supervisor_generation: accepted.supervisor_generation + 1 },
    { dispatch_id: 'ld_ffffffffffffffff' },
    { expected_revision: accepted.expected_revision + 1 },
    { raw_body: 'PRIVATE_SENTINEL' }
  ]) {
    const operation = reducer.reduce(prepare(), {
      ...event('semantic_probe_completed', 'home_assistant_bridge'),
      probe_result: { ...accepted, ...mutation }
    }, authority)
    assert.equal(operation.reason, 'invalid_event')
    assert.equal(operation.phase, 'waiting_ready')
    const service = operation.services.find((candidate) => candidate.service_id === 'home_assistant_bridge')
    assert.equal(service.state, 'starting')
    assert.equal(service.probe_status, 'transport_ready')
    assert.equal(service.last_probe_result, null)
  }
})

test('PowerShell transport uses fixed no-shell invocation, one inflight request, and bounded cleanup', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-'))
  const planPath = path.join(root, 'private-plan.json')
  fs.writeFileSync(planPath, '{}', { encoding: 'utf8', mode: 0o600 })
  const invocations = []
  const children = []
  const spawnImpl = (file, args, options) => {
    const child = new EventEmitter()
    child.exitCode = null
    child.stdout = new PassThrough()
    child.stdin = new Writable({
      write (chunk, encoding, callback) {
        const request = JSON.parse(chunk.toString('utf8'))
        queueMicrotask(() => child.stdout.write(`${JSON.stringify(resultFor(request, {
          result_class: 'accepted', listener_class: 'not_applicable'
        }))}\n`))
        callback()
      },
      final (callback) {
        queueMicrotask(() => {
          child.exitCode = 0
          child.emit('exit', 0)
        })
        callback()
      }
    })
    child.kill = () => {
      child.exitCode = 1
      child.emit('exit', 1)
      return true
    }
    invocations.push({ file, args, options })
    children.push(child)
    return child
  }
  try {
    assert.throws(() => new PowerShellJsonLineTransport({
      repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: 'pwsh.exe', spawnImpl
    }), (error) => error instanceof LauncherJobWorkerError && error.code === 'worker_configuration_invalid')
    const transport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: process.execPath, spawnImpl
    })
    const client = new LauncherJobWorkerClient({ authority, transport })
    assert.equal((await client.execute(requestFor('touchdesigner_control_gui', 'start'))).result_class, 'accepted')
    await client.close()
    assert.equal(invocations.length, 1)
    assert.equal(invocations[0].file, process.execPath)
    assert.equal(invocations[0].options.shell, false)
    assert.deepEqual(invocations[0].options.stdio, ['pipe', 'pipe', 'ignore'])
    assert.equal(invocations[0].options.env.SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE, planPath)
    assert.match(invocations[0].args.join(' '), /launcher-job-worker\.ps1/u)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('PowerShell transport reports cleanup failure when its exact child does not exit', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-close-'))
  const planPath = path.join(root, 'private-plan.json')
  fs.writeFileSync(planPath, '{}', { encoding: 'utf8', mode: 0o600 })
  const spawnImpl = () => {
    const child = new EventEmitter()
    child.exitCode = null
    child.stdout = new PassThrough()
    child.stdin = new Writable({ write (chunk, encoding, callback) { callback() } })
    child.kill = () => true
    return child
  }
  try {
    const transport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT,
      privatePlanPath: planPath,
      powershellPath: process.execPath,
      spawnImpl,
      closeTimeoutMs: 100
    })
    transport.start()
    await assert.rejects(transport.close(), (error) => (
      error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed'
    ))
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('PowerShell transport fails the next exchange after the worker exits', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-exit-'))
  const planPath = path.join(root, 'private-plan.json')
  fs.writeFileSync(planPath, '{}', { encoding: 'utf8', mode: 0o600 })
  const spawnImpl = () => {
    const child = new EventEmitter()
    child.exitCode = null
    child.stdout = new PassThrough()
    child.stdin = new Writable({
      write (chunk, encoding, callback) {
        const request = JSON.parse(chunk.toString('utf8'))
        queueMicrotask(() => {
          child.stdout.write(`${JSON.stringify(resultFor(request, {
            result_class: 'accepted', listener_class: 'not_applicable'
          }))}\n`)
          child.exitCode = 1
          child.emit('exit', 1)
        })
        callback()
      }
    })
    child.kill = () => false
    return child
  }
  try {
    const transport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: process.execPath, spawnImpl
    })
    const client = new LauncherJobWorkerClient({ authority, transport })
    assert.equal((await client.execute(requestFor('aituber_kit', 'start'))).result_class, 'accepted')
    await assert.rejects(client.execute(requestFor('aituber_kit', 'probe')), (error) => (
      error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed'
    ))
    await client.close()
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('transport grace retains a bounded worker result and timeout terminalizes the worker', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-timeout-'))
  const planPath = path.join(root, 'private-plan.json')
  fs.writeFileSync(planPath, '{}', { encoding: 'utf8', mode: 0o600 })
  const makeChild = (respond) => {
    const child = new EventEmitter()
    child.exitCode = null
    child.stdout = new PassThrough()
    child.stdin = new Writable({
      write (chunk, encoding, callback) {
        if (respond) setTimeout(() => child.stdout.write('bounded\n'), 25)
        callback()
      },
      final (callback) {
        child.exitCode = 0
        queueMicrotask(() => child.emit('exit', 0))
        callback()
      }
    })
    child.kill = () => {
      child.exitCode = 1
      child.emit('exit', 1)
      return true
    }
    return child
  }
  try {
    const graceTransport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: process.execPath,
      spawnImpl: () => makeChild(true), responseGraceMs: 100
    })
    assert.equal(await graceTransport.exchange('{}', 0), 'bounded')
    await graceTransport.close()

    const timeoutTransport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: process.execPath,
      spawnImpl: () => makeChild(false), responseGraceMs: 100
    })
    await assert.rejects(timeoutTransport.exchange('{}', 0), (error) => (
      error instanceof LauncherJobWorkerError && error.code === 'worker_transport_timeout'
    ))
    await assert.rejects(timeoutTransport.exchange('{}', 0), (error) => (
      error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed'
    ))
    await timeoutTransport.close()
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('stdin callback error and synchronous write failure terminalize the worker before a second exchange', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-write-'))
  const planPath = path.join(root, 'private-plan.json')
  fs.writeFileSync(planPath, '{}', { encoding: 'utf8', mode: 0o600 })
  const makeChild = (mode) => {
    const child = new EventEmitter()
    child.exitCode = null
    child.stdout = new PassThrough()
    if (mode === 'callback') {
      child.stdin = new Writable({
        write (chunk, encoding, callback) { callback(new Error('PRIVATE_SENTINEL')) }
      })
    } else {
      child.stdin = new EventEmitter()
      child.stdin.write = () => { throw new Error('PRIVATE_SENTINEL') }
      child.stdin.end = () => {}
    }
    child.kill = () => {
      child.exitCode = 1
      queueMicrotask(() => child.emit('exit', 1))
      return true
    }
    return child
  }
  try {
    for (const mode of ['callback', 'throw']) {
      const transport = new PowerShellJsonLineTransport({
        repositoryRoot: ROOT, privatePlanPath: planPath, powershellPath: process.execPath,
        spawnImpl: () => makeChild(mode)
      })
      await assert.rejects(transport.exchange('{}', 0), (error) => (
        error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed'
      ))
      await assert.rejects(transport.exchange('{}', 0), (error) => (
        error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed'
      ))
      await transport.close()
    }
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('actual Windows Job worker contains descendants, survives foreign listeners, and cleans ten cycles', { timeout: 120000 }, async (context) => {
  if (process.platform !== 'win32') return context.skip('windows_job_object_only')
  if (process.env.SWORD_LAUNCHER_N1_ACTUAL_TEST !== '1') return context.skip('explicit_normal_user_gate_required')
  const powershellPath = 'C:\\Program Files\\PowerShell\\7\\pwsh.exe'
  assert.equal(fs.existsSync(powershellPath), true)
  const port = 8788
  await assertPortFree(port)
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-job-worker-live-'))
  const transports = []
  let sentinel = null
  const markerPaths = []
  const explicitEnvironment = {
    SYSTEMROOT: process.env.SystemRoot || process.env.SYSTEMROOT || 'C:\\Windows',
    TEMP: root,
    TMP: root
  }
  const writeScript = (name, source) => {
    const file = path.join(root, name)
    fs.writeFileSync(file, source, 'utf8')
    return file
  }
  const directMarker = path.join(root, 'direct.pid')
  const wrapperMarker = path.join(root, 'wrapper.pid')
  const childMarker = path.join(root, 'child.pid')
  const grandchildMarker = path.join(root, 'grandchild.pid')
  const sentinelMarker = path.join(root, 'sentinel.pid')
  markerPaths.push(directMarker, wrapperMarker, childMarker, grandchildMarker, sentinelMarker)
  const serverSource = (marker) => `'use strict'\n` +
    `require('node:fs').writeFileSync(${JSON.stringify(marker)}, String(process.pid))\n` +
    `require('node:http').createServer((request,response)=>{response.statusCode=200;response.end('ready')}).listen(${port},'127.0.0.1')\n`
  const directScript = writeScript('direct.js', serverSource(directMarker))
  const grandchildScript = writeScript('grandchild.js', serverSource(grandchildMarker))
  const childScript = writeScript('child.js', `'use strict'\n` +
    `require('node:fs').writeFileSync(${JSON.stringify(childMarker)}, String(process.pid))\n` +
    `require('node:child_process').spawn(process.execPath,[${JSON.stringify(grandchildScript)}],{cwd:${JSON.stringify(root)},env:process.env,windowsHide:true,stdio:'ignore'})\n` +
    'setInterval(()=>{},1000)\n')
  const wrapperScript = writeScript('wrapper.js', `'use strict'\n` +
    `require('node:fs').writeFileSync(${JSON.stringify(wrapperMarker)}, String(process.pid))\n` +
    `require('node:child_process').spawn(process.execPath,[${JSON.stringify(childScript)}],{cwd:${JSON.stringify(root)},env:process.env,windowsHide:true,stdio:'ignore'})\n` +
    'setInterval(()=>{},1000)\n')
  const sentinelScript = writeScript('sentinel.js', serverSource(sentinelMarker))
  const executableByService = {
    home_assistant_bridge: 'uv.exe',
    environment_state_server: 'uv.exe',
    openai_provider_broker: 'uv.exe',
    thought_core_api: 'pwsh.exe',
    aituber_kit: 'cmd.exe',
    thought_core_watcher: 'pwsh.exe',
    touchdesigner_control_gui: path.basename(process.execPath)
  }
  const writePlan = (name, targetScript) => {
    const services = authority.graph.services
      .filter((service) => service.ownership === 'owned' && service.requirement === 'required')
      .map((service) => ({
        service_id: service.service_id,
        file_path: service.service_id === 'touchdesigner_control_gui'
          ? process.execPath
          : path.join(root, executableByService[service.service_id]),
        arguments: service.service_id === 'touchdesigner_control_gui' ? [targetScript] : [],
        working_directory: root,
        environment: explicitEnvironment,
        remove_environment: [],
        clear_inherited_environment: true,
        listener_port: service.port.loopback_port || 0
      }))
    const planPath = path.join(root, `${name}.json`)
    fs.writeFileSync(planPath, JSON.stringify({
      schema_version: 'launcher_private_service_plans.v1',
      graph_sha256: authority.identities.graphSha256,
      binding_sha256: authority.identities.bindingSha256,
      services
    }), { encoding: 'utf8', mode: 0o600 })
    return planPath
  }
  const makeClient = (name, targetScript) => {
    const transport = new PowerShellJsonLineTransport({
      repositoryRoot: ROOT,
      privatePlanPath: writePlan(name, targetScript),
      powershellPath,
      responseGraceMs: 1000,
      closeTimeoutMs: 5000
    })
    transports.push(transport)
    return { transport, client: new LauncherJobWorkerClient({ authority, transport }) }
  }
  const removeMarker = (marker) => {
    if (fs.existsSync(marker)) fs.unlinkSync(marker)
  }
  try {
    const direct = makeClient('direct-plan', directScript)
    const stopping = stoppingLifecycle()
    const emptyStopRequest = requestFor('touchdesigner_control_gui', 'stop', stopping.revision)
    const emptyStopResult = await direct.client.execute(emptyStopRequest)
    assert.equal(emptyStopResult.ownership_class, 'matched')
    const emptyStopEvent = reducer.workerResultToEvent(emptyStopResult, stopping, emptyStopRequest, authority)
    assert.equal(emptyStopEvent.event_type, 'service_stopped')
    assert.notEqual(reducer.reduce(stopping, emptyStopEvent, authority).reason, 'invalid_event')
    const optionalStopRequest = requestFor('mediapipe_camera_hub_stack', 'stop', stopping.revision)
    const optionalStopResult = await direct.client.execute(optionalStopRequest)
    assert.equal(optionalStopResult.ownership_class, 'matched')
    const optionalStopEvent = reducer.workerResultToEvent(optionalStopResult, stopping, optionalStopRequest, authority)
    assert.notEqual(reducer.reduce(stopping, optionalStopEvent, authority).reason, 'invalid_event')

    for (let index = 0; index < 10; index++) {
      removeMarker(directMarker)
      assert.equal((await direct.client.execute(requestFor('touchdesigner_control_gui', 'start', index * 3))).result_class, 'accepted')
      const pid = await waitForOwnedPid(directMarker)
      await waitUntil(() => canConnect(port), 'direct_listener_missing')
      assert.equal((await direct.client.execute(requestFor('touchdesigner_control_gui', 'probe', index * 3 + 1))).result_class, 'ready')
      assert.equal(pidAlive(pid), true)
      assert.equal((await direct.client.execute(requestFor('touchdesigner_control_gui', 'stop', index * 3 + 2))).result_class, 'stopped')
      await waitForPidExit(pid)
      await waitUntil(async () => !(await canConnect(port)), 'listener_residue')
    }
    await direct.client.close()

    const wrapper = makeClient('wrapper-plan', wrapperScript)
    for (const marker of [wrapperMarker, childMarker, grandchildMarker]) removeMarker(marker)
    assert.equal((await wrapper.client.execute(requestFor('touchdesigner_control_gui', 'start'))).result_class, 'accepted')
    assert.equal((await wrapper.client.execute(requestFor('touchdesigner_control_gui', 'probe', 2))).result_class, 'ready')
    const treePids = await Promise.all([wrapperMarker, childMarker, grandchildMarker].map(waitForOwnedPid))
    assert.equal(new Set(treePids).size, 3)
    assert.equal((await wrapper.client.execute(requestFor('touchdesigner_control_gui', 'stop', 3))).result_class, 'stopped')
    await Promise.all(treePids.map(waitForPidExit))
    await waitUntil(async () => !(await canConnect(port)), 'wrapper_listener_residue')
    await wrapper.client.close()

    const stdinCleanup = makeClient('stdin-plan', directScript)
    removeMarker(directMarker)
    assert.equal((await stdinCleanup.client.execute(requestFor('touchdesigner_control_gui', 'start'))).result_class, 'accepted')
    assert.equal((await stdinCleanup.client.execute(requestFor('touchdesigner_control_gui', 'probe', 2))).result_class, 'ready')
    const stdinPid = await waitForOwnedPid(directMarker)
    await stdinCleanup.client.close()
    await waitForPidExit(stdinPid)
    await waitUntil(async () => !(await canConnect(port)), 'stdin_cleanup_listener_residue')

    const crashCleanup = makeClient('crash-plan', directScript)
    removeMarker(directMarker)
    assert.equal((await crashCleanup.client.execute(requestFor('touchdesigner_control_gui', 'start'))).result_class, 'accepted')
    assert.equal((await crashCleanup.client.execute(requestFor('touchdesigner_control_gui', 'probe', 2))).result_class, 'ready')
    const crashPid = await waitForOwnedPid(directMarker)
    crashCleanup.transport.abort()
    await waitForPidExit(crashPid)
    await waitUntil(async () => !(await canConnect(port)), 'crash_cleanup_listener_residue')
    try {
      await crashCleanup.client.close()
    } catch (error) {
      assert.equal(error instanceof LauncherJobWorkerError && error.code === 'worker_transport_failed', true)
    }

    sentinel = spawnChild(process.execPath, [sentinelScript], {
      cwd: root, env: process.env, windowsHide: true, stdio: 'ignore'
    })
    const sentinelPid = await waitForOwnedPid(sentinelMarker)
    await waitUntil(() => canConnect(port), 'sentinel_listener_missing')
    const foreign = makeClient('foreign-plan', directScript)
    const foreignResult = await foreign.client.execute(requestFor('touchdesigner_control_gui', 'start'))
    assert.equal(foreignResult.result_class, 'listener_mismatch')
    assert.equal(foreignResult.descendant_class, 'foreign')
    assert.equal(pidAlive(sentinelPid), true)
    await foreign.client.close()
    assert.equal(pidAlive(sentinelPid), true)
    sentinel.kill()
    await waitForPidExit(sentinelPid)
    sentinel = null
    await assertPortFree(port)
  } finally {
    if (sentinel && sentinel.exitCode === null) sentinel.kill()
    for (const transport of transports) {
      try { transport.abort() } catch {}
      try { await transport.close() } catch {}
    }
    for (const marker of markerPaths) {
      const pid = readOwnedPid(marker)
      if (pid !== null) assert.equal(pidAlive(pid), false, 'owned_process_residue')
    }
    await assertPortFree(port)
    fs.rmSync(root, { recursive: true, force: true })
  }
})
