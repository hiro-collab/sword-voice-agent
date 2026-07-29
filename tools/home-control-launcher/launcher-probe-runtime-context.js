'use strict'

const fs = require('node:fs')
const path = require('node:path')

const {
  assertAuthority,
  canonicalJsonSha256,
  loadAuthority
} = require('./launcher-supervisor-contract')
const { LauncherProbeExecutor } = require('./launcher-probe-executor')
const { loadProbeAuthority } = require('./launcher-probe-result-binding')

const SAFE_CODES = new Set([
  'probe_runtime_options_invalid',
  'probe_runtime_authority_invalid',
  'probe_runtime_plan_invalid',
  'probe_runtime_endpoint_invalid',
  'probe_runtime_auth_missing',
  'probe_runtime_config_drift',
  'probe_runtime_operation_invalid',
  'probe_runtime_service_invalid',
  'probe_runtime_observer_invalid',
  'probe_runtime_internal_failure'
])
const OPERATION_ID = /^lop_[a-z0-9]{8,64}$/u
const DISPATCH_ID = /^ld_[a-z0-9]{16,64}$/u
const SERVICE_ID = /^[a-z][a-z0-9_]{0,63}$/u
const ENDPOINT_REF = /^[a-z][a-z0-9_]{0,63}$/u
const MAX_SECRET_BYTES = 4000
const MAX_MODULE_STATUS_BYTES = 16 * 1024
const MODULE_STATUS_POLL_MS = 25
const MODULE_STATUS_RELATIVE_SEGMENTS = Object.freeze([
  'thought-core-watcher', 'modules', 'thought_core_watcher.json'
])
const MODULE_STATUS_KEYS = Object.freeze(['name', 'label', 'state', 'detail', 'timestamp'])
const OPERATION_KEYS = Object.freeze([
  'schema_version', 'graph_sha256', 'binding_sha256', 'operation_id',
  'supervisor_generation', 'intent', 'phase', 'reason', 'cleanup',
  'primary_result', 'cleanup_result', 'revision', 'joined_existing',
  'rollback_required', 'recovery_required', 'services', 'residue_service_ids'
])
const SERVICE_KEYS = Object.freeze([
  'service_id', 'state', 'attempt_sequence', 'pending_dispatch_id', 'pending_action',
  'probe_status', 'probe_expected_revision', 'last_probe_result'
])
const PRIVATE_AUTH_ENV_BY_ENDPOINT = Object.freeze({
  environment_state_http: 'ENVIRONMENT_API_TOKEN'
})
const OBSERVER_KEYS = Object.freeze(['websocket', 'module_status', 'private_status'])
const OBSERVER_RESULT_KEYS = Object.freeze({
  websocket: new Set(['ok', 'source_observed_at', 'operational_class']),
  module_status: new Set(['ok', 'state', 'operation_id', 'timestamp']),
  private_status: new Set([
    'ownership_matched', 'registry_matched', 'manifest_valid', 'lineage_matched',
    'listener_matched', 'operational_class', 'source_observed_at'
  ])
})
const PRIVATE_CONTEXT = new WeakMap()

class LauncherProbeRuntimeContextError extends Error {
  constructor (code) {
    super(code)
    this.name = 'LauncherProbeRuntimeContextError'
    this.code = code
  }
}

const fail = (code) => {
  if (!SAFE_CODES.has(code)) code = 'probe_runtime_internal_failure'
  throw new LauncherProbeRuntimeContextError(code)
}

const isPlainObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const exactKeys = (value, keys, code) => {
  if (!isPlainObject(value)) fail(code)
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(code)
}

const timestamp = (value) => {
  if (typeof value !== 'string' || value.length > 32) return false
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === value
}

const clockMillis = (clock) => {
  let value
  try { value = clock() } catch { fail('probe_runtime_options_invalid') }
  if (value instanceof Date) value = value.getTime()
  else if (typeof value === 'string') value = Date.parse(value)
  if (!Number.isSafeInteger(value) || value < 0) fail('probe_runtime_options_invalid')
  return value
}

const sortedUniqueStrings = (value, code) => {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string') || new Set(value).size !== value.length) fail(code)
  return [...value].sort()
}

