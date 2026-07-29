'use strict'

const { performance } = require('node:perf_hooks')

const { bindProbeResult } = require('./launcher-probe-result-binding')

const MAX_BODY_BYTES = 64 * 1024
const MAX_HEADERS = 16
const MAX_HEADER_VALUE_BYTES = 4096
const LOOPBACK_HOSTS = new Set(['127.0.0.1', '::1', '[::1]', 'localhost'])
const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/u
const SAFE_EXECUTOR_CODES = new Set([
  'probe_executor_options_invalid',
  'probe_executor_target_resolution_invalid',
  'probe_executor_observer_missing',
  'probe_executor_observer_result_invalid',
  'probe_executor_clock_invalid',
  'probe_executor_internal_failure'
])

class LauncherProbeExecutorError extends Error {
  constructor (code) {
    super(code)
    this.name = 'LauncherProbeExecutorError'
    this.code = code
  }
}

const fail = (code) => {
  if (!SAFE_EXECUTOR_CODES.has(code)) code = 'probe_executor_internal_failure'
  throw new LauncherProbeExecutorError(code)
}

const isPlainObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const isStrictJsonObject = (value) => isPlainObject(value) && Object.getPrototypeOf(value) === Object.prototype
const SOURCE_ID = /^[a-z][a-z0-9_-]{0,63}$/u
const FORBIDDEN_SOURCE_IDS = new Set(['constructor', 'prototype'])
const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

const expectedConfiguredEnvironmentSources = (identity) => new Set([
  'home_assistant',
  ...(identity.camera_policy === 'required' ? ['camera_hub'] : [])
])

const classifyConfiguredEnvironmentSources = (sources, identity) => {
  if (!isStrictJsonObject(sources)) return 'invalid'
  const expected = expectedConfiguredEnvironmentSources(identity)
  const observed = new Set()
  for (const [sourceId, entry] of Object.entries(sources)) {
    if (!SOURCE_ID.test(sourceId) || FORBIDDEN_SOURCE_IDS.has(sourceId) || !isStrictJsonObject(entry) ||
        !hasOwn(entry, 'available') || typeof entry.available !== 'boolean' ||
        !hasOwn(entry, 'stale') || typeof entry.stale !== 'boolean' ||
        (hasOwn(entry, 'configured') && typeof entry.configured !== 'boolean')) {
      return 'invalid'
    }
    observed.add(sourceId)
    const requiredByConfiguration = expected.has(sourceId)
    if (requiredByConfiguration && entry.configured === false) return 'invalid'
    if ((requiredByConfiguration || entry.configured === true) &&
        (entry.available !== true || entry.stale !== false)) return 'unready'
  }
  return [...expected].every((sourceId) => observed.has(sourceId)) ? 'ready' : 'invalid'
}

const clockMillis = (clock) => {
  let value
  try {
    value = clock()
  } catch {
    fail('probe_executor_clock_invalid')
  }
  if (value instanceof Date) value = value.getTime()
  else if (typeof value === 'string') value = Date.parse(value)
  if (!Number.isSafeInteger(value) || value < 0) fail('probe_executor_clock_invalid')
  return value
}

const monotonicMillis = (clock) => {
  let value
  try {
    value = clock()
  } catch {
    fail('probe_executor_clock_invalid')
  }
  if (!Number.isFinite(value) || value < 0) fail('probe_executor_clock_invalid')
  return value
}

const strictTimestampMillis = (value) => {
  if (typeof value !== 'string' || value.length > 32) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === value ? parsed : null
}

const iso = (millis) => new Date(millis).toISOString()

