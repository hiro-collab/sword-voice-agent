'use strict'

const path = require('node:path')

const {
  assertAuthority,
  canonicalJsonSha256,
  canonicalLfSha256,
  deepFreeze,
  readBoundedUtf8Text
} = require('./launcher-supervisor-contract')

const MAX_AUTHORITY_BYTES = 1024 * 1024
const MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER
const PROBE_AUTHORITY_TOKEN = Symbol('launcher_probe_authority_v1')
const ID = /^[a-z][a-z0-9_-]{0,63}$/u
const OPERATION_ID = /^lop_[a-z0-9]{8,64}$/u
const DISPATCH_ID = /^ld_[a-z0-9]{16,64}$/u
const SHA256 = /^[a-f0-9]{64}$/u
const CLOCK_SKEW_MS = 1000

const SEMANTIC_CLASSES = Object.freeze([
  'ready', 'reachable', 'external_ready', 'degraded_operational', 'not_ready'
])
const REASON_CLASSES = Object.freeze([
  'none', 'health_unavailable', 'response_invalid', 'service_identity_invalid',
  'environment_not_ready', 'environment_current_invalid', 'configured_source_unready',
  'module_status_missing', 'module_status_invalid', 'source_stale', 'ownership_invalid',
  'registry_invalid', 'lineage_invalid', 'listener_unavailable', 'websocket_unavailable',
  'camera_not_operational', 'version_unavailable', 'version_invalid', 'optional_degraded',
  'deadline', 'internal_failure'
])
const CHECKS = Object.freeze([
  'http_2xx', 'bounded_json', 'bounded_text', 'service_identity',
  'environment_ready_contract', 'environment_current_schema', 'configured_source_policy',
  'worker_owned_identity', 'module_status_shape', 'module_status_current_operation',
  'module_status_freshness', 'registry_identity', 'manifest_shape', 'manifest_lineage',
  'listener_lineage', 'websocket_handshake', 'camera_operational_class',
  'no_touch_external', 'world_freshness_advisory'
])
const PROOF_CEILINGS = Object.freeze([
  'home_bridge_reachability_only', 'environment_semantic_readiness_no_world_truth',
  'broker_reachability_only', 'thought_core_reachability_only',
  'camera_operational_no_media', 'vision_input_reachability_no_world_truth',
  'aituber_http_reachability_only', 'watcher_current_operation_liveness_only',
  'display_gui_reachability_only', 'voicevox_version_reachability_only'
])
const PROBE_CLASSES = Object.freeze([
  'http_json', 'http_text', 'composite_http_json', 'module_status', 'camera_composite', 'websocket'
])
const FRESHNESS_SOURCES = Object.freeze([
  'probe_observed_at', 'environment_current_observed_at', 'module_status_timestamp', 'camera_manifest_updated_at'
])
const SUCCESS_CLASSES = Object.freeze(SEMANTIC_CLASSES.filter((value) => value !== 'not_ready'))

class LauncherProbeContractError extends Error {
  constructor(code) {
    super(code)
    this.name = 'LauncherProbeContractError'
    this.code = code
  }
}

const fail = (code) => {
  throw new LauncherProbeContractError(code)
}

const isPlainObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const exactKeys = (value, keys, code) => {
  if (!isPlainObject(value)) fail(code)
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(code)
}

const requireStringPattern = (value, pattern, code) => {
  if (typeof value !== 'string' || !pattern.test(value)) fail(code)
  return value
}

const requireEnum = (value, allowed, code) => {
  if (typeof value !== 'string' || !allowed.includes(value)) fail(code)
  return value
}

const requireInteger = (value, minimum, maximum, code) => {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(code)
  return value
}

const requireBoolean = (value, code) => {
  if (typeof value !== 'boolean') fail(code)
  return value
}

const requireUniqueEnumArray = (value, allowed, minimum, maximum, code) => {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) fail(code)
  for (const item of value) requireEnum(item, allowed, code)
  if (new Set(value).size !== value.length) fail(code)
  return value
}

const sameArray = (left, right) => Array.isArray(left) && left.length === right.length &&
  left.every((value, index) => value === right[index])

const sameSet = (left, right) => Array.isArray(left) && left.length === right.length &&
  left.every((value) => right.includes(value))