const validateCompiledPlan = (compiled, authority) => {
  if (!isPlainObject(compiled) || !isPlainObject(compiled.document) ||
      compiled.document.schema_version !== 'launcher_private_service_plans.v1' ||
      compiled.document.graph_sha256 !== authority.identities.graphSha256 ||
      compiled.document.binding_sha256 !== authority.identities.bindingSha256 ||
      !Array.isArray(compiled.document.services)) fail('probe_runtime_plan_invalid')

  const graphById = new Map(authority.graph.services.map((service) => [service.service_id, service]))
  const services = []
  const seen = new Set()
  for (const plan of compiled.document.services) {
    if (!isPlainObject(plan) || !SERVICE_ID.test(plan.service_id) || seen.has(plan.service_id) ||
        !isPlainObject(plan.environment) || !Array.isArray(plan.remove_environment) ||
        plan.clear_inherited_environment !== true) fail('probe_runtime_plan_invalid')
    const spec = graphById.get(plan.service_id)
    if (!spec || spec.ownership === 'external' ||
        Number(plan.listener_port) !== Number(spec.port.loopback_port || 0)) fail('probe_runtime_plan_invalid')
    for (const [name, value] of Object.entries(plan.environment)) {
      if (typeof name !== 'string' || typeof value !== 'string') fail('probe_runtime_plan_invalid')
    }
    sortedUniqueStrings(plan.remove_environment, 'probe_runtime_plan_invalid')
    seen.add(plan.service_id)
    services.push(plan)
  }

  const included = sortedUniqueStrings(compiled.included_service_ids, 'probe_runtime_plan_invalid')
  if (JSON.stringify(included) !== JSON.stringify([...seen].sort())) fail('probe_runtime_plan_invalid')
  for (const spec of authority.graph.services) {
    if (spec.ownership === 'external') {
      if (seen.has(spec.service_id)) fail('probe_runtime_plan_invalid')
    } else if (spec.requirement === 'required' && !seen.has(spec.service_id)) {
      fail('probe_runtime_plan_invalid')
    }
  }
  return Object.freeze({ services: Object.freeze(services), included: Object.freeze(included) })
}

const safeConfigDocument = (validatedPlan, authority) => Object.freeze({
  schema_version: 'launcher_probe_runtime_config.v1',
  profile_id: authority.graph.profile_id,
  graph_sha256: authority.identities.graphSha256,
  binding_sha256: authority.identities.bindingSha256,
  included_service_ids: validatedPlan.included,
  services: Object.freeze(validatedPlan.services.map((plan) => Object.freeze({
    service_id: plan.service_id,
    listener_port: Number(plan.listener_port),
    environment_names: Object.freeze(Object.keys(plan.environment).sort()),
    remove_environment_names: Object.freeze([...plan.remove_environment].sort()),
    clear_inherited_environment: true
  })).sort((left, right) => left.service_id.localeCompare(right.service_id)))
})

const endpointAuthority = (authority) => {
  const endpoints = new Map()
  for (const service of authority.graph.services) {
    const endpointRef = service.port.endpoint_ref
    if (endpointRef === null) continue
    if (!ENDPOINT_REF.test(endpointRef) || endpoints.has(endpointRef) ||
        !['http', 'websocket'].includes(service.port.transport) ||
        !Number.isInteger(service.port.loopback_port) || service.port.loopback_port < 1 || service.port.loopback_port > 65535) {
      fail('probe_runtime_endpoint_invalid')
    }
    endpoints.set(endpointRef, Object.freeze({
      endpoint_ref: endpointRef,
      transport: service.port.transport,
      loopback_port: service.port.loopback_port
    }))
  }
  return endpoints
}

const secretFromPlan = (validatedPlan, environmentName) => {
  const values = new Set()
  for (const plan of validatedPlan.services) {
    if (Object.prototype.hasOwnProperty.call(plan.environment, environmentName)) values.add(plan.environment[environmentName])
  }
  if (values.size !== 1) fail('probe_runtime_auth_missing')
  const [value] = values
  if (typeof value !== 'string' || value.length === 0 || /[\u0000\r\n]/u.test(value) ||
      Buffer.byteLength(value, 'utf8') > MAX_SECRET_BYTES) fail('probe_runtime_auth_missing')
  return value
}