const PYTHON_UTC_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\+00:00$/u
const normalizeEnvironmentSourceTimestamp = (value) => {
  const strictMillis = strictTimestampMillis(value)
  if (strictMillis !== null) return iso(strictMillis)
  if (typeof value !== 'string' || value.length > 32) return null
  const match = PYTHON_UTC_TIMESTAMP.exec(value)
  if (!match) return null
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number)
  const milliseconds = Number((match[7] || '').padEnd(3, '0').slice(0, 3))
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31 ||
      hour > 23 || minute > 59 || second > 59) return null
  const parsed = new Date(0)
  parsed.setUTCFullYear(year, month - 1, day)
  parsed.setUTCHours(hour, minute, second, milliseconds)
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day ||
      parsed.getUTCHours() !== hour || parsed.getUTCMinutes() !== minute || parsed.getUTCSeconds() !== second ||
      parsed.getUTCMilliseconds() !== milliseconds) return null
  return parsed.toISOString()
}

const validateHeaders = (headers, authClass) => {
  if (headers === undefined) headers = {}
  if (!isPlainObject(headers) || Object.keys(headers).length > MAX_HEADERS) {
    fail('probe_executor_target_resolution_invalid')
  }
  if (authClass === 'none' && Object.keys(headers).length !== 0) {
    fail('probe_executor_target_resolution_invalid')
  }
  const safe = {}
  for (const [name, value] of Object.entries(headers)) {
    if (!HEADER_NAME.test(name) || typeof value !== 'string' || /[\r\n]/u.test(value) ||
        Buffer.byteLength(value, 'utf8') > MAX_HEADER_VALUE_BYTES) {
      fail('probe_executor_target_resolution_invalid')
    }
    safe[name] = value
  }
  return safe
}

const resolveNetworkTarget = async (resolver, target, expected) => {
  if (typeof resolver !== 'function') fail('probe_executor_options_invalid')
  let resolved
  try {
    resolved = await resolver(Object.freeze({
      target_id: target.target_id,
      transport: target.transport,
      endpoint_ref: target.endpoint_ref,
      path: target.path,
      auth_class: target.auth_class,
      operation_id: expected.operation_id,
      supervisor_generation: expected.supervisor_generation,
      dispatch_id: expected.dispatch_id,
      service_id: expected.service_id,
      probe_id: expected.probe_id,
      config_sha256: expected.config_sha256
    }))
  } catch {
    fail('probe_executor_target_resolution_invalid')
  }
  if (!isPlainObject(resolved) || typeof resolved.url !== 'string') {
    fail('probe_executor_target_resolution_invalid')
  }
  let url
  try {
    url = new URL(resolved.url)
  } catch {
    fail('probe_executor_target_resolution_invalid')
  }
  const allowedProtocol = target.transport === 'http' ? 'http:' : target.transport === 'websocket' ? 'ws:' : null
  if (url.protocol !== allowedProtocol || !LOOPBACK_HOSTS.has(url.hostname) || url.username || url.password ||
      url.search || url.hash || url.pathname !== target.path) {
    fail('probe_executor_target_resolution_invalid')
  }
  return Object.freeze({ url: url.href, headers: Object.freeze(validateHeaders(resolved.headers, target.auth_class)) })
}

const readBoundedBody = async (response) => {
  const declared = response?.headers?.get?.('content-length')
  if (declared !== null && declared !== undefined) {
    const length = Number(declared)
    if (!Number.isSafeInteger(length) || length < 0 || length > MAX_BODY_BYTES) {
      return { ok: false, reason: 'response_invalid' }
    }
  }
  if (!response?.body || typeof response.body.getReader !== 'function') {
    return { ok: false, reason: 'response_invalid' }
  }
  const reader = response.body.getReader()
  const chunks = []
  let length = 0
  try {
    while (true) {
      const item = await reader.read()
      if (item.done) break
      if (!(item.value instanceof Uint8Array)) return { ok: false, reason: 'response_invalid' }
      length += item.value.byteLength
      if (length > MAX_BODY_BYTES) {
        await reader.cancel().catch(() => {})
        return { ok: false, reason: 'response_invalid' }
      }
      chunks.push(item.value)
    }
  } catch {
    return { ok: false, reason: 'health_unavailable' }
  }
  const bytes = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk)), length)
  let text
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    return { ok: false, reason: 'response_invalid' }
  }
  return { ok: true, text }
}

