'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const AUTHORITY_TOKEN = Symbol('launcher_authority_v2')
const ID = /^[a-z][a-z0-9_-]{0,63}$/u
const OPERATION_ID = /^lop_[a-z0-9]{8,64}$/u
const SHA256 = /^[a-f0-9]{64}$/u
const WORKER_NONCE = /^lw_[a-z0-9]{16,64}$/u
const DISPATCH_ID = /^ld_[a-z0-9]{16,64}$/u
const AUTHORITY_LEASE_PROOF = /^lp_[a-f0-9]{64}$/u
const SERVICE_ID_PATTERN = '^[a-z][a-z0-9_]{0,63}$'
const MAX_CONTRACT_BYTES = 1024 * 1024
const MAX_LEGACY_SOURCE_BYTES = 4 * 1024 * 1024
const MAX_SERVICES = 64
const MAX_REDUCER_VECTORS = 256
const MAX_VECTOR_EVENTS = 256
const MAX_VECTOR_COVERAGE = 64
const STANDARD_PROFILE_ID = 'thought-core-v0'
const REDUCED_PROFILE_ID = 'core-rehearsal-text-bubble-v0'
const REDUCED_PARENT_PROFILE = Object.freeze({
  profile_id: 'core-rehearsal-text-bubble',
  source_commit: '6b09f62ea343dea0ac53bc9e8e466980d9971a4a',
  source_path: 'manifests/profiles/core-rehearsal-text-bubble.json',
  source_sha256: '1F8182AD80BA150D696869895D699121397EE5D088A3CCFACE3B5027B7829836'
})

class LauncherContractError extends Error {
  constructor(code, cleanupCode = 'none') {
    super(code)
    this.name = 'LauncherContractError'
    this.code = code
    this.cleanup_code = cleanupCode
  }
}

const fail = (code) => {
  throw new LauncherContractError(code)
}

const strictUtf8Text = (value, code = 'contract_text_utf8_invalid') => {
  if (!Buffer.isBuffer(value) && !(value instanceof Uint8Array)) return String(value)
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(value)
  } catch {
    fail(code)
  }
}

const canonicalLfText = (value) => {
  const text = strictUtf8Text(value)
  if (text.startsWith('\uFEFF')) fail('contract_text_bom_invalid')
  return text.replace(/\r\n?/gu, '\n')
}

const canonicalLfSha256 = (value) => crypto
  .createHash('sha256')
  .update(Buffer.from(canonicalLfText(value), 'utf8'))
  .digest('hex')