const createTargetResolver = (endpoints, validatedPlan) => async (target) => {
  if (!isPlainObject(target) || !ENDPOINT_REF.test(target.endpoint_ref || '')) fail('probe_runtime_endpoint_invalid')
  const endpoint = endpoints.get(target.endpoint_ref)
  if (!endpoint || endpoint.transport !== target.transport ||
      !/^\/(?!\/)[^\u0000\r\n]{0,255}$/u.test(target.path)) fail('probe_runtime_endpoint_invalid')
  const scheme = target.transport === 'http' ? 'http' : target.transport === 'websocket' ? 'ws' : null
  if (scheme === null) fail('probe_runtime_endpoint_invalid')
  let headers = {}
  if (target.auth_class === 'private_secret_ref') {
    const environmentName = PRIVATE_AUTH_ENV_BY_ENDPOINT[target.endpoint_ref]
    if (!environmentName) fail('probe_runtime_auth_missing')
    headers = { Authorization: `Bearer ${secretFromPlan(validatedPlan, environmentName)}` }
  } else if (target.auth_class !== 'none') {
    fail('probe_runtime_endpoint_invalid')
  }
  return Object.freeze({
    url: `${scheme}://127.0.0.1:${endpoint.loopback_port}${target.path}`,
    headers: Object.freeze(headers)
  })
}

const observerUnavailable = async () => {
  throw new LauncherProbeRuntimeContextError('probe_runtime_observer_invalid')
}

const abortError = () => Object.assign(new Error('probe_runtime_observer_invalid'), { name: 'AbortError' })

const waitForPoll = (signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) return reject(abortError())
  const timer = setTimeout(finish, MODULE_STATUS_POLL_MS)
  function finish () {
    signal?.removeEventListener('abort', abort)
    resolve()
  }
  function abort () {
    clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
    reject(abortError())
  }
  signal?.addEventListener('abort', abort, { once: true })
})

const readBoundedFile = async (filePath, initialStat) => {
  let handle
  try {
    handle = await fs.promises.open(filePath, 'r')
    const openedStat = await handle.stat()
    if (!openedStat.isFile() || openedStat.isSymbolicLink?.() || openedStat.size > MAX_MODULE_STATUS_BYTES ||
        openedStat.dev !== initialStat.dev || openedStat.ino !== initialStat.ino) fail('probe_runtime_observer_invalid')
    const bytes = Buffer.alloc(MAX_MODULE_STATUS_BYTES + 1)
    let length = 0
    while (length < bytes.length) {
      const item = await handle.read(bytes, length, bytes.length - length, null)
      if (item.bytesRead === 0) break
      length += item.bytesRead
    }
    if (length > MAX_MODULE_STATUS_BYTES) fail('probe_runtime_observer_invalid')
    return bytes.subarray(0, length)
  } catch (error) {
    if (error instanceof LauncherProbeRuntimeContextError) throw error
    fail('probe_runtime_observer_invalid')
  } finally {
    try { await handle?.close() } catch {}
  }
}

const inspectModuleStatusPath = async (privateRuntimeRoot) => {
  let cursor = path.resolve(privateRuntimeRoot)
  const paths = [cursor]
  for (const segment of MODULE_STATUS_RELATIVE_SEGMENTS) {
    cursor = path.join(cursor, segment)
    paths.push(cursor)
  }
  let fileStat
  for (let index = 0; index < paths.length; index += 1) {
    let stat
    try { stat = await fs.promises.lstat(paths[index]) } catch (error) {
      if (error?.code === 'ENOENT') return null
      fail('probe_runtime_observer_invalid')
    }
    if (stat.isSymbolicLink() || (index < paths.length - 1 ? !stat.isDirectory() : !stat.isFile())) {
      fail('probe_runtime_observer_invalid')
    }
    if (index === paths.length - 1) fileStat = stat
  }
  if (fileStat.size > MAX_MODULE_STATUS_BYTES) fail('probe_runtime_observer_invalid')
  return Object.freeze({ filePath: paths[paths.length - 1], fileStat })
}

const parseModuleStatus = (bytes) => {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0 ||
      (bytes.byteLength >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf)) {
    fail('probe_runtime_observer_invalid')
  }
  let text
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes) } catch { fail('probe_runtime_observer_invalid') }
  if (text.includes('\u0000')) fail('probe_runtime_observer_invalid')
  let value
  try { value = JSON.parse(text) } catch { fail('probe_runtime_observer_invalid') }
  exactKeys(value, MODULE_STATUS_KEYS, 'probe_runtime_observer_invalid')
  if (value.name !== 'thought_core_watcher' || !['running', 'ready'].includes(value.state) ||
      typeof value.label !== 'string' || Buffer.byteLength(value.label, 'utf8') > 256 ||
      typeof value.detail !== 'string' || Buffer.byteLength(value.detail, 'utf8') > 2048 ||
      !Number.isFinite(value.timestamp) || value.timestamp < 0) fail('probe_runtime_observer_invalid')
  const millis = Math.trunc(value.timestamp * 1000)
  if (!Number.isSafeInteger(millis) || millis < 0 || millis > 8640000000000000) {
    fail('probe_runtime_observer_invalid')
  }
  let observedAt
  try { observedAt = new Date(millis).toISOString() } catch { fail('probe_runtime_observer_invalid') }
  return Object.freeze({ state: value.state, observedAt, observedMillis: millis })
}