const withDeadline = async (durationMs, action) => {
  const controller = new AbortController()
  let timer
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      controller.abort()
      resolve({ ok: false, failure: 'deadline' })
    }, durationMs)
  })
  try {
    return await Promise.race([
      Promise.resolve().then(() => action(controller.signal)).catch((error) => {
        if (error instanceof LauncherProbeExecutorError) throw error
        return {
          ok: false,
          failure: error?.name === 'AbortError' ? 'deadline' : 'unavailable'
        }
      }),
      timeout
    ])
  } finally {
    clearTimeout(timer)
  }
}

const executeHttpTarget = async ({ target, expected, options, timeoutMs }) => withDeadline(timeoutMs, async (signal) => {
  const resolved = await resolveNetworkTarget(options.privateTargetResolver, target, expected)
  let response
  try {
    response = await options.fetchImpl(resolved.url, {
      method: 'GET',
      headers: resolved.headers,
      redirect: 'error',
      signal
    })
  } catch (error) {
    return { ok: false, failure: error?.name === 'AbortError' ? 'deadline' : 'unavailable' }
  }
  if (!response || !Number.isInteger(response.status) || response.status < 100 || response.status > 599) {
    return { ok: false, failure: 'invalid' }
  }
  const body = await readBoundedBody(response)
  if (!body.ok) return { ok: false, failure: body.reason === 'health_unavailable' ? 'unavailable' : 'invalid' }
  let value = body.text
  if (target.response_class === 'bounded_json') {
    try {
      value = JSON.parse(body.text)
    } catch {
      return { ok: false, failure: 'invalid' }
    }
    if (!isPlainObject(value)) return { ok: false, failure: 'invalid' }
  } else if (typeof value !== 'string' || value.trim().length === 0 || /\u0000/u.test(value)) {
    return { ok: false, failure: 'invalid' }
  }
  return { ok: true, status_ok: response.status >= 200 && response.status < 300, value }
})

const executeObserverTarget = async ({ target, expected, descriptor, options, timeoutMs }) => {
  const observer = options.observers[target.transport]
  if (typeof observer !== 'function') fail('probe_executor_observer_missing')
  return withDeadline(timeoutMs, async (signal) => {
    const resolved = target.transport === 'websocket'
      ? await resolveNetworkTarget(options.privateTargetResolver, target, expected)
      : null
    let value
    try {
      value = await observer(Object.freeze({
        target_id: target.target_id,
        transport: target.transport,
        resolved_target: resolved,
        operation_id: expected.operation_id,
        supervisor_generation: expected.supervisor_generation,
        dispatch_id: expected.dispatch_id,
        expected_revision: expected.expected_revision,
        requested_at: expected.requested_at,
        service_id: expected.service_id,
        probe_id: expected.probe_id,
        freshness_max_age_ms: descriptor.freshness_max_age_ms,
        signal
      }))
    } catch (error) {
      return { ok: false, failure: error?.name === 'AbortError' ? 'deadline' : 'unavailable' }
    }
    if (!isPlainObject(value)) fail('probe_executor_observer_result_invalid')
    return { ok: true, value }
  })
}

const TRANSPORT_HANDLERS = Object.freeze({
  http: executeHttpTarget,
  websocket: executeObserverTarget,
  module_status: executeObserverTarget,
  private_status: executeObserverTarget
})

const successfulIdentity = (value) => isPlainObject(value) && (
  value.ok === true || ['ok', 'ready', 'healthy', 'running'].includes(value.status)
)

const exactServiceHealthV1 = (value, expected) => isPlainObject(value) &&
  Object.keys(value).length === 4 &&
  value.schema_version === `${expected.probe_id}.v1` &&
  value.ok === true &&
  value.status === 'ready' &&
  value.service_id === expected.service_id

const failure = (reason) => ({ semantic_class: 'not_ready', reason_class: reason, source_observed_at: null })
const success = (descriptor, sourceObservedAt = null, semanticClass = null) => ({
  semantic_class: semanticClass || descriptor.success_semantic_classes[0],
  reason_class: semanticClass === 'degraded_operational' ? 'optional_degraded' : 'none',
  source_observed_at: sourceObservedAt
})

