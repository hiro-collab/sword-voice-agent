'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { loadAuthority } = require('../tools/home-control-launcher/launcher-supervisor-contract')
const { loadProbeAuthority } = require('../tools/home-control-launcher/launcher-probe-result-binding')
const {
  LauncherProbeRuntimeContext,
  LauncherProbeRuntimeContextError,
  MAX_MODULE_STATUS_BYTES,
  createDefaultModuleStatusObserver
} = require('../tools/home-control-launcher/launcher-probe-runtime-context')

const ROOT = path.resolve(__dirname, '..')
const authority = loadAuthority(ROOT)
const probeAuthority = loadProbeAuthority(ROOT, authority)
const BASE_MS = Date.parse('2026-07-29T14:00:00.000Z')
const PRIVATE_PATH = 'C:\\private\\runtime\\PRIVATE_PATH_SENTINEL'
const EFFECTIVE_CONFIG_SHA256 = 'e'.repeat(64)
const CAMERA_POLICY = 'camera_excluded_by_profile'

const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'content-type': 'application/json' }
})
const textResponse = (value, status = 200) => new Response(value, { status })

const compiledPlan = ({ environmentSecret = 'PRIVATE_ENVIRONMENT_TOKEN_A', pathSuffix = 'one', addEnvironmentName = false } = {}) => {
  const services = authority.graph.services
    .filter((service) => service.ownership !== 'external')
    .map((service) => ({
      service_id: service.service_id,
      file_path: `${PRIVATE_PATH}\\${pathSuffix}\\${service.service_id}.exe`,
      arguments: ['PRIVATE_ARGUMENT_SENTINEL'],
      working_directory: `${PRIVATE_PATH}\\${pathSuffix}\\${service.service_id}`,
      environment: service.service_id === 'thought_core_api'
        ? {
            ENVIRONMENT_API_TOKEN: environmentSecret,
            HOME_CONTROL_API_TOKEN: 'PRIVATE_HOME_TOKEN',
            ...(addEnvironmentName ? { SAFE_NEW_SETTING: 'PRIVATE_VALUE' } : {})
          }
        : {},
      remove_environment: [],
      clear_inherited_environment: true,
      listener_port: Number(service.port.loopback_port || 0)
    }))
  return {
    document: {
      schema_version: 'launcher_private_service_plans.v1',
      graph_sha256: authority.identities.graphSha256,
      binding_sha256: authority.identities.bindingSha256,
      profile_id: authority.graph.profile_id,
      effective_config_sha256: EFFECTIVE_CONFIG_SHA256,
      camera_policy: CAMERA_POLICY,
      services
    },
    powershell_path: `${PRIVATE_PATH}\\pwsh.exe`,
    included_service_ids: services.map((service) => service.service_id).sort()
  }
}

const operationFor = (serviceId, values = {}) => ({
  schema_version: 'launcher_operation.v2',
  graph_sha256: authority.identities.graphSha256,
  binding_sha256: authority.identities.bindingSha256,
  profile_id: authority.graph.profile_id,
  effective_config_sha256: EFFECTIVE_CONFIG_SHA256,
  camera_policy: CAMERA_POLICY,
  operation_id: 'lop_runtimecontext01',
  supervisor_generation: 19,
  intent: 'start',
  phase: 'waiting_ready',
  reason: 'none',
  cleanup: 'not_started',
  primary_result: { class: 'none', responsible_id: null, action_certainty: 'not_attempted' },
  cleanup_result: { class: 'not_started', responsible_id: null },
  revision: 42,
  joined_existing: false,
  rollback_required: false,
  recovery_required: false,
  services: authority.graph.services.map((service) => service.service_id === serviceId
    ? {
        service_id: service.service_id,
        state: 'starting',
        attempt_sequence: 1,
        pending_dispatch_id: 'ld_runtimecontext000001',
        pending_action: 'probe',
        probe_status: 'transport_ready',
        probe_expected_revision: 41,
        last_probe_result: null
      }
    : {
        service_id: service.service_id,
        state: 'pending',
        attempt_sequence: 0,
        pending_dispatch_id: null,
        pending_action: null,
        probe_status: 'not_checked',
        probe_expected_revision: null,
        last_probe_result: null
      }),
  residue_service_ids: [],
  ...values
})

const makeContext = ({ compiled = compiledPlan(), privateRuntimeRoot, fetchImpl, observers = {}, now = BASE_MS, clock } = {}) => new LauncherProbeRuntimeContext({
  repositoryRoot: ROOT,
  ...(privateRuntimeRoot === undefined ? {} : { privateRuntimeRoot }),
  authority,
  compiled,
  fetchImpl: fetchImpl || (async () => { throw new Error('unexpected_fetch') }),
  observers,
  clock: clock || (() => now),
  monotonicClock: () => 10
})