const parseStrictTimestamp = (value, code) => {
  if (typeof value !== 'string' || value.length > 32) fail(code)
  const time = Date.parse(value)
  if (!Number.isFinite(time) || new Date(time).toISOString() !== value) fail(code)
  return time
}

const parseJson = (text, code) => {
  try {
    return JSON.parse(text)
  } catch {
    fail(code)
  }
}

const readJson = (filePath, readCode, parseCode) => {
  let text
  try {
    text = readBoundedUtf8Text(filePath, MAX_AUTHORITY_BYTES, readCode, 'probe_authority_oversized')
  } catch {
    fail(readCode)
  }
  return { value: parseJson(text, parseCode), sha256: canonicalLfSha256(text) }
}

const validateSchemaAuthority = (schema) => {
  if (!isPlainObject(schema) || schema.$schema !== 'https://json-schema.org/draft/2020-12/schema' ||
      schema.$id !== 'https://sword.local/contracts/launcher/launcher-probe-descriptor.v1.schema.json') {
    fail('probe_schema_identity_invalid')
  }
  const defs = schema.$defs
  if (!isPlainObject(defs) || !sameArray(defs.semantic_class?.enum, SEMANTIC_CLASSES) ||
      !sameArray(defs.reason_class?.enum, REASON_CLASSES) || !sameArray(defs.check?.enum, CHECKS) ||
      !sameArray(defs.proof_ceiling?.enum, PROOF_CEILINGS)) fail('probe_schema_enums_invalid')
  if (defs.document?.properties?.schema_version?.const !== 'launcher_probe_descriptors.v1' ||
      defs.observation?.unevaluatedProperties !== false ||
      defs.result?.unevaluatedProperties !== false ||
      defs.target?.properties?.path?.oneOf?.[0]?.pattern !== '^(?!//)/[^\\u0000\\r\\n]{0,255}$') {
    fail('probe_schema_shape_invalid')
  }
  const identityRequired = [
    'operation_id', 'supervisor_generation', 'dispatch_id', 'expected_revision',
    'service_id', 'probe_id', 'graph_sha256', 'binding_sha256', 'descriptor_sha256',
    'config_sha256', 'requested_at'
  ]
  if (!sameSet(defs.identity?.required, identityRequired)) fail('probe_schema_identity_shape_invalid')
  return schema
}

const validateTarget = (target, service, targetIds) => {
  exactKeys(target, [
    'target_id', 'transport', 'method', 'endpoint_ref', 'path', 'auth_class', 'response_class'
  ], 'probe_target_shape_invalid')
  requireStringPattern(target.target_id, ID, 'probe_target_id_invalid')
  if (targetIds.has(target.target_id)) fail('probe_target_duplicate')
  targetIds.add(target.target_id)
  requireEnum(target.transport, ['http', 'websocket', 'module_status', 'private_status'], 'probe_target_transport_invalid')
  requireEnum(target.method, ['GET', 'CONNECT', 'NONE'], 'probe_target_method_invalid')
  requireEnum(target.auth_class, ['none', 'private_secret_ref', 'not_applicable'], 'probe_target_auth_invalid')
  requireEnum(target.response_class, ['bounded_json', 'bounded_text', 'websocket_handshake'], 'probe_target_response_invalid')

  if (target.transport === 'http') {
    if (target.method !== 'GET' || target.response_class === 'websocket_handshake' ||
        typeof target.path !== 'string' || !/^\/[^\u0000\r\n]{0,255}$/u.test(target.path) || target.path.startsWith('//') ||
        target.endpoint_ref !== service.port.endpoint_ref) fail('probe_http_target_invalid')
  } else if (target.transport === 'websocket') {
    if (target.method !== 'CONNECT' || target.response_class !== 'websocket_handshake' ||
        typeof target.path !== 'string' || !/^\/[^\u0000\r\n]{0,255}$/u.test(target.path) || target.path.startsWith('//') ||
        target.endpoint_ref !== service.port.endpoint_ref || target.auth_class !== 'none') fail('probe_websocket_target_invalid')
  } else if (target.method !== 'NONE' || target.endpoint_ref !== null || target.path !== null ||
      target.auth_class !== 'not_applicable' || target.response_class !== 'bounded_json') {
    fail('probe_private_target_invalid')
  }
}