const firstTransportFailure = (descriptor, outcomes) => {
  const failed = outcomes.find((item) => !item.outcome.ok)
  if (!failed) return null
  if (failed.outcome.failure === 'deadline') return failure('deadline')
  if (failed.outcome.failure === 'invalid') {
    return failure(descriptor.no_touch ? 'version_invalid' : 'response_invalid')
  }
  return failure(descriptor.no_touch ? 'version_unavailable' : 'health_unavailable')
}

const classifyHttp = (descriptor, outcomes, expected, nowMs, options) => {
  const transportFailure = firstTransportFailure(descriptor, outcomes)
  if (transportFailure) return transportFailure
  const non2xx = outcomes.find((item) => !item.outcome.status_ok)
  if (non2xx) {
    if (descriptor.checks.includes('environment_ready_contract') &&
        non2xx.target.target_id === 'ready' && non2xx.outcome.value?.ready === false) {
      return failure('environment_not_ready')
    }
    return failure(descriptor.no_touch ? 'version_unavailable' : 'health_unavailable')
  }
  if (descriptor.checks.includes('bounded_text') && outcomes.some((item) =>
    typeof item.outcome.value !== 'string' || item.outcome.value.trim().length === 0)) {
    return failure(descriptor.no_touch ? 'version_invalid' : 'response_invalid')
  }
  if (descriptor.checks.includes('service_identity')) {
    const health = outcomes.find((item) => item.target.target_id === 'health') || outcomes[0]
    if (!successfulIdentity(health.outcome.value)) return failure('service_identity_invalid')
  }
  if (descriptor.checks.includes('exact_service_health_v1')) {
    const health = outcomes.find((item) => item.target.target_id === 'health') || outcomes[0]
    if (!exactServiceHealthV1(health.outcome.value, expected)) return failure('service_identity_invalid')
  }
  let sourceObservedAt = null
  if (descriptor.checks.includes('environment_ready_contract')) {
    const ready = outcomes.find((item) => item.target.target_id === 'ready')?.outcome.value
    if (!isPlainObject(ready) || ready.ok !== true || ready.ready !== true || ready.status !== 'ready') {
      return failure('environment_not_ready')
    }
    const identity = options.environmentReadyIdentity
    const returned = ready.camera_requirement
    if (!isPlainObject(identity) || !isPlainObject(returned) ||
        returned.requirement_id !== 'camera_hub' ||
        returned.profile_id !== identity.profile_id ||
        returned.effective_config_sha256 !== identity.effective_config_sha256 ||
        returned.policy !== identity.camera_policy ||
        (identity.camera_policy === 'camera_excluded_by_profile' &&
          (returned.requirement !== 'not_required' || returned.result !== 'camera_excluded_by_profile')) ||
        (identity.camera_policy === 'required' &&
          (returned.requirement !== 'required' || returned.result !== 'ready'))) {
      return failure('environment_not_ready')
    }
  }
  if (descriptor.checks.includes('environment_current_schema')) {
    const current = outcomes.find((item) => item.target.target_id === 'current')?.outcome.value
    const sourceObservedAtNormalized = normalizeEnvironmentSourceTimestamp(current?.observed_at)
    if (!isPlainObject(current) || current.schema_version !== 1 ||
        !isStrictJsonObject(current.sources) || typeof current.stale !== 'boolean' ||
        sourceObservedAtNormalized === null) {
      return failure('environment_current_invalid')
    }
    sourceObservedAt = sourceObservedAtNormalized
    if (descriptor.checks.includes('configured_source_policy')) {
      const configuredSourceState = classifyConfiguredEnvironmentSources(current.sources, options.environmentReadyIdentity)
      if (configuredSourceState === 'invalid') return failure('environment_current_invalid')
      if (configuredSourceState === 'unready') return failure('configured_source_unready')
    }
  }
  return success(descriptor, sourceObservedAt || iso(nowMs))
}