const statusPathFor = (root) => path.join(root, 'thought-core-watcher', 'modules', 'thought_core_watcher.json')
const statusPayload = (timestampSeconds) => ({
  name: 'thought_core_watcher',
  label: 'PRIVATE_LABEL_SENTINEL',
  state: 'running',
  detail: 'PRIVATE_DETAIL_SENTINEL',
  timestamp: timestampSeconds
})
const writeStatus = (root, payload) => {
  const statusPath = statusPathFor(root)
  fs.mkdirSync(path.dirname(statusPath), { recursive: true })
  const temporaryPath = `${statusPath}.temporary`
  fs.writeFileSync(temporaryPath, JSON.stringify(payload), 'utf8')
  fs.rmSync(statusPath, { force: true })
  fs.renameSync(temporaryPath, statusPath)
}
const withTempRoot = async (action) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-probe-status-'))
  try { return await action(root) } finally { fs.rmSync(root, { recursive: true, force: true }) }
}
const moduleObserverInput = (signal) => ({
  target_id: 'module_status',
  transport: 'module_status',
  resolved_target: null,
  operation_id: 'lop_runtimecontext01',
  supervisor_generation: 19,
  dispatch_id: 'ld_runtimecontext000001',
  expected_revision: 41,
  requested_at: new Date(BASE_MS).toISOString(),
  service_id: 'thought_core_watcher',
  probe_id: 'thought_core_watcher_status',
  freshness_max_age_ms: 15000,
  signal
})

test('runtime context derives loopback endpoints and injects only declared private auth', async () => {
  const requests = []
  const bodies = {
    '/health': { ok: true, status: 'ok' },
    '/ready': {
      ok: true,
      ready: true,
      status: 'ready',
      camera_requirement: {
        requirement_id: 'camera_hub',
        profile_id: authority.graph.profile_id,
        effective_config_sha256: EFFECTIVE_CONFIG_SHA256,
        policy: CAMERA_POLICY,
        requirement: 'not_required',
        result: 'camera_excluded_by_profile'
      }
    },
    '/environment/current': {
      schema_version: 1,
      stale: false,
      observed_at: '2026-07-29T14:00:00.000000+00:00',
      sources: {
        home_assistant: { available: true, stale: false }
      }
    }
  }
  const context = makeContext({
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return jsonResponse(bodies[new URL(url).pathname])
    }
  })
  const result = await context.executePendingProbe({
    operation: operationFor('environment_state_server'),
    serviceId: 'environment_state_server'
  })

  const port = authority.graph.services.find((service) => service.service_id === 'environment_state_server').port.loopback_port
  assert.deepEqual(requests.map((request) => request.url), [
    `http://127.0.0.1:${port}/health`,
    `http://127.0.0.1:${port}/ready`,
    `http://127.0.0.1:${port}/environment/current`
  ])
  assert.deepEqual(requests[0].options.headers, {})
  assert.deepEqual(requests[1].options.headers, {})
  assert.deepEqual(requests[2].options.headers, { Authorization: 'Bearer PRIVATE_ENVIRONMENT_TOKEN_A' })
  assert.equal(result.ready, true)
  assert.equal(result.semantic_class, 'ready')
  assert.equal(result.source_observed_at, new Date(BASE_MS).toISOString())
})

test('external VOICEVOX is read-only, loopback-only and receives no private header', async () => {
  const requests = []
  const context = makeContext({
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return textResponse('0.25.1')
    }
  })
  const result = await context.executePendingProbe({
    operation: operationFor('voicevox'),
    serviceId: 'voicevox'
  })
  assert.equal(result.semantic_class, 'external_ready')
  assert.equal(result.ready, true)
  assert.equal(requests.length, 1)
  assert.equal(requests[0].options.method, 'GET')
  assert.deepEqual(requests[0].options.headers, {})
  assert.equal(new URL(requests[0].url).hostname, '127.0.0.1')
  assert.equal(new URL(requests[0].url).pathname, '/version')
})

test('safe config hash is stable and insensitive to secret and private path values', () => {
  const first = makeContext({ compiled: compiledPlan({ environmentSecret: 'PRIVATE_SECRET_ONE', pathSuffix: 'one' }) })
  const second = makeContext({ compiled: compiledPlan({ environmentSecret: 'PRIVATE_SECRET_TWO', pathSuffix: 'two' }) })
  const changedShape = makeContext({ compiled: compiledPlan({ addEnvironmentName: true }) })
  assert.match(first.configSha256, /^[a-f0-9]{64}$/u)
  assert.equal(first.configSha256, second.configSha256)
  assert.notEqual(first.configSha256, changedShape.configSha256)
  assert.equal(first.configSha256.includes('PRIVATE'), false)
  assert.deepEqual(Object.keys(first), ['configSha256'])
})