const createDefaultModuleStatusObserver = (privateRuntimeRoot) => {
  if (typeof privateRuntimeRoot !== 'string' || !path.isAbsolute(privateRuntimeRoot) || privateRuntimeRoot.includes('\u0000')) {
    fail('probe_runtime_options_invalid')
  }
  const fixedRoot = path.resolve(privateRuntimeRoot)
  return async (input) => {
    const requestedMillis = timestamp(input?.requested_at) ? Date.parse(input.requested_at) : NaN
    if (!isPlainObject(input) || input.transport !== 'module_status' ||
        input.target_id !== 'module_status' || input.service_id !== 'thought_core_watcher' ||
        !OPERATION_ID.test(input.operation_id || '') || !Number.isFinite(requestedMillis)) {
      fail('probe_runtime_observer_invalid')
    }
    while (true) {
      if (input.signal?.aborted) throw abortError()
      const inspected = await inspectModuleStatusPath(fixedRoot)
      if (inspected !== null) {
        const status = parseModuleStatus(await readBoundedFile(inspected.filePath, inspected.fileStat))
        if (status.observedMillis >= requestedMillis) {
          return Object.freeze({
            ok: true,
            state: status.state,
            operation_id: input.operation_id,
            timestamp: status.observedAt
          })
        }
      }
      await waitForPoll(input.signal)
    }
  }
}

const validateObserverResult = (transport, value) => {
  if (!isPlainObject(value)) fail('probe_runtime_observer_invalid')
  const allowed = OBSERVER_RESULT_KEYS[transport]
  if (!allowed || Object.keys(value).some((key) => !allowed.has(key))) fail('probe_runtime_observer_invalid')
  if (transport === 'websocket') {
    if (typeof value.ok !== 'boolean' ||
        (value.source_observed_at !== undefined && !timestamp(value.source_observed_at)) ||
        (value.operational_class !== undefined && !['ready', 'degraded'].includes(value.operational_class))) {
      fail('probe_runtime_observer_invalid')
    }
  } else if (transport === 'module_status') {
    if (typeof value.ok !== 'boolean' ||
        (value.state !== undefined && !['running', 'ready'].includes(value.state)) ||
        (value.operation_id !== undefined && !OPERATION_ID.test(value.operation_id)) ||
        (value.timestamp !== undefined && !timestamp(value.timestamp))) fail('probe_runtime_observer_invalid')
  } else if (transport === 'private_status') {
    const flags = ['ownership_matched', 'registry_matched', 'manifest_valid', 'lineage_matched', 'listener_matched']
    if (flags.some((key) => typeof value[key] !== 'boolean') ||
        !['ready', 'degraded'].includes(value.operational_class) || !timestamp(value.source_observed_at)) {
      fail('probe_runtime_observer_invalid')
    }
  }
  return Object.freeze({ ...value })
}

const validatedObservers = (observers, defaults = {}) => {
  if (observers === undefined) observers = {}
  if (!isPlainObject(observers) || Object.keys(observers).some((key) => !OBSERVER_KEYS.includes(key))) {
    fail('probe_runtime_options_invalid')
  }
  return Object.freeze(Object.fromEntries(OBSERVER_KEYS.map((transport) => {
    const observer = observers[transport] || defaults[transport] || observerUnavailable
    if (typeof observer !== 'function') fail('probe_runtime_options_invalid')
    return [transport, async (input) => validateObserverResult(transport, await observer(input))]
  })))
}