const classifyWebsocket = (descriptor, outcomes, expected, nowMs) => {
  const transportFailure = firstTransportFailure(descriptor, outcomes)
  if (transportFailure) return transportFailure.reason_class === 'health_unavailable'
    ? failure('websocket_unavailable')
    : transportFailure
  const value = outcomes[0]?.outcome.value
  if (!isPlainObject(value) || value.ok !== true) return failure('websocket_unavailable')
  return success(descriptor, value.source_observed_at || iso(nowMs),
    value.operational_class === 'degraded' && descriptor.degraded_allowed ? 'degraded_operational' : null)
}

const classifyModuleStatus = (descriptor, outcomes, expected, nowMs) => {
  const transportFailure = firstTransportFailure(descriptor, outcomes)
  if (transportFailure) return transportFailure.reason_class === 'health_unavailable'
    ? failure('module_status_missing')
    : transportFailure
  const value = outcomes[0]?.outcome.value
  if (!isPlainObject(value)) return failure('module_status_missing')
  const sourceMs = strictTimestampMillis(value.timestamp)
  if (value.ok !== true || value.operation_id !== expected.operation_id ||
      !['running', 'ready'].includes(value.state) || sourceMs === null) {
    return failure('module_status_invalid')
  }
  if (nowMs - sourceMs > descriptor.freshness_max_age_ms) return failure('source_stale')
  return success(descriptor, value.timestamp)
}

const classifyCamera = (descriptor, outcomes, expected, nowMs) => {
  const transportFailure = firstTransportFailure(descriptor, outcomes)
  if (transportFailure) return transportFailure
  const privateStatus = outcomes.find((item) => item.target.transport === 'private_status')?.outcome.value
  const websocket = outcomes.find((item) => item.target.transport === 'websocket')?.outcome.value
  if (!isPlainObject(privateStatus)) return failure('registry_invalid')
  if (privateStatus.ownership_matched !== true) return failure('ownership_invalid')
  if (privateStatus.registry_matched !== true) return failure('registry_invalid')
  if (privateStatus.manifest_valid !== true) return failure('registry_invalid')
  if (privateStatus.lineage_matched !== true) return failure('lineage_invalid')
  if (privateStatus.listener_matched !== true) return failure('listener_unavailable')
  if (!isPlainObject(websocket) || websocket.ok !== true) return failure('websocket_unavailable')
  const sourceMs = strictTimestampMillis(privateStatus.source_observed_at)
  if (sourceMs === null || nowMs - sourceMs > descriptor.freshness_max_age_ms) return failure('source_stale')
  if (privateStatus.operational_class === 'ready') return success(descriptor, privateStatus.source_observed_at)
  if (privateStatus.operational_class === 'degraded' && descriptor.degraded_allowed) {
    return success(descriptor, privateStatus.source_observed_at, 'degraded_operational')
  }
  return failure('camera_not_operational')
}

const CLASSIFIERS = Object.freeze({
  http_json: classifyHttp,
  http_text: classifyHttp,
  composite_http_json: classifyHttp,
  websocket: classifyWebsocket,
  module_status: classifyModuleStatus,
  camera_composite: classifyCamera
})

class LauncherProbeExecutor {
  constructor ({ probeAuthority, environmentReadyIdentity, privateTargetResolver, fetchImpl = globalThis.fetch, observers = {}, clock = Date.now, monotonicClock = () => performance.now() }) {
    if (!probeAuthority || typeof fetchImpl !== 'function' || typeof clock !== 'function' ||
        typeof monotonicClock !== 'function' || !isPlainObject(observers) ||
        !isPlainObject(environmentReadyIdentity) ||
        typeof environmentReadyIdentity.profile_id !== 'string' ||
        typeof environmentReadyIdentity.effective_config_sha256 !== 'string' ||
        !/^[a-f0-9]{64}$/u.test(environmentReadyIdentity.effective_config_sha256) ||
        !['required', 'camera_excluded_by_profile'].includes(environmentReadyIdentity.camera_policy)) {
      fail('probe_executor_options_invalid')
    }
    this.probeAuthority = probeAuthority
    this.options = Object.freeze({
      privateTargetResolver,
      environmentReadyIdentity: Object.freeze({ ...environmentReadyIdentity }),
      fetchImpl,
      observers: Object.freeze({
        websocket: observers.websocket,
        module_status: observers.module_status,
        private_status: observers.private_status
      }),
      clock,
      monotonicClock
    })
  }