test('result is correlated to pending generation, dispatch and expected revision without inference', async () => {
  const operation = operationFor('home_assistant_bridge')
  const context = makeContext({ fetchImpl: async () => jsonResponse({ ok: true, status: 'ok' }) })
  const result = await context.executePendingProbe({ operation, serviceId: 'home_assistant_bridge' })
  assert.equal(result.operation_id, operation.operation_id)
  assert.equal(result.supervisor_generation, 19)
  assert.equal(result.dispatch_id, 'ld_runtimecontext000001')
  assert.equal(result.expected_revision, 41)
  assert.equal(result.ready, true)

  let fetchCalls = 0
  const rejecting = makeContext({
    fetchImpl: async () => {
      fetchCalls += 1
      return jsonResponse({ ok: true, status: 'ok' })
    }
  })
  const malformed = operationFor('home_assistant_bridge')
  malformed.services.find((service) => service.service_id === 'home_assistant_bridge').probe_status = 'pending'
  await assert.rejects(
    rejecting.executePendingProbe({ operation: malformed, serviceId: 'home_assistant_bridge' }),
    (error) => error instanceof LauncherProbeRuntimeContextError && error.code === 'probe_runtime_service_invalid'
  )
  assert.equal(fetchCalls, 0)
})

test('runtime-compatible execute accepts only the safe context config identity', async () => {
  let fetchCalls = 0
  const context = makeContext({
    fetchImpl: async () => {
      fetchCalls += 1
      return jsonResponse({ ok: true, status: 'ok' })
    }
  })
  const descriptor = probeAuthority.descriptors.find((item) => item.service_id === 'home_assistant_bridge')
  const expected = {
    operation_id: 'lop_runtimecontext01',
    supervisor_generation: 19,
    dispatch_id: 'ld_runtimecontext000001',
    expected_revision: 41,
    service_id: descriptor.service_id,
    probe_id: descriptor.probe_id,
    graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    descriptor_sha256: descriptor.descriptor_sha256,
    config_sha256: context.configSha256,
    requested_at: new Date(BASE_MS).toISOString()
  }
  const result = await context.execute(expected)
  assert.equal(result.ready, true)
  assert.equal(result.config_sha256, context.configSha256)
  assert.equal(fetchCalls, 1)

  await assert.rejects(
    context.execute({ ...expected, config_sha256: 'f'.repeat(64) }),
    (error) => error instanceof LauncherProbeRuntimeContextError && error.code === 'probe_runtime_config_drift'
  )
  assert.equal(fetchCalls, 1)
})

test('default module, websocket and private-status observers fail closed without fake Ready', async () => {
  const context = makeContext()
  const moduleResult = await context.executePendingProbe({
    operation: operationFor('thought_core_watcher'),
    serviceId: 'thought_core_watcher'
  })
  assert.equal(moduleResult.ready, false)
  assert.equal(moduleResult.reason_class, 'module_status_missing')

  const websocketResult = await context.executePendingProbe({
    operation: operationFor('vision_snapshot_processor'),
    serviceId: 'vision_snapshot_processor'
  })
  assert.equal(websocketResult.ready, false)
  assert.equal(websocketResult.reason_class, 'websocket_unavailable')

  const cameraResult = await context.executePendingProbe({
    operation: operationFor('mediapipe_camera_hub_stack'),
    serviceId: 'mediapipe_camera_hub_stack'
  })
  assert.equal(cameraResult.ready, false)
  assert.equal(cameraResult.semantic_class, 'not_ready')
})

test('default module observer waits past prior bytes and binds only a post-request status', async () => {
  await withTempRoot(async (root) => {
    writeStatus(root, statusPayload((BASE_MS - 1000) / 1000))
    const observer = createDefaultModuleStatusObserver(root)
    const controller = new AbortController()
    const pending = observer(moduleObserverInput(controller.signal))
    const timer = setTimeout(() => writeStatus(root, statusPayload((BASE_MS + 100) / 1000)), 30)
    try {
      const value = await pending
      assert.deepEqual(value, {
        ok: true,
        state: 'running',
        operation_id: 'lop_runtimecontext01',
        timestamp: new Date(BASE_MS + 100).toISOString()
      })
      assert.equal(JSON.stringify(value).includes('PRIVATE_'), false)
    } finally {
      clearTimeout(timer)
    }

    let clockCalls = 0
    const context = makeContext({
      privateRuntimeRoot: root,
      clock: () => clockCalls++ === 0 ? BASE_MS : BASE_MS + 200
    })
    const result = await context.executePendingProbe({
      operation: operationFor('thought_core_watcher'),
      serviceId: 'thought_core_watcher'
    })
    assert.equal(result.ready, true)
    assert.equal(result.source_observed_at, new Date(BASE_MS + 100).toISOString())
  })
})