const validatePendingProbe = (operation, serviceId, authority) => {
  exactKeys(operation, OPERATION_KEYS, 'probe_runtime_operation_invalid')
  if (operation.schema_version !== 'launcher_operation.v2' ||
      operation.graph_sha256 !== authority.identities.graphSha256 ||
      operation.binding_sha256 !== authority.identities.bindingSha256 ||
      !OPERATION_ID.test(operation.operation_id) ||
      !Number.isSafeInteger(operation.supervisor_generation) || operation.supervisor_generation < 1 ||
      !Number.isSafeInteger(operation.revision) || operation.revision < 0 ||
      !Array.isArray(operation.services) || !SERVICE_ID.test(serviceId)) fail('probe_runtime_operation_invalid')

  const expectedIds = authority.graph.services.map((service) => service.service_id).sort()
  const actualIds = operation.services.map((service) => service?.service_id).sort()
  if (JSON.stringify(expectedIds) !== JSON.stringify(actualIds) || new Set(actualIds).size !== actualIds.length) {
    fail('probe_runtime_operation_invalid')
  }
  for (const service of operation.services) exactKeys(service, SERVICE_KEYS, 'probe_runtime_operation_invalid')
  const service = operation.services.find((candidate) => candidate.service_id === serviceId)
  const spec = authority.graph.services.find((candidate) => candidate.service_id === serviceId)
  if (!service || !spec || service.pending_action !== 'probe' ||
      service.probe_status !== 'transport_ready' || !DISPATCH_ID.test(service.pending_dispatch_id || '') ||
      !Number.isSafeInteger(service.probe_expected_revision) || service.probe_expected_revision < 0 ||
      service.last_probe_result !== null) fail('probe_runtime_service_invalid')
  return Object.freeze({ service, spec })
}

class LauncherProbeRuntimeContext {
  constructor ({ repositoryRoot, privateRuntimeRoot, compiled, authority, fetchImpl = globalThis.fetch, observers, clock = Date.now, monotonicClock }) {
    if (typeof repositoryRoot !== 'string' || typeof fetchImpl !== 'function' || typeof clock !== 'function' ||
        (monotonicClock !== undefined && typeof monotonicClock !== 'function')) {
      fail('probe_runtime_options_invalid')
    }
    let launcherAuthority
    try {
      launcherAuthority = authority || loadAuthority(repositoryRoot)
      assertAuthority(launcherAuthority)
    } catch {
      fail('probe_runtime_authority_invalid')
    }
    const validatedPlan = validateCompiledPlan(compiled, launcherAuthority)
    let probeAuthority
    try { probeAuthority = loadProbeAuthority(repositoryRoot, launcherAuthority) } catch { fail('probe_runtime_authority_invalid') }
    const endpoints = endpointAuthority(launcherAuthority)
    const configDocument = safeConfigDocument(validatedPlan, launcherAuthority)
    this.configSha256 = canonicalJsonSha256(configDocument)
    const executor = new LauncherProbeExecutor({
      probeAuthority,
      privateTargetResolver: createTargetResolver(endpoints, validatedPlan),
      fetchImpl,
      observers: validatedObservers(observers, {
        ...(privateRuntimeRoot === undefined
          ? {}
          : { module_status: createDefaultModuleStatusObserver(privateRuntimeRoot) })
      }),
      clock,
      ...(monotonicClock === undefined ? {} : { monotonicClock })
    })
    PRIVATE_CONTEXT.set(this, Object.freeze({
      authority: launcherAuthority,
      probeAuthority,
      clock,
      executor
    }))
    Object.freeze(this)
  }

  async execute (expected) {
    if (!isPlainObject(expected) || expected.config_sha256 !== this.configSha256) {
      fail('probe_runtime_config_drift')
    }
    return PRIVATE_CONTEXT.get(this).executor.execute(expected)
  }

  async executePendingProbe ({ operation, serviceId }) {
    const context = PRIVATE_CONTEXT.get(this)
    const { spec } = validatePendingProbe(operation, serviceId, context.authority)
    const descriptor = context.probeAuthority.byPair[`${serviceId}:${spec.readiness.probe_id}`]
    if (!descriptor) fail('probe_runtime_authority_invalid')
    const service = operation.services.find((candidate) => candidate.service_id === serviceId)
    const expected = Object.freeze({
      operation_id: operation.operation_id,
      supervisor_generation: operation.supervisor_generation,
      dispatch_id: service.pending_dispatch_id,
      expected_revision: service.probe_expected_revision,
      service_id: serviceId,
      probe_id: descriptor.probe_id,
      graph_sha256: context.authority.identities.graphSha256,
      binding_sha256: context.authority.identities.bindingSha256,
      descriptor_sha256: descriptor.descriptor_sha256,
      config_sha256: this.configSha256,
      requested_at: new Date(clockMillis(context.clock)).toISOString()
    })
    return context.executor.execute(expected)
  }
}

module.exports = {
  LauncherProbeRuntimeContext,
  LauncherProbeRuntimeContextError,
  MAX_MODULE_STATUS_BYTES,
  createDefaultModuleStatusObserver
}