const validateDescriptor = (descriptor, service) => {
  exactKeys(descriptor, [
    'service_id', 'probe_id', 'probe_class', 'targets', 'checks', 'success_semantic_classes',
    'freshness_source', 'freshness_max_age_ms', 'observation_timeout_ms', 'degraded_allowed',
    'no_touch', 'world_freshness_startup_authoritative', 'proof_ceiling'
  ], 'probe_descriptor_shape_invalid')
  if (descriptor.service_id !== service.service_id) fail('probe_descriptor_service_invalid')
  requireStringPattern(descriptor.probe_id, ID, 'probe_descriptor_probe_invalid')
  if (descriptor.probe_id !== service.readiness.probe_id) fail('probe_descriptor_graph_probe_mismatch')
  requireEnum(descriptor.probe_class, PROBE_CLASSES, 'probe_descriptor_class_invalid')
  if (!Array.isArray(descriptor.targets) || descriptor.targets.length < 1 || descriptor.targets.length > 4) {
    fail('probe_descriptor_targets_invalid')
  }
  const targetIds = new Set()
  for (const target of descriptor.targets) validateTarget(target, service, targetIds)
  const endpointTargets = descriptor.targets.filter((target) => target.endpoint_ref !== null)
  if (service.port.endpoint_ref === null ? endpointTargets.length !== 0 : endpointTargets.length === 0) {
    fail('probe_descriptor_endpoint_coverage_invalid')
  }
  if (endpointTargets.some((target) => target.transport !== service.port.transport)) {
    fail('probe_descriptor_transport_mismatch')
  }
  requireUniqueEnumArray(descriptor.checks, CHECKS, 1, 16, 'probe_descriptor_checks_invalid')
  requireUniqueEnumArray(descriptor.success_semantic_classes, SUCCESS_CLASSES, 1, 4, 'probe_descriptor_success_invalid')
  requireEnum(descriptor.freshness_source, FRESHNESS_SOURCES, 'probe_descriptor_freshness_source_invalid')
  requireInteger(descriptor.freshness_max_age_ms, 1, 300000, 'probe_descriptor_freshness_invalid')
  requireInteger(descriptor.observation_timeout_ms, 1, 300000, 'probe_descriptor_timeout_invalid')
  requireBoolean(descriptor.degraded_allowed, 'probe_descriptor_degraded_invalid')
  requireBoolean(descriptor.no_touch, 'probe_descriptor_no_touch_invalid')
  requireBoolean(descriptor.world_freshness_startup_authoritative, 'probe_descriptor_world_freshness_invalid')
  requireEnum(descriptor.proof_ceiling, PROOF_CEILINGS, 'probe_descriptor_proof_ceiling_invalid')
  if (descriptor.degraded_allowed !== service.readiness.degraded_allowed ||
      descriptor.success_semantic_classes.includes('degraded_operational') !== descriptor.degraded_allowed) {
    fail('probe_descriptor_degraded_mismatch')
  }
  if (descriptor.world_freshness_startup_authoritative !== false) fail('probe_descriptor_world_truth_invalid')

  const external = service.ownership === 'external'
  if (descriptor.no_touch !== external || external !== descriptor.checks.includes('no_touch_external')) {
    fail('probe_descriptor_no_touch_mismatch')
  }
  if (external && descriptor.targets.some((target) => target.transport !== 'http' || target.method !== 'GET' || target.auth_class !== 'none')) {
    fail('probe_descriptor_external_target_invalid')
  }
  return descriptor
}