const canonicalJsonValue = (value) => {
  if (Array.isArray(value)) return `[${value.map(canonicalJsonValue).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJsonValue(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

const canonicalJsonSha256 = (value) => crypto
  .createHash('sha256')
  .update(Buffer.from(canonicalJsonValue(value), 'utf8'))
  .digest('hex')

const parseJsonText = (text, code) => {
  try {
    return JSON.parse(canonicalLfText(text))
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail(code)
  }
}

const readBoundedBytes = (filePath, maximumBytes, code, oversizedCode) => {
  let descriptor
  try {
    descriptor = fs.openSync(filePath, 'r')
    const stat = fs.fstatSync(descriptor)
    if (!stat.isFile()) fail(code)
    if (!Number.isSafeInteger(stat.size) || stat.size < 0 || stat.size > maximumBytes) fail(oversizedCode)
    const bytes = Buffer.alloc(stat.size)
    let offset = 0
    while (offset < bytes.length) {
      const count = fs.readSync(descriptor, bytes, offset, bytes.length - offset, offset)
      if (count === 0) fail(code)
      offset += count
    }
    const overflow = Buffer.alloc(1)
    if (fs.readSync(descriptor, overflow, 0, 1, offset) !== 0) fail(oversizedCode)
    return bytes
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail(code)
  } finally {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
  }
}

const readContract = (filePath, code) => {
  const bytes = readBoundedBytes(filePath, MAX_CONTRACT_BYTES, code, 'contract_file_oversized')
  return { value: parseJsonText(bytes, code), sha256: canonicalLfSha256(bytes) }
}

const readBoundedUtf8Text = (filePath, maximumBytes, readCode, oversizedCode) => {
  const bytes = readBoundedBytes(filePath, maximumBytes, readCode, oversizedCode)
  const text = strictUtf8Text(bytes, readCode)
  if (text.startsWith('\uFEFF')) fail(readCode)
  return text.replace(/\r\n?/gu, '\n')
}

const copyJson = (value) => JSON.parse(JSON.stringify(value))

const deepFreeze = (value, seen = new Set()) => {
  if (!value || typeof value !== 'object' || seen.has(value)) return value
  seen.add(value)
  for (const child of Object.values(value)) deepFreeze(child, seen)
  return Object.freeze(value)
}

const isPlainObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const exactKeys = (value, keys, code) => {
  if (!isPlainObject(value)) fail(code)
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(code)
}

const requireString = (value, code) => {
  if (typeof value !== 'string' || value.length === 0) fail(code)
  return value
}

const requireEnum = (value, allowed, code) => {
  requireString(value, code)
  if (!allowed.includes(value)) fail(code)
  return value
}

const CLEANUP_ATTEMPT_KEYS = [
  'sequence', 'target_class', 'responsible_id', 'outcome_class', 'reason_class',
  'termination_class', 'job_query_class', 'active_count_after', 'post_stop_listener_class'
]
const TERMINATION_CLASSES = ['forced_only', 'graceful', 'already_clear', 'not_applicable', 'unknown']
const JOB_QUERY_CLASSES = ['trusted', 'failed', 'not_applicable', 'unknown']
const POST_STOP_LISTENER_CLASSES = ['clear', 'foreign_present', 'unknown', 'not_applicable']
const CLEANUP_REASON_CLASSES = [
  'none', 'rollback_failed', 'stop_failed', 'unattempted_transport_unavailable',
  'legacy_missing', 'private_plan_cleanup_failed'
]

const hasClearServiceCleanupProof = (attempt) => Boolean(attempt) &&
  attempt.target_class === 'service' && attempt.outcome_class === 'clear' && attempt.reason_class === 'none' &&
  ['forced_only', 'already_clear'].includes(attempt.termination_class) &&
  attempt.job_query_class === 'trusted' && attempt.active_count_after === 0 &&
  ['clear', 'not_applicable'].includes(attempt.post_stop_listener_class)

const validateCleanupAttempt = (attempt, serviceIdPattern = SERVICE_ID_PATTERN) => {
  exactKeys(attempt, CLEANUP_ATTEMPT_KEYS, 'cleanup_attempt_shape_invalid')
  requireInteger(attempt.sequence, 1, 65, 'cleanup_attempt_sequence_invalid')
  requireEnum(attempt.target_class, ['service', 'private_plan'], 'cleanup_attempt_target_invalid')
  requireEnum(attempt.outcome_class, ['clear', 'failed', 'unattempted'], 'cleanup_attempt_outcome_invalid')
  requireEnum(attempt.reason_class, CLEANUP_REASON_CLASSES, 'cleanup_attempt_reason_invalid')
  requireEnum(attempt.termination_class, TERMINATION_CLASSES, 'cleanup_attempt_termination_invalid')
  requireEnum(attempt.job_query_class, JOB_QUERY_CLASSES, 'cleanup_attempt_job_query_invalid')
  requireEnum(attempt.post_stop_listener_class, POST_STOP_LISTENER_CLASSES, 'cleanup_attempt_listener_invalid')
  if (attempt.job_query_class === 'trusted') {
    requireInteger(attempt.active_count_after, 0, 4294967295, 'cleanup_attempt_active_count_invalid')
  } else if (attempt.active_count_after !== null) fail('cleanup_attempt_active_count_invalid')
  if (attempt.target_class === 'service') {
    requireServiceId(attempt.responsible_id, serviceIdPattern, 'cleanup_attempt_responsible_invalid')
  } else if (attempt.responsible_id !== 'launcher_supervisor' ||
      attempt.termination_class !== 'not_applicable' || attempt.job_query_class !== 'not_applicable' ||
      attempt.active_count_after !== null || attempt.post_stop_listener_class !== 'not_applicable') {
    fail('cleanup_attempt_private_plan_invalid')
  }
  if (attempt.outcome_class === 'clear' && attempt.reason_class !== 'none') fail('cleanup_attempt_reason_invalid')
  if (attempt.outcome_class === 'failed' && !['rollback_failed', 'stop_failed', 'private_plan_cleanup_failed'].includes(attempt.reason_class)) {
    fail('cleanup_attempt_reason_invalid')
  }
  if (attempt.outcome_class === 'unattempted' &&
      !['unattempted_transport_unavailable', 'legacy_missing'].includes(attempt.reason_class)) {
    fail('cleanup_attempt_reason_invalid')
  }
  if (attempt.target_class === 'service' && attempt.outcome_class === 'clear' && !hasClearServiceCleanupProof(attempt)) {
    fail('cleanup_attempt_clear_proof_invalid')
  }
  return attempt
}

const requireId = (value, code) => {
  if (typeof value !== 'string' || !ID.test(value)) fail(code)
  return value
}

const serviceIdPatternFromOperationSchema = (schema) => {
  const definition = schema?.$defs?.service_id
  if (!isPlainObject(definition) || definition.type !== 'string' || definition.pattern !== SERVICE_ID_PATTERN) {
    fail('operation_service_id_pattern_invalid')
  }
  return definition.pattern
}

const requireServiceId = (value, serviceIdPattern, code) => {
  if (serviceIdPattern !== SERVICE_ID_PATTERN || typeof value !== 'string' || !(new RegExp(serviceIdPattern, 'u')).test(value)) fail(code)
  return value
}

const requireOperationId = (value, code = 'operation_id_invalid') => {
  if (typeof value !== 'string' || !OPERATION_ID.test(value)) fail(code)
  return value
}

const requireSha = (value, code) => {
  if (typeof value !== 'string' || !SHA256.test(value)) fail(code)
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

const requireIdArray = (value, code) => {
  if (!Array.isArray(value)) fail(code)
  const result = value.map((item) => requireId(item, code))
  if (new Set(result).size !== result.length) fail(code)
  return result
}

const requireServiceIdArray = (value, serviceIdPattern, code) => {
  if (!Array.isArray(value) || value.length > MAX_SERVICES) fail(code)
  const result = value.map((item) => requireServiceId(item, serviceIdPattern, code))
  if (new Set(result).size !== result.length) fail(code)
  return result
}

const topologicalOrder = (services) => {
  const remaining = new Map(services.map((service) => [service.service_id, new Set(service.dependencies)]))
  const result = []
  while (remaining.size > 0) {
    const ready = [...remaining].filter(([, dependencies]) => dependencies.size === 0).map(([id]) => id).sort()
    if (ready.length === 0) fail('graph_dependency_cycle')
    for (const id of ready) {
      result.push(id)
      remaining.delete(id)
      for (const dependencies of remaining.values()) dependencies.delete(id)
    }
  }
  return result
}

const validateGraph = (graph, serviceIdPattern) => {
  if (serviceIdPattern !== SERVICE_ID_PATTERN) fail('operation_service_id_pattern_invalid')
  exactKeys(graph, ['schema_version', 'profile_id', 'start_policy', 'stop_policy', 'services'], 'graph_unknown_or_missing_field')
  if (graph.schema_version !== 'launcher_service_graph.v1') fail('graph_schema_version_invalid')
  requireId(graph.profile_id, 'graph_profile_id_invalid')
  exactKeys(graph.start_policy, ['duplicate_start', 'optional_absent'], 'graph_start_policy_invalid')
  if (graph.start_policy.duplicate_start !== 'join_active' || graph.start_policy.optional_absent !== 'continue_degraded') fail('graph_start_policy_invalid')
  exactKeys(graph.stop_policy, ['idempotent', 'residue'], 'graph_stop_policy_invalid')
  if (graph.stop_policy.idempotent !== true || graph.stop_policy.residue !== 'retain_fixed_state') fail('graph_stop_policy_invalid')
  if (!Array.isArray(graph.services) || graph.services.length === 0 || graph.services.length > MAX_SERVICES) fail('graph_services_invalid')

  const ids = new Set()
  const publicIds = new Set()
  for (const service of graph.services) {
    exactKeys(service, [
      'service_id', 'public_readiness_id', 'dependencies', 'requirement', 'ownership',
      'legacy_profile_member', 'start', 'readiness', 'ready_deadline_ms', 'port', 'stop'
    ], 'graph_service_shape_invalid')
    requireServiceId(service.service_id, serviceIdPattern, 'graph_service_id_invalid')
    if (ids.has(service.service_id)) fail('graph_service_duplicate')
    ids.add(service.service_id)
    if (service.public_readiness_id !== null) {
      requireId(service.public_readiness_id, 'graph_public_id_invalid')
      if (publicIds.has(service.public_readiness_id)) fail('graph_public_id_duplicate')
      publicIds.add(service.public_readiness_id)
    }
    requireServiceIdArray(service.dependencies, serviceIdPattern, 'graph_dependencies_invalid')
    requireEnum(service.requirement, ['required', 'optional', 'external'], 'graph_requirement_invalid')
    requireEnum(service.ownership, ['owned', 'external'], 'graph_ownership_invalid')
    requireBoolean(service.legacy_profile_member, 'graph_legacy_profile_invalid')

    exactKeys(service.start, ['adapter_id', 'absent_behavior', 'legacy_spec_ids'], 'graph_start_shape_invalid')
    requireEnum(service.start.adapter_id, ['job_worker_service', 'external_probe_only'], 'graph_start_adapter_invalid')
    requireEnum(service.start.absent_behavior, ['fail', 'optional_absent', 'external_probe_only'], 'graph_absent_behavior_invalid')
    requireServiceIdArray(service.start.legacy_spec_ids, serviceIdPattern, 'graph_legacy_specs_invalid')

    exactKeys(service.readiness, ['probe_id', 'success', 'degraded_allowed'], 'graph_readiness_shape_invalid')
    requireId(service.readiness.probe_id, 'graph_probe_id_invalid')
    requireEnum(service.readiness.success, ['owned_identity_and_probe', 'external_probe', 'module_status'], 'graph_readiness_success_invalid')
    requireBoolean(service.readiness.degraded_allowed, 'graph_readiness_degraded_invalid')
    requireInteger(service.ready_deadline_ms, 1, 300000, 'graph_ready_deadline_invalid')

    exactKeys(service.port, ['ownership', 'port_mode', 'transport', 'loopback_port', 'endpoint_ref'], 'graph_port_shape_invalid')
    requireEnum(service.port.ownership, ['owned', 'external', 'none'], 'graph_port_ownership_invalid')
    requireEnum(service.port.port_mode, ['manifest_default', 'launcher_default', 'none'], 'graph_port_mode_invalid')
    requireEnum(service.port.transport, ['http', 'websocket', 'none'], 'graph_port_transport_invalid')
    if (service.port.loopback_port !== null) requireInteger(service.port.loopback_port, 1, 65535, 'graph_loopback_port_invalid')
    if (service.port.endpoint_ref !== null) requireId(service.port.endpoint_ref, 'graph_endpoint_ref_invalid')

    exactKeys(service.stop, ['adapter_id', 'graceful_timeout_ms', 'escalation'], 'graph_stop_shape_invalid')
    requireEnum(service.stop.adapter_id, ['job_worker_job_close', 'external_noop'], 'graph_stop_adapter_invalid')
    requireInteger(service.stop.graceful_timeout_ms, 0, 120000, 'graph_stop_deadline_invalid')
    requireEnum(service.stop.escalation, ['owned_only', 'none'], 'graph_stop_escalation_invalid')

    const noPort = service.port.ownership === 'none'
    if (noPort !== (service.port.endpoint_ref === null) || noPort !== (service.port.loopback_port === null) ||
        noPort !== (service.port.transport === 'none') || noPort !== (service.port.port_mode === 'none')) fail('graph_port_semantics_invalid')

    if (service.requirement === 'external' || service.ownership === 'external') {
      if (service.requirement !== 'external' || service.ownership !== 'external' || service.legacy_profile_member ||
          service.start.adapter_id !== 'external_probe_only' || service.start.absent_behavior !== 'external_probe_only' ||
          service.start.legacy_spec_ids.length !== 0 || service.readiness.success !== 'external_probe' || service.readiness.degraded_allowed ||
          service.port.ownership !== 'external' || service.port.port_mode !== 'launcher_default' || noPort ||
          service.stop.adapter_id !== 'external_noop' || service.stop.graceful_timeout_ms !== 0 || service.stop.escalation !== 'none') {
        fail('graph_external_semantics_invalid')
      }
      continue
    }

    if (service.ownership !== 'owned' || !service.legacy_profile_member || service.start.adapter_id !== 'job_worker_service' ||
        !service.start.legacy_spec_ids.includes(service.service_id) || service.stop.adapter_id !== 'job_worker_job_close' ||
        service.stop.escalation !== 'owned_only' || service.port.ownership === 'external' ||
        (!noPort && service.port.port_mode !== 'manifest_default')) fail('graph_owned_semantics_invalid')
    if (service.requirement === 'required') {
      if (service.start.absent_behavior !== 'fail' || service.readiness.degraded_allowed) fail('graph_required_semantics_invalid')
    } else if (service.requirement === 'optional') {
      if (service.start.absent_behavior !== 'optional_absent' || !service.readiness.degraded_allowed ||
          service.readiness.success !== 'owned_identity_and_probe' || noPort) fail('graph_optional_semantics_invalid')
    } else {
      fail('graph_requirement_invalid')
    }
    if ((service.readiness.success === 'module_status') !== noPort ||
        (!noPort && service.readiness.success !== 'owned_identity_and_probe')) fail('graph_readiness_port_semantics_invalid')
  }
  for (const service of graph.services) {
    for (const dependency of service.dependencies) {
      if (!ids.has(dependency)) fail('graph_dependency_unknown')
      if (dependency === service.service_id) fail('graph_dependency_cycle')
    }
  }
  topologicalOrder(graph.services)
  return graph
}

const validateStrictSchemaEnvelope = (schema, idSuffix, code) => {
  if (!isPlainObject(schema) || schema.$schema !== 'https://json-schema.org/draft/2020-12/schema' ||
      typeof schema.$id !== 'string' || !schema.$id.endsWith(idSuffix)) fail(code)
}

const validateSchemaAuthority = (graphSchema, operationSchema, workerSchema) => {
  const serviceIdPattern = serviceIdPatternFromOperationSchema(operationSchema)
  const graphServiceRef = 'launcher-operation.v1.schema.json#/$defs/service_id'
  const workerServiceRef = 'launcher-operation.v2.schema.json#/$defs/service_id'
  const graphService = graphSchema?.$defs?.service?.properties
  const operationService = operationSchema?.properties?.services?.items
  const workerRequest = workerSchema?.$defs?.request?.properties
  const workerResult = workerSchema?.$defs?.result?.properties
  if (graphService?.service_id?.$ref !== graphServiceRef || graphService?.dependencies?.items?.$ref !== graphServiceRef ||
      graphService?.start?.properties?.legacy_spec_ids?.items?.$ref !== graphServiceRef ||
      workerRequest?.service_id?.$ref !== workerServiceRef || workerResult?.service_id?.$ref !== workerServiceRef) {
    fail('service_id_schema_authority_drift')
  }
  if (!Array.isArray(operationService?.required) ||
      !['probe_status', 'probe_expected_revision', 'last_probe_result'].every((field) => operationService.required.includes(field)) ||
      operationService?.properties?.last_probe_result?.oneOf?.[1]?.$ref !== 'launcher-probe-descriptor.v1.schema.json#/$defs/result') {
    fail('operation_probe_result_schema_drift')
  }
  const maximum = Number.MAX_SAFE_INTEGER
  if (operationSchema?.properties?.revision?.maximum !== maximum ||
      workerRequest?.expected_revision?.maximum !== maximum || workerResult?.expected_revision?.maximum !== maximum) {
    fail('revision_schema_runtime_drift')
  }
  return serviceIdPattern
}

const validateBinding = (document, identities, graph) => {
  exactKeys(document, ['binding_sha256', 'binding'], 'binding_shape_invalid')
  requireSha(document.binding_sha256, 'binding_sha256_invalid')
  const binding = document.binding
  exactKeys(binding, [
    'binding_version', 'text_hash_mode', 'profile_id', 'graph_sha256', 'graph_schema_sha256',
    'operation_schema_sha256', 'worker_schema_sha256', 'reducer_vectors_sha256',
    'probe_schema_sha256', 'probe_document_sha256', 'service_order',
    'public_readiness_ids', 'required_service_ids', 'optional_service_ids', 'external_service_ids'
  ], 'binding_body_shape_invalid')
  if (binding.binding_version !== 'launcher_service_graph.binding.v2' || binding.text_hash_mode !== 'utf8_lf_v1' || binding.profile_id !== graph.profile_id) fail('binding_identity_invalid')
  for (const field of [
    'graph_sha256', 'graph_schema_sha256', 'operation_schema_sha256', 'worker_schema_sha256',
    'reducer_vectors_sha256', 'probe_schema_sha256', 'probe_document_sha256'
  ]) requireSha(binding[field], 'binding_source_sha256_invalid')
  if (binding.graph_sha256 !== identities.graphSha256 || binding.graph_schema_sha256 !== identities.graphSchemaSha256 ||
      binding.operation_schema_sha256 !== identities.operationSchemaSha256 || binding.worker_schema_sha256 !== identities.workerSchemaSha256 ||
      binding.reducer_vectors_sha256 !== identities.reducerVectorsSha256 ||
      binding.probe_schema_sha256 !== identities.probeSchemaSha256 ||
      binding.probe_document_sha256 !== identities.probeDocumentSha256) fail('binding_source_drift')
  const expected = {
    service_order: topologicalOrder(graph.services),
    public_readiness_ids: graph.services.filter((service) => service.public_readiness_id !== null).map((service) => service.public_readiness_id).sort(),
    required_service_ids: graph.services.filter((service) => service.requirement === 'required').map((service) => service.service_id).sort(),
    optional_service_ids: graph.services.filter((service) => service.requirement === 'optional').map((service) => service.service_id).sort(),
    external_service_ids: graph.services.filter((service) => service.requirement === 'external').map((service) => service.service_id).sort()
  }
  for (const [field, value] of Object.entries(expected)) {
    if (!Array.isArray(binding[field]) || JSON.stringify(binding[field]) !== JSON.stringify(value)) fail('binding_graph_drift')
  }
  if (canonicalJsonSha256(binding) !== document.binding_sha256) fail('binding_self_hash_invalid')
  return document
}

const validateReducedProfile = (profile, graph) => {
  exactKeys(profile, [
    'profile_id', 'layer', 'description', 'status', 'parent_profile', 'services',
    'service_order', 'execution_contract', 'watcher_contract', 'readiness_contract',
    'privacy_contract', 'proof_contract'
  ], 'profile_shape_invalid')
  if (profile.profile_id !== REDUCED_PROFILE_ID || graph.profile_id !== REDUCED_PROFILE_ID ||
      profile.layer !== 'ops' || profile.status !== 'candidate_not_selected' ||
      typeof profile.description !== 'string' || profile.description.length === 0 || profile.description.length > 512) {
    fail('profile_identity_invalid')
  }
  exactKeys(profile.parent_profile, Object.keys(REDUCED_PARENT_PROFILE), 'profile_parent_source_invalid')
  for (const [field, expected] of Object.entries(REDUCED_PARENT_PROFILE)) {
    if (profile.parent_profile[field] !== expected) fail('profile_parent_source_invalid')
  }
  const graphOrder = graph.services.map((service) => service.service_id)
  const dependencyOrder = topologicalOrder(graph.services)
  if (!Array.isArray(profile.services) || !Array.isArray(profile.service_order) ||
      new Set(profile.services).size !== profile.services.length ||
      new Set(profile.service_order).size !== profile.service_order.length ||
      JSON.stringify(profile.services) !== JSON.stringify(graphOrder) ||
      JSON.stringify(profile.service_order) !== JSON.stringify(dependencyOrder)) {
    fail('profile_services_invalid')
  }

  const execution = profile.execution_contract
  exactKeys(execution, [
    'mode', 'tools_adapter', 'capabilities', 'actions', 'action_submit_max',
    'home_calls_max', 'environment_calls_max', 'automatic_retries_max', 'mock_fallback'
  ], 'profile_execution_invalid')
  if (execution.mode !== 'conversation_only' || execution.tools_adapter !== 'disabled' ||
      execution.capabilities !== 'unavailable' || execution.actions !== 'disabled_action0' ||
      execution.mock_fallback !== 'forbidden') fail('profile_execution_invalid')
  for (const field of ['action_submit_max', 'home_calls_max', 'environment_calls_max', 'automatic_retries_max']) {
    if (execution[field] !== 0) fail('profile_execution_invalid')
  }

  const watcher = profile.watcher_contract
  exactKeys(watcher, [
    'turn_admission', 'skip_existing', 'thought_dispatch_max', 'result_write_max',
    'narration_max', 'presentation_dispatch_max', 'tts', 'direct_send', 'local_ack',
    'auto_review', 'closed_loop_output'
  ], 'profile_watcher_invalid')
  if (watcher.turn_admission !== 'held' || watcher.skip_existing !== true ||
      watcher.tts !== 'disabled' || watcher.direct_send !== 'disabled' ||
      watcher.local_ack !== 'disabled' || watcher.auto_review !== 'disabled' ||
      watcher.closed_loop_output !== 'disabled') fail('profile_watcher_invalid')
  for (const field of ['thought_dispatch_max', 'result_write_max', 'narration_max', 'presentation_dispatch_max']) {
    if (watcher[field] !== 0) fail('profile_watcher_invalid')
  }

  const readiness = profile.readiness_contract
  exactKeys(readiness, [
    'missing_proof', 'aituber_http', 'message_receiver', 'browser_store',
    'bubble_applied', 'visible_pixels', 'may_claim_standard_ready', 'may_claim_full_ready'
  ], 'profile_readiness_invalid')
  if (readiness.missing_proof !== 'unknown' || readiness.aituber_http !== 'reachability_only' ||
      readiness.message_receiver !== 'unproved' || readiness.browser_store !== 'unproved' ||
      readiness.bubble_applied !== 'unproved' || readiness.visible_pixels !== 'unproved' ||
      readiness.may_claim_standard_ready !== false || readiness.may_claim_full_ready !== false) {
    fail('profile_readiness_invalid')
  }

  const privacy = profile.privacy_contract
  exactKeys(privacy, [
    'public_profile_fields', 'raw_path_public', 'raw_command_public',
    'private_identifier_public', 'home_environment_private_inputs'
  ], 'profile_privacy_invalid')
  const publicFields = [
    'profile_id', 'profile_revision', 'config_identity', 'route_class',
    'readiness_class', 'reason_class'
  ]
  if (JSON.stringify(privacy.public_profile_fields) !== JSON.stringify(publicFields) ||
      privacy.raw_path_public !== false || privacy.raw_command_public !== false ||
      privacy.private_identifier_public !== false ||
      privacy.home_environment_private_inputs !== 'forbidden') fail('profile_privacy_invalid')

  const proof = profile.proof_contract
  exactKeys(proof, [
    'ceiling', 'parent_selected', 'provider_available', 'runtime_ready',
    'presentation_observed', 'cleanup_residue0', 'core_rehearsal_clear'
  ], 'profile_proof_invalid')
  if (proof.ceiling !== 'source_static_candidate_contract' ||
      ['parent_selected', 'provider_available', 'runtime_ready', 'presentation_observed', 'cleanup_residue0', 'core_rehearsal_clear']
        .some((field) => proof[field] !== false)) fail('profile_proof_invalid')
  return profile
}

const validateReducedProbeCrosswalk = (probeDocument, graph, graphSha256) => {
  exactKeys(probeDocument, ['schema_version', 'profile_id', 'graph_sha256', 'descriptors'], 'probe_document_shape_invalid')
  if (probeDocument.schema_version !== 'launcher_probe_descriptors.v1' ||
      probeDocument.profile_id !== graph.profile_id || probeDocument.graph_sha256 !== graphSha256 ||
      !Array.isArray(probeDocument.descriptors) || probeDocument.descriptors.length !== graph.services.length) {
    fail('probe_document_identity_invalid')
  }
  const serviceIds = probeDocument.descriptors.map((descriptor) => descriptor?.service_id)
  if (new Set(serviceIds).size !== serviceIds.length ||
      JSON.stringify(serviceIds) !== JSON.stringify(graph.services.map((service) => service.service_id))) {
    fail('probe_document_services_invalid')
  }
  for (const service of graph.services) {
    const descriptor = probeDocument.descriptors.find((candidate) => candidate.service_id === service.service_id)
    if (!isPlainObject(descriptor) || descriptor.probe_id !== service.readiness.probe_id) {
      fail('probe_document_services_invalid')
    }
  }
  const aituber = probeDocument.descriptors.find((descriptor) => descriptor.service_id === 'aituber_kit')
  if (!aituber || aituber.proof_ceiling !== 'aituber_http_reachability_only') fail('probe_document_proof_invalid')
  return probeDocument
}

const validateReducedInventory = (repositoryRoot, graph) => {
  for (const service of graph.services) {
    const manifest = readContract(
      path.join(repositoryRoot, 'ops', 'manifests', 'services', `${service.service_id}.json`),
      'profile_service_manifest_missing'
    ).value
    if (!isPlainObject(manifest) || manifest.service_id !== service.service_id) fail('profile_service_manifest_invalid')
  }
}

const validateWorkerMessage = (message, authority) => {
  assertAuthority(authority)
  const serviceIdPattern = authority.serviceIdPattern
  if (!isPlainObject(message)) fail('worker_message_invalid')
  if (message.message_type === 'request') {
    exactKeys(message, [
      'schema_version', 'message_type', 'operation_id', 'supervisor_generation', 'authority_lease_proof', 'dispatch_id', 'graph_sha256', 'binding_sha256',
      'service_id', 'action', 'adapter_class', 'expected_revision', 'deadline_ms', 'worker_nonce'
    ], 'worker_request_shape_invalid')
    if (message.schema_version !== 'launcher_worker.v2') fail('worker_schema_version_invalid')
    requireOperationId(message.operation_id, 'worker_operation_id_invalid')
    requireInteger(message.supervisor_generation, 1, Number.MAX_SAFE_INTEGER, 'worker_generation_invalid')
    if (typeof message.authority_lease_proof !== 'string' || !AUTHORITY_LEASE_PROOF.test(message.authority_lease_proof)) fail('worker_authority_lease_invalid')
    if (typeof message.dispatch_id !== 'string' || !DISPATCH_ID.test(message.dispatch_id)) fail('worker_dispatch_id_invalid')
    requireSha(message.graph_sha256, 'worker_graph_sha256_invalid')
    requireSha(message.binding_sha256, 'worker_binding_sha256_invalid')
    requireServiceId(message.service_id, serviceIdPattern, 'worker_service_id_invalid')
    requireEnum(message.action, ['start', 'probe', 'stop'], 'worker_action_invalid')
    requireEnum(message.adapter_class, ['job_worker_service', 'job_worker_job_close', 'external_probe_only', 'external_noop'], 'worker_adapter_invalid')
    requireInteger(message.expected_revision, 0, Number.MAX_SAFE_INTEGER, 'worker_revision_invalid')
    requireInteger(message.deadline_ms, 0, 300000, 'worker_deadline_invalid')
    if (typeof message.worker_nonce !== 'string' || !WORKER_NONCE.test(message.worker_nonce)) fail('worker_nonce_invalid')
    const allowedAdapters = message.action === 'start'
      ? ['job_worker_service']
      : message.action === 'stop'
        ? ['job_worker_job_close', 'external_noop']
        : ['job_worker_service', 'external_probe_only']
    if (!allowedAdapters.includes(message.adapter_class)) fail('worker_action_adapter_mismatch')
    return message
  }
  if (message.message_type === 'result') {
    exactKeys(message, [
      'schema_version', 'message_type', 'operation_id', 'supervisor_generation', 'authority_lease_proof', 'dispatch_id', 'service_id', 'action', 'expected_revision',
      'worker_nonce', 'result_class', 'ownership_class', 'listener_class', 'descendant_class',
      'termination_class', 'job_query_class', 'active_count_after', 'post_stop_listener_class'
    ], 'worker_result_shape_invalid')
    if (message.schema_version !== 'launcher_worker.v2') fail('worker_schema_version_invalid')
    requireOperationId(message.operation_id, 'worker_operation_id_invalid')
    requireInteger(message.supervisor_generation, 1, Number.MAX_SAFE_INTEGER, 'worker_generation_invalid')
    if (typeof message.authority_lease_proof !== 'string' || !AUTHORITY_LEASE_PROOF.test(message.authority_lease_proof)) fail('worker_authority_lease_invalid')
    if (typeof message.dispatch_id !== 'string' || !DISPATCH_ID.test(message.dispatch_id)) fail('worker_dispatch_id_invalid')
    requireServiceId(message.service_id, serviceIdPattern, 'worker_service_id_invalid')
    requireEnum(message.action, ['start', 'probe', 'stop'], 'worker_action_invalid')
    requireInteger(message.expected_revision, 0, Number.MAX_SAFE_INTEGER, 'worker_revision_invalid')
    if (typeof message.worker_nonce !== 'string' || !WORKER_NONCE.test(message.worker_nonce)) fail('worker_nonce_invalid')
    requireEnum(message.result_class, [
      'accepted', 'ready', 'optional_absent', 'external_ready', 'stopped', 'spawn_failed', 'early_exit',
      'listener_mismatch', 'readiness_timeout', 'stop_failed', 'deadline', 'cancelled', 'invalid_request', 'internal_failure'
    ], 'worker_result_class_invalid')
    requireEnum(message.ownership_class, ['matched', 'mismatch', 'unknown', 'not_applicable'], 'worker_ownership_class_invalid')
    requireEnum(message.listener_class, ['matched', 'mismatch', 'unknown', 'not_applicable'], 'worker_listener_class_invalid')
    requireEnum(message.descendant_class, ['owned_clear', 'owned_active', 'foreign', 'unknown', 'not_applicable'], 'worker_descendant_class_invalid')
    requireEnum(message.termination_class, TERMINATION_CLASSES, 'worker_termination_class_invalid')
    requireEnum(message.job_query_class, JOB_QUERY_CLASSES, 'worker_job_query_class_invalid')
    requireEnum(message.post_stop_listener_class, POST_STOP_LISTENER_CLASSES, 'worker_post_stop_listener_class_invalid')
    if (message.job_query_class === 'trusted') {
      requireInteger(message.active_count_after, 0, 4294967295, 'worker_active_count_after_invalid')
    } else if (message.active_count_after !== null) fail('worker_active_count_after_invalid')
    if (message.action !== 'stop' && (message.termination_class !== 'not_applicable' ||
        message.job_query_class !== 'not_applicable' || message.active_count_after !== null ||
        message.post_stop_listener_class !== 'not_applicable')) fail('worker_stop_proof_action_invalid')
    return message
  }
  fail('worker_message_type_invalid')
}

const validateWorkerRequestAgainstAuthority = (request, authority) => {
  assertAuthority(authority)
  validateWorkerMessage(request, authority)
  if (request.message_type !== 'request') fail('worker_request_expected')
  const spec = authority.graph.services.find((service) => service.service_id === request.service_id)
  if (!spec) fail('worker_request_service_unknown')
  const expectedAdapter = request.action === 'start'
    ? spec.start.adapter_id
    : request.action === 'stop'
      ? spec.stop.adapter_id
      : spec.ownership === 'external' ? 'external_probe_only' : 'job_worker_service'
  if (request.adapter_class !== expectedAdapter) fail('worker_request_adapter_mismatch')
  const expectedDeadline = request.action === 'stop' ? spec.stop.graceful_timeout_ms : spec.ready_deadline_ms
  if (request.deadline_ms !== expectedDeadline) fail('worker_request_deadline_mismatch')
  return request
}

const validateReducerVectors = (document, serviceIdPattern) => {
  if (serviceIdPattern !== SERVICE_ID_PATTERN) fail('operation_service_id_pattern_invalid')
  exactKeys(document, ['schema_version', 'vectors'], 'reducer_vectors_shape_invalid')
  if (document.schema_version !== 'launcher_reducer_vectors.v2' || !Array.isArray(document.vectors) ||
      document.vectors.length === 0 || document.vectors.length > MAX_REDUCER_VECTORS) fail('reducer_vectors_invalid')
  const ids = new Set()
  const phases = ['planned', 'preflight', 'prepared', 'starting', 'waiting_ready', 'ready', 'rolling_back', 'failed', 'stopping', 'stopped', 'recovering', 'residue']
  const reasons = ['none', 'preflight_failed', 'spawn_failed', 'early_exit', 'listener_mismatch', 'readiness_timeout', 'start_dispatch_unknown', 'semantic_probe_failed', 'rollback_failed', 'stop_failed', 'supervisor_crash', 'residue_present', 'invalid_event']
  const cleanups = ['not_started', 'in_progress', 'clear', 'residue', 'unknown']
  const events = new Set(['preflight_started', 'preflight_passed', 'preflight_failed', 'start_requested', 'spawn_requested', 'probe_requested', 'stop_dispatch_requested', 'spawn_succeeded', 'spawn_failed', 'start_dispatch_unknown', 'early_exit', 'listener_mismatch', 'readiness_timeout', 'probe_failed', 'probe_transport_ready', 'semantic_probe_completed', 'optional_absent', 'rollback_started', 'rollback_completed', 'rollback_failed', 'stop_requested', 'service_stopped', 'stop_failed', 'cleanup_unattempted', 'private_plan_cleanup_completed', 'private_plan_cleanup_failed', 'private_plan_cleanup_unattempted', 'supervisor_crashed', 'recovery_started', 'recovery_completed', 'residue_observed', 'residue_cleared'])
  for (const vector of document.vectors) {
    const hasEventOperation = isPlainObject(vector) && Object.hasOwn(vector, 'event_operation_id')
    const keys = ['vector_id', 'events', 'expected', 'coverage', ...(hasEventOperation ? ['event_operation_id'] : [])]
    exactKeys(vector, keys, 'reducer_vector_shape_invalid')
    requireId(vector.vector_id, 'reducer_vector_id_invalid')
    if (ids.has(vector.vector_id)) fail('reducer_vector_duplicate')
    ids.add(vector.vector_id)
    if (hasEventOperation) requireOperationId(vector.event_operation_id, 'reducer_vector_operation_id_invalid')
    if (!Array.isArray(vector.events) || vector.events.length === 0 || vector.events.length > MAX_VECTOR_EVENTS) fail('reducer_vector_events_invalid')
    for (const event of vector.events) {
      const hasService = isPlainObject(event) && Object.hasOwn(event, 'service_id')
      const hasDispatch = Object.hasOwn(event, 'dispatch_id')
      const hasAction = Object.hasOwn(event, 'action')
      const hasCleanupAttempt = Object.hasOwn(event, 'cleanup_attempt')
      exactKeys(event, ['event_type', ...(hasService ? ['service_id'] : []), ...(hasDispatch ? ['dispatch_id'] : []), ...(hasAction ? ['action'] : []), ...(hasCleanupAttempt ? ['cleanup_attempt'] : [])], 'reducer_vector_event_shape_invalid')
      if (!events.has(event.event_type)) fail('reducer_vector_event_type_invalid')
      if (hasService) requireServiceId(event.service_id, serviceIdPattern, 'reducer_vector_service_id_invalid')
      if (hasDispatch && (typeof event.dispatch_id !== 'string' || !DISPATCH_ID.test(event.dispatch_id))) fail('reducer_vector_dispatch_id_invalid')
      if (hasAction) requireEnum(event.action, ['start', 'probe', 'stop'], 'reducer_vector_action_invalid')
      if (hasCleanupAttempt) validateCleanupAttempt(event.cleanup_attempt, serviceIdPattern)
    }
    exactKeys(vector.expected, ['phase', 'reason', 'cleanup', 'residue_service_ids', 'cleanup_attempts'], 'reducer_vector_expected_shape_invalid')
    requireEnum(vector.expected.phase, phases, 'reducer_vector_phase_invalid')
    requireEnum(vector.expected.reason, reasons, 'reducer_vector_reason_invalid')
    requireEnum(vector.expected.cleanup, cleanups, 'reducer_vector_cleanup_invalid')
    requireServiceIdArray(vector.expected.residue_service_ids, serviceIdPattern, 'reducer_vector_residue_invalid')
    if (!Array.isArray(vector.expected.cleanup_attempts) || vector.expected.cleanup_attempts.length > 65) fail('reducer_vector_cleanup_attempts_invalid')
    vector.expected.cleanup_attempts.forEach((attempt, index) => {
      validateCleanupAttempt(attempt, serviceIdPattern)
      if (attempt.sequence !== index + 1) fail('reducer_vector_cleanup_attempts_invalid')
    })
    if (!Array.isArray(vector.coverage) || vector.coverage.length > MAX_VECTOR_COVERAGE) fail('reducer_vector_coverage_invalid')
    requireIdArray(vector.coverage, 'reducer_vector_coverage_invalid')
  }
  return document
}

const renderBindingDocument = ({ graph, identities }) => {
  const binding = {
    binding_version: 'launcher_service_graph.binding.v2',
    text_hash_mode: 'utf8_lf_v1',
    profile_id: graph.profile_id,
    graph_sha256: identities.graphSha256,
    graph_schema_sha256: identities.graphSchemaSha256,
    operation_schema_sha256: identities.operationSchemaSha256,
    worker_schema_sha256: identities.workerSchemaSha256,
    reducer_vectors_sha256: identities.reducerVectorsSha256,
    probe_schema_sha256: identities.probeSchemaSha256,
    probe_document_sha256: identities.probeDocumentSha256,
    service_order: topologicalOrder(graph.services),
    public_readiness_ids: graph.services.filter((service) => service.public_readiness_id !== null).map((service) => service.public_readiness_id).sort(),
    required_service_ids: graph.services.filter((service) => service.requirement === 'required').map((service) => service.service_id).sort(),
    optional_service_ids: graph.services.filter((service) => service.requirement === 'optional').map((service) => service.service_id).sort(),
    external_service_ids: graph.services.filter((service) => service.requirement === 'external').map((service) => service.service_id).sort()
  }
  return `${JSON.stringify({ binding_sha256: canonicalJsonSha256(binding), binding }, null, 2)}\n`
}

const validateLegacyDrift = (repositoryRoot, graph) => {
  const owned = new Map(graph.services.filter((service) => service.ownership === 'owned').map((service) => [service.service_id, service]))
  for (const service of owned.values()) {
    const manifest = readContract(path.join(repositoryRoot, 'ops', 'manifests', 'services', `${service.service_id}.json`), 'drift_service_manifest_missing').value
    if (manifest.service_id !== service.service_id) fail('drift_service_id')
    if (JSON.stringify([...manifest.depends_on].sort()) !== JSON.stringify([...service.dependencies].sort())) fail('drift_service_dependencies')
    const noPort = service.port.ownership === 'none'
    if (noPort) {
      if (manifest.health?.type !== 'module_status') fail('drift_service_port')
    } else {
      let health
      try { health = new URL(manifest.health.url) } catch { fail('drift_service_port') }
      if (!['127.0.0.1', 'localhost', '::1', '[::1]'].includes(health.hostname)) fail('drift_service_port')
      const transport = ['http:', 'https:'].includes(health.protocol) ? 'http' : ['ws:', 'wss:'].includes(health.protocol) ? 'websocket' : 'unknown'
      const port = Number(health.port || (health.protocol === 'https:' ? 443 : 80))
      if (service.port.port_mode !== 'manifest_default' || service.port.loopback_port !== port || service.port.transport !== transport) fail('drift_service_port')
    }
  }

  const profile = readContract(path.join(repositoryRoot, 'ops', 'manifests', 'profiles', `${graph.profile_id}.json`), 'drift_profile_missing').value
  const actualProfile = [...profile.services].sort()
  const expectedProfile = graph.services.filter((service) => service.legacy_profile_member).map((service) => service.service_id).sort()
  if (JSON.stringify(actualProfile) !== JSON.stringify(expectedProfile)) fail('drift_legacy_profile_membership')

  const route = readContract(path.join(repositoryRoot, 'contracts', 'turn', 'ordinary-standard-route.v1.json'), 'drift_route_missing').value
  const actualReady = [...route.readiness.expected_service_ids].sort()
  const expectedReady = graph.services.filter((service) => service.public_readiness_id !== null).map((service) => service.public_readiness_id).sort()
  if (JSON.stringify(actualReady) !== JSON.stringify(expectedReady)) fail('drift_ordinary_route_readiness')

  let server
  let stack
  try {
    server = readBoundedUtf8Text(path.join(repositoryRoot, 'tools', 'home-control-launcher', 'server.js'), MAX_LEGACY_SOURCE_BYTES, 'drift_legacy_source_missing', 'drift_legacy_source_oversized')
    stack = readBoundedUtf8Text(path.join(repositoryRoot, 'ops', 'scripts', 'home-control-stack', 'start-home-control-stack.ps1'), MAX_LEGACY_SOURCE_BYTES, 'drift_legacy_source_missing', 'drift_legacy_source_oversized')
  } catch (error) {
    if (error instanceof LauncherContractError) throw error
    fail('drift_legacy_source_missing')
  }
  const voicevoxPort = /let\s+voicevoxPort\s*=\s*(\d+)/u.exec(server)
  const voicevox = graph.services.find((service) => service.service_id === 'voicevox')
  if (!voicevoxPort || Number(voicevoxPort[1]) !== voicevox.port.loopback_port) fail('drift_voicevox_port')
  const functionStart = server.indexOf('const expectedServicesForOptions')
  const functionEnd = server.indexOf('const startupReadyTimeoutMsForService', functionStart)
  if (functionStart < 0 || functionEnd <= functionStart) fail('drift_launcher_function_missing')
  const launcherIds = [...server.slice(functionStart, functionEnd).matchAll(/services\.push\('([^']+)'\)/gu)].map((match) => match[1]).sort()
  if (JSON.stringify(launcherIds) !== JSON.stringify(expectedReady)) fail('drift_launcher_readiness_list')
  const stackIds = [...stack.matchAll(/\$specs\s*\+=\s*New-ServiceSpec[\s\S]{0,400}?-Name\s+"([^"]+)"/gu)].map((match) => match[1])
  const expectedStack = [...new Set(graph.services.flatMap((service) => service.start.legacy_spec_ids))].sort()
  if (JSON.stringify([...new Set(stackIds)].sort()) !== JSON.stringify(expectedStack)) fail('drift_stack_service_list')
}

const loadAuthority = (repositoryRoot, { profileId = STANDARD_PROFILE_ID } = {}) => {
  if (typeof repositoryRoot !== 'string' || !path.isAbsolute(repositoryRoot)) fail('authority_root_invalid')
  if (![STANDARD_PROFILE_ID, REDUCED_PROFILE_ID].includes(profileId)) fail('authority_profile_invalid')
  const reduced = profileId === REDUCED_PROFILE_ID
  const suffix = reduced ? 'core-rehearsal-text-bubble' : 'standard'
  const graphSchemaSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-service-graph.v1.schema.json'), 'graph_schema_read_failed')
  const operationSchemaSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-operation.v2.schema.json'), 'operation_schema_read_failed')
  const workerSchemaSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-worker.v2.schema.json'), 'worker_schema_read_failed')
  const vectorsSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-reducer-vectors.v2.json'), 'reducer_vectors_read_failed')
  const probeSchemaSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'launcher-probe-descriptor.v1.schema.json'), 'probe_schema_read_failed')
  const probeDocumentSource = readContract(path.join(repositoryRoot, 'ops', 'manifests', `launcher-probe-descriptors.${suffix}.v1.json`), 'probe_document_read_failed')
  const graphSource = readContract(path.join(repositoryRoot, 'ops', 'manifests', `launcher-service-graph.${suffix}.v1.json`), 'graph_read_failed')
  const bindingSource = readContract(path.join(repositoryRoot, 'contracts', 'launcher', 'generated', `launcher-service-graph.${suffix}.v2.binding.json`), 'binding_read_failed')
  const profileSource = reduced
    ? readContract(path.join(repositoryRoot, 'ops', 'manifests', 'profiles', `${REDUCED_PROFILE_ID}.json`), 'profile_document_read_failed')
    : null

  validateStrictSchemaEnvelope(graphSchemaSource.value, 'launcher-service-graph.v1.schema.json', 'graph_schema_invalid')
  validateStrictSchemaEnvelope(operationSchemaSource.value, 'launcher-operation.v2.schema.json', 'operation_schema_invalid')
  validateStrictSchemaEnvelope(workerSchemaSource.value, 'launcher-worker.v2.schema.json', 'worker_schema_invalid')
  const serviceIdPattern = validateSchemaAuthority(graphSchemaSource.value, operationSchemaSource.value, workerSchemaSource.value)
  validateReducerVectors(vectorsSource.value, serviceIdPattern)
  const graph = validateGraph(graphSource.value, serviceIdPattern)
  const identities = {
    graphSha256: graphSource.sha256,
    graphSchemaSha256: graphSchemaSource.sha256,
    operationSchemaSha256: operationSchemaSource.sha256,
    workerSchemaSha256: workerSchemaSource.sha256,
    reducerVectorsSha256: vectorsSource.sha256,
    probeSchemaSha256: probeSchemaSource.sha256,
    probeDocumentSha256: probeDocumentSource.sha256
  }
  const bindingDocument = validateBinding(bindingSource.value, identities, graph)
  let profileDocument = null
  if (reduced) {
    profileDocument = validateReducedProfile(profileSource.value, graph)
    validateReducedProbeCrosswalk(probeDocumentSource.value, graph, graphSource.sha256)
    validateReducedInventory(repositoryRoot, graph)
  } else {
    validateLegacyDrift(repositoryRoot, graph)
  }
  const authority = {
    [AUTHORITY_TOKEN]: true,
    serviceIdPattern,
    graph: copyJson(graph),
    graphSchema: copyJson(graphSchemaSource.value),
    operationSchema: copyJson(operationSchemaSource.value),
    workerSchema: copyJson(workerSchemaSource.value),
    reducerVectors: copyJson(vectorsSource.value),
    probeSchema: copyJson(probeSchemaSource.value),
    probeDocument: copyJson(probeDocumentSource.value),
    bindingDocument: copyJson(bindingDocument),
    ...(profileDocument === null ? {} : { profileDocument: copyJson(profileDocument) }),
    identities: { ...identities, bindingSha256: bindingDocument.binding_sha256 }
  }
  return deepFreeze(authority)
}

const assertAuthority = (authority) => {
  if (!authority || authority[AUTHORITY_TOKEN] !== true) fail('authority_not_validated')
  return authority
}

module.exports = {
  LauncherContractError,
  assertAuthority,
  canonicalJsonSha256,
  canonicalLfSha256,
  canonicalLfText,
  deepFreeze,
  loadAuthority,
  hasClearServiceCleanupProof,
  readBoundedUtf8Text,
  renderBindingDocument,
  serviceIdPatternFromOperationSchema,
  topologicalOrder,
  validateGraph,
  validateCleanupAttempt,
  validateReducerVectors,
  validateWorkerMessage,
  validateWorkerRequestAgainstAuthority
}