test('default module observer rejects reparse, oversized and malformed status files', async () => {
  await withTempRoot(async (root) => {
    const external = path.join(root, 'external-watcher')
    writeStatus(external, statusPayload(BASE_MS / 1000))
    fs.symlinkSync(external, path.join(root, 'thought-core-watcher'), 'junction')
    const observer = createDefaultModuleStatusObserver(root)
    await assert.rejects(
      observer(moduleObserverInput(new AbortController().signal)),
      (error) => error instanceof LauncherProbeRuntimeContextError && error.code === 'probe_runtime_observer_invalid'
    )
  })

  await withTempRoot(async (root) => {
    const statusPath = statusPathFor(root)
    fs.mkdirSync(path.dirname(statusPath), { recursive: true })
    fs.writeFileSync(statusPath, Buffer.alloc(MAX_MODULE_STATUS_BYTES + 1, 0x61))
    const observer = createDefaultModuleStatusObserver(root)
    await assert.rejects(
      observer(moduleObserverInput(new AbortController().signal)),
      (error) => error instanceof LauncherProbeRuntimeContextError && error.code === 'probe_runtime_observer_invalid'
    )
  })

  await withTempRoot(async (root) => {
    const statusPath = statusPathFor(root)
    fs.mkdirSync(path.dirname(statusPath), { recursive: true })
    fs.writeFileSync(statusPath, '{malformed', 'utf8')
    const observer = createDefaultModuleStatusObserver(root)
    await assert.rejects(
      observer(moduleObserverInput(new AbortController().signal)),
      (error) => error instanceof LauncherProbeRuntimeContextError && error.code === 'probe_runtime_observer_invalid'
    )
  })
})

test('default module observer aborts a missing or prior status poll without leaking a path', async () => {
  await withTempRoot(async (root) => {
    const observer = createDefaultModuleStatusObserver(root)
    const controller = new AbortController()
    const pending = observer(moduleObserverInput(controller.signal))
    setTimeout(() => controller.abort(), 30)
    await assert.rejects(pending, (error) => error.name === 'AbortError' && !String(error).includes(root))
  })
})

test('injected module observer is shape validated and current-operation correlation remains authoritative', async () => {
  const operation = operationFor('thought_core_watcher')
  const current = makeContext({
    observers: {
      module_status: async (input) => ({
        ok: true,
        state: 'running',
        operation_id: input.operation_id,
        timestamp: new Date(BASE_MS).toISOString()
      })
    }
  })
  const ready = await current.executePendingProbe({ operation, serviceId: 'thought_core_watcher' })
  assert.equal(ready.ready, true)
  assert.equal(ready.semantic_class, 'ready')

  const mismatched = makeContext({
    observers: {
      module_status: async () => ({
        ok: true,
        state: 'running',
        operation_id: 'lop_otheroperation1',
        timestamp: new Date(BASE_MS).toISOString()
      })
    }
  })
  const rejected = await mismatched.executePendingProbe({ operation, serviceId: 'thought_core_watcher' })
  assert.equal(rejected.ready, false)
  assert.equal(rejected.reason_class, 'module_status_invalid')
})

test('bounded result and failures exclude raw secret, command and private path material', async () => {
  const context = makeContext({ fetchImpl: async () => jsonResponse({ ok: true, status: 'ok' }) })
  const result = await context.executePendingProbe({
    operation: operationFor('home_assistant_bridge'),
    serviceId: 'home_assistant_bridge'
  })
  const serialized = JSON.stringify(result)
  for (const sentinel of ['PRIVATE_', 'ENVIRONMENT_API_TOKEN', 'Authorization', 'file_path', 'working_directory', 'arguments']) {
    assert.equal(serialized.includes(sentinel), false, sentinel)
  }

  const missingSecret = makeContext({ compiled: compiledPlan({ environmentSecret: '' }), fetchImpl: async () => jsonResponse({}) })
  await assert.rejects(
    missingSecret.executePendingProbe({
      operation: operationFor('environment_state_server'),
      serviceId: 'environment_state_server'
    }),
    (error) => error.code === 'probe_executor_target_resolution_invalid' && !String(error).includes('PRIVATE_')
  )
})