const validateProbeDocument = (document, launcherAuthority) => {
  assertAuthority(launcherAuthority)
  exactKeys(document, ['schema_version', 'profile_id', 'graph_sha256', 'descriptors'], 'probe_document_shape_invalid')
  if (document.schema_version !== 'launcher_probe_descriptors.v1' ||
      document.profile_id !== launcherAuthority.graph.profile_id) fail('probe_document_identity_invalid')
  requireStringPattern(document.graph_sha256, SHA256, 'probe_document_graph_hash_invalid')
  if (document.graph_sha256 !== launcherAuthority.identities.graphSha256) fail('probe_document_authority_drift')
  if (!Array.isArray(document.descriptors) || document.descriptors.length !== launcherAuthority.graph.services.length) {
    fail('probe_document_coverage_invalid')
  }

  const services = new Map(launcherAuthority.graph.services.map((service) => [service.service_id, service]))
  const pairs = new Set()
  const descriptorMap = new Map()
  for (const descriptor of document.descriptors) {
    if (!isPlainObject(descriptor) || typeof descriptor.service_id !== 'string') fail('probe_descriptor_shape_invalid')
    const service = services.get(descriptor.service_id)
    if (!service) fail('probe_descriptor_service_unknown')
    const pair = `${descriptor.service_id}\u0000${descriptor.probe_id}`
    if (pairs.has(pair) || descriptorMap.has(descriptor.service_id)) fail('probe_descriptor_duplicate')
    pairs.add(pair)
    descriptorMap.set(descriptor.service_id, validateDescriptor(descriptor, service))
  }
  for (const service of launcherAuthority.graph.services) {
    if (!descriptorMap.has(service.service_id)) fail('probe_document_coverage_invalid')
  }
  if (canonicalJsonSha256(document) !== canonicalJsonSha256(launcherAuthority.probeDocument)) {
    fail('probe_document_authority_drift')
  }
  return descriptorMap
}

const loadProbeAuthority = (repositoryRoot, launcherAuthority) => {
  if (typeof repositoryRoot !== 'string' || !path.isAbsolute(repositoryRoot)) fail('probe_authority_root_invalid')
  assertAuthority(launcherAuthority)
  const schemaSource = readJson(
    path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-probe-descriptor.v1.schema.json'),
    'probe_schema_read_failed', 'probe_schema_parse_failed'
  )
  const documentSource = readJson(
    path.join(repositoryRoot, 'ops', 'manifests', 'launcher-probe-descriptors.standard.v1.json'),
    'probe_document_read_failed', 'probe_document_parse_failed'
  )
  if (schemaSource.sha256 !== launcherAuthority.identities.probeSchemaSha256 ||
      documentSource.sha256 !== launcherAuthority.identities.probeDocumentSha256) {
    fail('probe_authority_drift')
  }
  validateSchemaAuthority(schemaSource.value)
  const descriptorMap = validateProbeDocument(documentSource.value, launcherAuthority)
  const descriptors = documentSource.value.descriptors.map((descriptor) => ({
    ...descriptor,
    descriptor_sha256: canonicalJsonSha256(descriptor)
  }))
  const byPair = Object.fromEntries(descriptors.map((descriptor) => [
    `${descriptor.service_id}:${descriptor.probe_id}`, descriptor
  ]))
  return deepFreeze({
    [PROBE_AUTHORITY_TOKEN]: true,
    schema: schemaSource.value,
    document: documentSource.value,
    descriptors,
    byPair,
    identities: {
      schemaSha256: schemaSource.sha256,
      documentSha256: documentSource.sha256,
      graphSha256: launcherAuthority.identities.graphSha256,
      bindingSha256: launcherAuthority.identities.bindingSha256
    },
    serviceIds: [...descriptorMap.keys()]
  })
}

const EXPECTED_KEYS = Object.freeze([
  'operation_id', 'supervisor_generation', 'dispatch_id', 'expected_revision',
  'service_id', 'probe_id', 'graph_sha256', 'binding_sha256', 'descriptor_sha256',
  'config_sha256', 'requested_at'
])
const OBSERVATION_KEYS = Object.freeze([
  'schema_version', 'message_type', ...EXPECTED_KEYS, 'observed_at', 'source_observed_at',
  'semantic_class', 'reason_class'
])

const validateIdentity = (identity, code) => {
  requireStringPattern(identity.operation_id, OPERATION_ID, code)
  requireInteger(identity.supervisor_generation, 1, MAX_SAFE_INTEGER, code)
  requireStringPattern(identity.dispatch_id, DISPATCH_ID, code)
  requireInteger(identity.expected_revision, 0, MAX_SAFE_INTEGER, code)
  requireStringPattern(identity.service_id, /^[a-z][a-z0-9_]{0,63}$/u, code)
  requireStringPattern(identity.probe_id, ID, code)
  for (const field of ['graph_sha256', 'binding_sha256', 'descriptor_sha256', 'config_sha256']) {
    requireStringPattern(identity[field], SHA256, code)
  }
  parseStrictTimestamp(identity.requested_at, code)
}