  async execute (expected) {
    const requestedMs = strictTimestampMillis(expected?.requested_at)
    if (requestedMs === null) {
      // bindProbeResult owns the public contract error for malformed identities.
      return bindProbeResult(this.probeAuthority, expected, {}, { now: 0 })
    }
    const preflightObservation = {
      schema_version: 'launcher_probe_observation.v1',
      message_type: 'observation',
      ...expected,
      observed_at: expected.requested_at,
      source_observed_at: expected.requested_at,
      semantic_class: 'not_ready',
      reason_class: 'internal_failure'
    }
    bindProbeResult(this.probeAuthority, expected, preflightObservation, { now: requestedMs })
    const descriptor = this.probeAuthority.byPair[`${expected.service_id}:${expected.probe_id}`]
    const started = monotonicMillis(this.options.monotonicClock)
    const outcomes = []
    for (const target of descriptor.targets) {
      const elapsed = monotonicMillis(this.options.monotonicClock) - started
      if (elapsed < 0) fail('probe_executor_clock_invalid')
      const remaining = Math.max(1, Math.ceil(descriptor.observation_timeout_ms - elapsed))
      if (elapsed >= descriptor.observation_timeout_ms) {
        outcomes.push({ target, outcome: { ok: false, failure: 'deadline' } })
        break
      }
      const handler = TRANSPORT_HANDLERS[target.transport]
      if (typeof handler !== 'function') fail('probe_executor_internal_failure')
      const outcome = await handler({
        target,
        expected,
        descriptor,
        options: this.options,
        timeoutMs: remaining
      })
      outcomes.push({ target, outcome })
      if (!outcome.ok) break
    }
    const nowMs = clockMillis(this.options.clock)
    const classifier = CLASSIFIERS[descriptor.probe_class]
    if (typeof classifier !== 'function') fail('probe_executor_internal_failure')
    let classification
    try {
      classification = classifier(descriptor, outcomes, expected, nowMs, this.options)
    } catch (error) {
      if (error instanceof LauncherProbeExecutorError) throw error
      fail('probe_executor_observer_result_invalid')
    }
    let observedMs = Math.max(requestedMs, nowMs)
    if (observedMs - requestedMs > descriptor.observation_timeout_ms) {
      classification = failure('deadline')
      observedMs = requestedMs + descriptor.observation_timeout_ms
    }
    let sourceObservedAt = classification.source_observed_at
    const sourceMs = strictTimestampMillis(sourceObservedAt)
    const allowsPreRequestSource =
      descriptor.freshness_source === 'environment_current_observed_at'
    if (sourceMs === null || (!allowsPreRequestSource && sourceMs < requestedMs) ||
        sourceMs > observedMs ||
        observedMs - sourceMs > descriptor.freshness_max_age_ms) {
      if (classification.semantic_class !== 'not_ready') classification = failure('source_stale')
      sourceObservedAt = iso(observedMs)
    }
    const observation = {
      schema_version: 'launcher_probe_observation.v1',
      message_type: 'observation',
      ...expected,
      observed_at: iso(observedMs),
      source_observed_at: sourceObservedAt,
      semantic_class: classification.semantic_class,
      reason_class: classification.reason_class
    }
    return bindProbeResult(this.probeAuthority, expected, observation, { now: observedMs })
  }
}

module.exports = {
  LauncherProbeExecutor,
  LauncherProbeExecutorError,
  MAX_BODY_BYTES
}