const bindProbeResult = (probeAuthority, expected, observation, options = {}) => {
  if (!isPlainObject(probeAuthority) || probeAuthority[PROBE_AUTHORITY_TOKEN] !== true ||
      !Object.isFrozen(probeAuthority) || !isPlainObject(probeAuthority.byPair)) {
    fail('probe_authority_not_validated')
  }
  exactKeys(expected, EXPECTED_KEYS, 'probe_expected_shape_invalid')
  validateIdentity(expected, 'probe_expected_identity_invalid')
  exactKeys(observation, OBSERVATION_KEYS, 'probe_observation_shape_invalid')
  validateIdentity(observation, 'probe_observation_identity_invalid')
  if (observation.schema_version !== 'launcher_probe_observation.v1' || observation.message_type !== 'observation') {
    fail('probe_observation_envelope_invalid')
  }
  for (const field of EXPECTED_KEYS) {
    if (observation[field] !== expected[field]) fail('probe_observation_identity_mismatch')
  }
  if (expected.graph_sha256 !== probeAuthority.identities.graphSha256 ||
      expected.binding_sha256 !== probeAuthority.identities.bindingSha256) fail('probe_expected_authority_drift')
  const descriptor = probeAuthority.byPair[`${expected.service_id}:${expected.probe_id}`]
  if (!descriptor || descriptor.descriptor_sha256 !== expected.descriptor_sha256) {
    fail('probe_expected_descriptor_mismatch')
  }

  requireEnum(observation.semantic_class, SEMANTIC_CLASSES, 'probe_observation_semantic_invalid')
  requireEnum(observation.reason_class, REASON_CLASSES, 'probe_observation_reason_invalid')
  const successful = descriptor.success_semantic_classes.includes(observation.semantic_class)
  if (observation.semantic_class === 'not_ready') {
    if (observation.reason_class === 'none' || observation.reason_class === 'optional_degraded') {
      fail('probe_observation_reason_mismatch')
    }
  } else if (!successful) {
    fail('probe_observation_semantic_mismatch')
  } else if (observation.semantic_class === 'degraded_operational') {
    if (!descriptor.degraded_allowed || observation.reason_class !== 'optional_degraded') {
      fail('probe_observation_degraded_mismatch')
    }
  } else if (observation.reason_class !== 'none') {
    fail('probe_observation_reason_mismatch')
  }

  const requestedAt = parseStrictTimestamp(expected.requested_at, 'probe_requested_at_invalid')
  const sourceObservedAt = parseStrictTimestamp(observation.source_observed_at, 'probe_source_observed_at_invalid')
  const observedAt = parseStrictTimestamp(observation.observed_at, 'probe_observed_at_invalid')
  let now
  if (options.now === undefined) now = Date.now()
  else if (options.now instanceof Date) now = options.now.getTime()
  else if (typeof options.now === 'string') now = parseStrictTimestamp(options.now, 'probe_now_invalid')
  else if (Number.isSafeInteger(options.now)) now = options.now
  else fail('probe_now_invalid')
  if (!Number.isFinite(now) || requestedAt > sourceObservedAt || sourceObservedAt > observedAt ||
      observedAt > now + CLOCK_SKEW_MS || requestedAt > now + CLOCK_SKEW_MS) fail('probe_observation_time_order_invalid')
  if (observedAt - requestedAt > descriptor.observation_timeout_ms) fail('probe_observation_deadline_exceeded')
  if (now - sourceObservedAt > descriptor.freshness_max_age_ms) fail('probe_observation_stale')

  return deepFreeze({
    schema_version: 'launcher_probe_result.v1',
    message_type: 'result',
    ...Object.fromEntries(EXPECTED_KEYS.map((field) => [field, expected[field]])),
    observed_at: observation.observed_at,
    source_observed_at: observation.source_observed_at,
    freshness_class: 'fresh',
    semantic_class: observation.semantic_class,
    reason_class: observation.reason_class,
    ready: successful,
    proof_ceiling: descriptor.proof_ceiling
  })
}

module.exports = {
  LauncherProbeContractError,
  bindProbeResult,
  loadProbeAuthority,
  validateProbeDocument,
  validateSchemaAuthority
}
