'use strict'

const {
  LauncherContractError, assertAuthority, validateWorkerMessage, validateWorkerRequestAgainstAuthority
} = require('./launcher-supervisor-contract')

const PHASE = Object.freeze({
  PLANNED: 'planned', PREFLIGHT: 'preflight', PREPARED: 'prepared', STARTING: 'starting',
  WAITING_READY: 'waiting_ready', READY: 'ready', ROLLING_BACK: 'rolling_back', FAILED: 'failed',
  STOPPING: 'stopping', STOPPED: 'stopped', RECOVERING: 'recovering', RESIDUE: 'residue'
})
const REASON = Object.freeze({
  NONE: 'none', PREFLIGHT_FAILED: 'preflight_failed', SPAWN_FAILED: 'spawn_failed', EARLY_EXIT: 'early_exit',
  LISTENER_MISMATCH: 'listener_mismatch', READINESS_TIMEOUT: 'readiness_timeout', ROLLBACK_FAILED: 'rollback_failed',
  STOP_FAILED: 'stop_failed', SUPERVISOR_CRASH: 'supervisor_crash', RESIDUE_PRESENT: 'residue_present', INVALID_EVENT: 'invalid_event'
})
const CLEANUP = Object.freeze({ NOT_STARTED: 'not_started', IN_PROGRESS: 'in_progress', CLEAR: 'clear', RESIDUE: 'residue', UNKNOWN: 'unknown' })
const SERVICE = Object.freeze({
  PENDING: 'pending', STARTING: 'starting', READY: 'ready', OPTIONAL_ABSENT: 'optional_absent', EXTERNAL_READY: 'external_ready',
  STOP_REQUESTED: 'stop_requested', STOPPED: 'stopped', FAILED: 'failed', RESIDUE: 'residue', UNKNOWN: 'unknown'
})

const OPERATION_ID = /^lop_[a-z0-9]{8,64}$/u
const DISPATCH_ID = /^ld_[a-z0-9]{16,64}$/u
const SHA256 = /^[a-f0-9]{64}$/u
const fail = (code) => { throw new LauncherContractError(code) }
const copyServices = (services) => services.map((service) => ({ ...service }))
const cloneOperation = (operation, changes = {}) => ({
  ...operation,
  primary_result: { ...operation.primary_result, ...(changes.primary_result || {}) },
  cleanup_result: { ...operation.cleanup_result, ...(changes.cleanup_result || {}) },
  services: changes.services ? copyServices(changes.services) : copyServices(operation.services),
  residue_service_ids: changes.residue_service_ids ? [...changes.residue_service_ids] : [...operation.residue_service_ids],
  ...changes
})

const validateIdentityInputs = (operationId, graphSha256, bindingSha256) => {
  if (typeof operationId !== 'string' || !OPERATION_ID.test(operationId)) fail('operation_id_invalid')
  if (typeof graphSha256 !== 'string' || !SHA256.test(graphSha256)) fail('operation_graph_sha256_invalid')
  if (typeof bindingSha256 !== 'string' || !SHA256.test(bindingSha256)) fail('operation_binding_sha256_invalid')
}

const createOperation = (operationId, authority, supervisorGeneration = 1) => {
  assertAuthority(authority)
  validateIdentityInputs(operationId, authority.identities.graphSha256, authority.identities.bindingSha256)
  if (!Number.isSafeInteger(supervisorGeneration) || supervisorGeneration < 1) fail('supervisor_generation_invalid')
  return {
    schema_version: 'launcher_operation.v2',
    graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    operation_id: operationId,
    supervisor_generation: supervisorGeneration,
    intent: 'start',
    phase: PHASE.PLANNED,
    reason: REASON.NONE,
    cleanup: CLEANUP.NOT_STARTED,
    primary_result: { class: REASON.NONE, responsible_id: null, action_certainty: 'not_attempted' },
    cleanup_result: { class: CLEANUP.NOT_STARTED, responsible_id: null },
    revision: 0,
    joined_existing: false,
    rollback_required: false,
    recovery_required: false,
    services: authority.graph.services.map((service) => ({
      service_id: service.service_id, state: SERVICE.PENDING, attempt_sequence: 0,
      pending_dispatch_id: null, pending_action: null
    })),
    residue_service_ids: []
  }
}

const startOperation = (active, operationId, authority, supervisorGeneration = 1) => {
  assertAuthority(authority)
  validateIdentityInputs(operationId, authority.identities.graphSha256, authority.identities.bindingSha256)
  if (active !== null && active !== undefined) {
    validateSnapshot(active, authority)
    if (active.graph_sha256 !== authority.identities.graphSha256 || active.binding_sha256 !== authority.identities.bindingSha256) fail('operation_active_identity_mismatch')
    if (![PHASE.STOPPED, PHASE.FAILED].includes(active.phase)) {
      return { operation: next(active, { joined_existing: true }), joined_existing: true }
    }
  }
  return { operation: createOperation(operationId, authority, supervisorGeneration), joined_existing: false }
}

const next = (operation, changes = {}) => {
  if (!Number.isSafeInteger(operation.revision) || operation.revision >= Number.MAX_SAFE_INTEGER) fail('operation_revision_exhausted')
  return cloneOperation(operation, { ...changes, revision: operation.revision + 1 })
}
const firstFailure = (operation, proposed) => operation.reason === REASON.NONE ? proposed : operation.reason
const primaryResult = (operation, proposed, responsibleId, actionCertainty = 'may_have_occurred') =>
  operation.primary_result.class === REASON.NONE
    ? { class: proposed, responsible_id: responsibleId, action_certainty: actionCertainty }
    : operation.primary_result
const cleanupResult = (klass, responsibleId = null) => ({ class: klass, responsible_id: responsibleId })
const invalid = (operation) => next(operation, { reason: firstFailure(operation, REASON.INVALID_EVENT) })
const stateOf = (operation, serviceId) => operation.services.find((service) => service.service_id === serviceId)?.state
const specOf = (authority, serviceId) => authority.graph.services.find((service) => service.service_id === serviceId)
const inPhase = (operation, phases) => phases.includes(operation.phase)

const setService = (operation, serviceId, state, phase) => {
  if (typeof serviceId !== 'string' || !operation.services.some((service) => service.service_id === serviceId)) return invalid(operation)
  const services = operation.services.map((service) => service.service_id === serviceId ? { ...service, state } : { ...service })
  return next(operation, { services, phase })
}

const requestServiceDispatch = (operation, event, state, phase, expectedAction) => {
  if (!event || typeof event.dispatch_id !== 'string' || !DISPATCH_ID.test(event.dispatch_id) || event.action !== expectedAction) return invalid(operation)
  const current = operation.services.find((service) => service.service_id === event.service_id)
  if (!current || current.pending_dispatch_id !== null || current.pending_action !== null || current.attempt_sequence >= Number.MAX_SAFE_INTEGER) return invalid(operation)
  const services = operation.services.map((service) => service.service_id === event.service_id
    ? { ...service, state, attempt_sequence: service.attempt_sequence + 1, pending_dispatch_id: event.dispatch_id, pending_action: expectedAction }
    : { ...service })
  return next(operation, { services, phase })
}

const pendingMatches = (operation, event, actions) => {
  const service = operation.services.find((candidate) => candidate.service_id === event.service_id)
  return Boolean(service && typeof event.dispatch_id === 'string' && DISPATCH_ID.test(event.dispatch_id) &&
    service.pending_dispatch_id === event.dispatch_id && actions.includes(service.pending_action))
}

const clearPending = (operation, serviceId) => cloneOperation(operation, {
  services: operation.services.map((service) => service.service_id === serviceId
    ? { ...service, pending_dispatch_id: null, pending_action: null }
    : { ...service })
})

const dependenciesReady = (operation, spec, authority) => spec.dependencies.every((dependencyId) => {
  const dependency = specOf(authority, dependencyId)
  const state = stateOf(operation, dependencyId)
  if (dependency.requirement === 'required') return state === SERVICE.READY
  if (dependency.requirement === 'optional') return [SERVICE.READY, SERVICE.OPTIONAL_ABSENT].includes(state)
  return state === SERVICE.EXTERNAL_READY
})

const canRequestSpawn = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.ownership === 'owned' && stateOf(operation, serviceId) === SERVICE.PENDING && dependenciesReady(operation, spec, authority))
}
const canCompleteSpawn = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.ownership === 'owned' && stateOf(operation, serviceId) === SERVICE.STARTING)
}
const canFailOwned = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.ownership === 'owned' && [SERVICE.STARTING, SERVICE.READY].includes(stateOf(operation, serviceId)))
}
const canFailExternalReadiness = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.requirement === 'external' && stateOf(operation, serviceId) === SERVICE.PENDING && dependenciesReady(operation, spec, authority))
}
const canOptionalAbsent = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.requirement === 'optional' && stateOf(operation, serviceId) === SERVICE.PENDING && dependenciesReady(operation, spec, authority))
}
const canExternalReady = (operation, serviceId, authority) => {
  const spec = specOf(authority, serviceId)
  return Boolean(spec && spec.requirement === 'external' && stateOf(operation, serviceId) === SERVICE.PENDING && dependenciesReady(operation, spec, authority))
}

const allReady = (operation, authority) => authority.graph.services.every((spec) => {
  const state = stateOf(operation, spec.service_id)
  if (spec.requirement === 'required') return state === SERVICE.READY
  if (spec.requirement === 'optional') return [SERVICE.READY, SERVICE.OPTIONAL_ABSENT].includes(state)
  return state === SERVICE.EXTERNAL_READY
})

const ready = (operation, serviceId, state, authority) => {
  const changed = setService(operation, serviceId, state, PHASE.WAITING_READY)
  return allReady(changed, authority) ? next(changed, { phase: PHASE.READY }) : changed
}

const failAndRollback = (operation, serviceId, reason) => {
  const changed = setService(operation, serviceId, SERVICE.FAILED, PHASE.ROLLING_BACK)
  return next(changed, {
    phase: PHASE.ROLLING_BACK, reason: firstFailure(operation, reason), cleanup: CLEANUP.IN_PROGRESS,
    primary_result: primaryResult(operation, reason, serviceId),
    cleanup_result: cleanupResult(CLEANUP.IN_PROGRESS, serviceId), rollback_required: true
  })
}

const outstandingOwned = (operation, authority) => {
  const owned = new Set(authority.graph.services.filter((service) => service.ownership === 'owned').map((service) => service.service_id))
  return operation.services.filter((service) => owned.has(service.service_id) && ![SERVICE.PENDING, SERVICE.STOPPED, SERVICE.OPTIONAL_ABSENT].includes(service.state)).map((service) => service.service_id).sort()
}

const normalizePendingOwned = (operation, authority) => {
  const owned = new Set(authority.graph.services.filter((service) => service.ownership === 'owned').map((service) => service.service_id))
  return cloneOperation(operation, { services: operation.services.map((service) => owned.has(service.service_id) && service.state === SERVICE.PENDING ? { ...service, state: SERVICE.STOPPED } : { ...service }) })
}

const retainUnknownResidue = (operation, outstanding) => {
  const unresolved = [...new Set([...operation.residue_service_ids, ...outstanding])].sort()
  const unresolvedSet = new Set(unresolved)
  const services = operation.services.map((service) => unresolvedSet.has(service.service_id) && service.state !== SERVICE.RESIDUE ? { ...service, state: SERVICE.UNKNOWN } : { ...service })
  return next(operation, {
    services, residue_service_ids: unresolved, phase: PHASE.RESIDUE,
    reason: firstFailure(operation, REASON.RESIDUE_PRESENT), cleanup: CLEANUP.UNKNOWN,
    primary_result: primaryResult(operation, REASON.RESIDUE_PRESENT, 'launcher_supervisor'),
    cleanup_result: cleanupResult(CLEANUP.UNKNOWN, 'launcher_supervisor'),
    rollback_required: false, recovery_required: true
  })
}

const residue = (operation, serviceId, reason, authority) => {
  const spec = specOf(authority, serviceId)
  if (!spec || spec.ownership !== 'owned') return invalid(operation)
  const changed = setService(operation, serviceId, SERVICE.RESIDUE, PHASE.RESIDUE)
  const unresolved = [...new Set([...operation.residue_service_ids, ...outstandingOwned(changed, authority), serviceId])].sort()
  const set = new Set(unresolved)
  const services = changed.services.map((service) => service.service_id === serviceId ? service : set.has(service.service_id) && service.state !== SERVICE.RESIDUE ? { ...service, state: SERVICE.UNKNOWN } : service)
  return next(changed, {
    services, residue_service_ids: unresolved, phase: PHASE.RESIDUE,
    reason: firstFailure(operation, reason), cleanup: CLEANUP.RESIDUE,
    primary_result: primaryResult(operation, reason, serviceId),
    cleanup_result: cleanupResult(reason === REASON.ROLLBACK_FAILED ? REASON.ROLLBACK_FAILED : reason === REASON.STOP_FAILED ? REASON.STOP_FAILED : CLEANUP.RESIDUE, serviceId),
    rollback_required: false, recovery_required: true
  })
}

const stop = (operation, authority) => {
  if (operation.phase === PHASE.STOPPED) return operation
  const external = new Set(authority.graph.services.filter((service) => service.ownership === 'external').map((service) => service.service_id))
  const services = operation.services.map((service) => external.has(service.service_id) || service.state === SERVICE.OPTIONAL_ABSENT ? { ...service } : { ...service, state: SERVICE.STOP_REQUESTED })
  return next(operation, {
    services, residue_service_ids: [], intent: 'stop', phase: PHASE.STOPPING,
    cleanup: CLEANUP.IN_PROGRESS, cleanup_result: cleanupResult(CLEANUP.IN_PROGRESS, 'launcher_supervisor'),
    rollback_required: false, recovery_required: false
  })
}

const serviceStopped = (operation, serviceId, authority) => {
  const changed = setService(operation, serviceId, SERVICE.STOPPED, PHASE.STOPPING)
  const external = new Set(authority.graph.services.filter((service) => service.ownership === 'external').map((service) => service.service_id))
  const complete = changed.services.filter((service) => !external.has(service.service_id)).every((service) => [SERVICE.STOPPED, SERVICE.OPTIONAL_ABSENT].includes(service.state))
  return complete ? next(changed, {
    phase: PHASE.STOPPED, cleanup: CLEANUP.CLEAR, cleanup_result: cleanupResult(CLEANUP.CLEAR), recovery_required: false
  }) : changed
}

const reduce = (operation, event, authority) => {
  assertAuthority(authority)
  if (!event || event.operation_id !== operation.operation_id) return operation
  if (event.event_type === 'stop_requested' && [PHASE.STOPPING, PHASE.STOPPED].includes(operation.phase)) return operation
  if (event.service_id !== undefined && event.service_id !== null && !specOf(authority, event.service_id)) return invalid(operation)
  const serviceId = event.service_id ?? null

  switch (event.event_type) {
    case 'preflight_started': return operation.phase === PHASE.PLANNED ? next(operation, { phase: PHASE.PREFLIGHT }) : invalid(operation)
    case 'preflight_passed': return operation.phase === PHASE.PREFLIGHT ? next(operation, { phase: PHASE.PREPARED }) : invalid(operation)
    case 'preflight_failed': return operation.phase === PHASE.PREFLIGHT
      ? next(normalizePendingOwned(operation, authority), {
          phase: PHASE.FAILED, reason: REASON.PREFLIGHT_FAILED, cleanup: CLEANUP.CLEAR,
          primary_result: primaryResult(operation, REASON.PREFLIGHT_FAILED, 'launcher_supervisor', 'not_attempted'),
          cleanup_result: cleanupResult(CLEANUP.CLEAR)
        })
      : invalid(operation)
    case 'start_requested': return operation.phase === PHASE.PREPARED ? next(operation, { phase: PHASE.STARTING }) : invalid(operation)
    case 'spawn_requested': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canRequestSpawn(operation, serviceId, authority)
      ? requestServiceDispatch(operation, event, SERVICE.STARTING, PHASE.STARTING, 'start') : invalid(operation)
    case 'probe_requested': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) &&
      ((canCompleteSpawn(operation, serviceId, authority)) || canOptionalAbsent(operation, serviceId, authority) || canExternalReady(operation, serviceId, authority))
      ? requestServiceDispatch(operation, event, stateOf(operation, serviceId), PHASE.WAITING_READY, 'probe') : invalid(operation)
    case 'stop_dispatch_requested': return operation.phase === PHASE.STOPPING && stateOf(operation, serviceId) === SERVICE.STOP_REQUESTED
      ? requestServiceDispatch(operation, event, SERVICE.STOP_REQUESTED, PHASE.STOPPING, 'stop') : invalid(operation)
    case 'spawn_succeeded': return operation.phase === PHASE.STARTING && canCompleteSpawn(operation, serviceId, authority) && pendingMatches(operation, event, ['start'])
      ? setService(clearPending(operation, serviceId), serviceId, SERVICE.STARTING, PHASE.WAITING_READY) : invalid(operation)
    case 'spawn_failed': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canCompleteSpawn(operation, serviceId, authority) && pendingMatches(operation, event, ['start'])
      ? failAndRollback(clearPending(operation, serviceId), serviceId, REASON.SPAWN_FAILED) : invalid(operation)
    case 'early_exit': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canFailOwned(operation, serviceId, authority) && pendingMatches(operation, event, ['start', 'probe'])
      ? failAndRollback(clearPending(operation, serviceId), serviceId, REASON.EARLY_EXIT) : invalid(operation)
    case 'listener_mismatch': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canFailOwned(operation, serviceId, authority) && pendingMatches(operation, event, ['start', 'probe'])
      ? failAndRollback(clearPending(operation, serviceId), serviceId, REASON.LISTENER_MISMATCH) : invalid(operation)
    case 'readiness_timeout': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) &&
      (canFailOwned(operation, serviceId, authority) || canFailExternalReadiness(operation, serviceId, authority)) && pendingMatches(operation, event, ['probe'])
      ? failAndRollback(clearPending(operation, serviceId), serviceId, REASON.READINESS_TIMEOUT) : invalid(operation)
    case 'service_ready': return operation.phase === PHASE.WAITING_READY && canCompleteSpawn(operation, serviceId, authority) && pendingMatches(operation, event, ['probe'])
      ? ready(clearPending(operation, serviceId), serviceId, SERVICE.READY, authority) : invalid(operation)
    case 'optional_absent': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canOptionalAbsent(operation, serviceId, authority)
      && pendingMatches(operation, event, ['probe']) ? ready(clearPending(operation, serviceId), serviceId, SERVICE.OPTIONAL_ABSENT, authority) : invalid(operation)
    case 'external_ready': return inPhase(operation, [PHASE.STARTING, PHASE.WAITING_READY]) && canExternalReady(operation, serviceId, authority)
      && pendingMatches(operation, event, ['probe']) ? ready(clearPending(operation, serviceId), serviceId, SERVICE.EXTERNAL_READY, authority) : invalid(operation)
    case 'rollback_started': return operation.rollback_required && operation.phase === PHASE.ROLLING_BACK
      ? next(operation, { phase: PHASE.ROLLING_BACK, cleanup: CLEANUP.IN_PROGRESS }) : invalid(operation)
    case 'rollback_completed': {
      if (!operation.rollback_required || operation.phase !== PHASE.ROLLING_BACK) return invalid(operation)
      const outstanding = outstandingOwned(operation, authority)
      return outstanding.length === 0 && operation.residue_service_ids.length === 0
        ? next(normalizePendingOwned(operation, authority), {
            phase: PHASE.FAILED, cleanup: CLEANUP.CLEAR, cleanup_result: cleanupResult(CLEANUP.CLEAR),
            rollback_required: false, recovery_required: false
          })
        : retainUnknownResidue(operation, outstanding)
    }
    case 'rollback_failed': return operation.rollback_required && operation.phase === PHASE.ROLLING_BACK
      ? residue(operation, serviceId, REASON.ROLLBACK_FAILED, authority) : invalid(operation)
    case 'stop_requested': return inPhase(operation, [PHASE.PLANNED, PHASE.PREFLIGHT, PHASE.PREPARED, PHASE.STARTING, PHASE.WAITING_READY, PHASE.READY, PHASE.ROLLING_BACK, PHASE.FAILED, PHASE.RECOVERING, PHASE.RESIDUE])
      ? stop(operation, authority) : invalid(operation)
    case 'service_stopped': {
      const spec = specOf(authority, serviceId)
      if (!spec || spec.ownership !== 'owned') return invalid(operation)
      if (operation.phase === PHASE.STOPPING && pendingMatches(operation, event, ['stop'])) return serviceStopped(clearPending(operation, serviceId), serviceId, authority)
      if ([PHASE.ROLLING_BACK, PHASE.RECOVERING, PHASE.RESIDUE].includes(operation.phase)) return setService(operation, serviceId, SERVICE.STOPPED, operation.phase)
      return invalid(operation)
    }
    case 'stop_failed': return inPhase(operation, [PHASE.STOPPING, PHASE.RESIDUE]) && pendingMatches(operation, event, ['stop'])
      ? residue(clearPending(operation, serviceId), serviceId, REASON.STOP_FAILED, authority) : invalid(operation)
    case 'supervisor_crashed': return inPhase(operation, [PHASE.PLANNED, PHASE.PREFLIGHT, PHASE.PREPARED, PHASE.STARTING, PHASE.WAITING_READY, PHASE.READY, PHASE.ROLLING_BACK, PHASE.STOPPING])
      ? next(operation, {
          phase: PHASE.RECOVERING, reason: firstFailure(operation, REASON.SUPERVISOR_CRASH), cleanup: CLEANUP.UNKNOWN,
          primary_result: primaryResult(operation, REASON.SUPERVISOR_CRASH, 'launcher_supervisor'),
          cleanup_result: cleanupResult(CLEANUP.UNKNOWN, 'launcher_supervisor'), rollback_required: false, recovery_required: true
        })
      : invalid(operation)
    case 'recovery_started': return operation.recovery_required && operation.phase === PHASE.RECOVERING
      ? next(operation, { phase: PHASE.RECOVERING, cleanup: CLEANUP.IN_PROGRESS }) : invalid(operation)
    case 'recovery_completed': {
      if (!operation.recovery_required || operation.phase !== PHASE.RECOVERING) return invalid(operation)
      const outstanding = outstandingOwned(operation, authority)
      if (outstanding.length > 0 || operation.residue_service_ids.length > 0) return retainUnknownResidue(operation, outstanding)
      return next(normalizePendingOwned(operation, authority), {
        phase: operation.intent === 'stop' ? PHASE.STOPPED : PHASE.FAILED,
        cleanup: CLEANUP.CLEAR, cleanup_result: cleanupResult(CLEANUP.CLEAR), recovery_required: false
      })
    }
    case 'residue_observed': return inPhase(operation, [PHASE.RECOVERING, PHASE.RESIDUE]) ? residue(operation, serviceId, REASON.RESIDUE_PRESENT, authority) : invalid(operation)
    case 'residue_cleared': {
      if (operation.phase !== PHASE.RESIDUE || !operation.residue_service_ids.includes(serviceId)) return invalid(operation)
      const residueIds = operation.residue_service_ids.filter((id) => id !== serviceId)
      const services = operation.services.map((service) => service.service_id === serviceId ? { ...service, state: SERVICE.STOPPED } : { ...service })
      const changed = cloneOperation(operation, { services, residue_service_ids: residueIds })
      const outstanding = outstandingOwned(changed, authority)
      if (residueIds.length > 0 || outstanding.length > 0) return retainUnknownResidue(changed, outstanding)
      return next(normalizePendingOwned(changed, authority), {
        phase: operation.intent === 'stop' ? PHASE.STOPPED : PHASE.FAILED,
        cleanup: CLEANUP.CLEAR, cleanup_result: cleanupResult(CLEANUP.CLEAR), recovery_required: false
      })
    }
    default: return invalid(operation)
  }
}

const allowed = {
  phase: new Set(Object.values(PHASE)), reason: new Set(Object.values(REASON)), cleanup: new Set(Object.values(CLEANUP)), service: new Set(Object.values(SERVICE))
}
const cleanupMatches = (phase, cleanup) => ({
  [CLEANUP.NOT_STARTED]: [PHASE.PLANNED, PHASE.PREFLIGHT, PHASE.PREPARED, PHASE.STARTING, PHASE.WAITING_READY, PHASE.READY],
  [CLEANUP.IN_PROGRESS]: [PHASE.ROLLING_BACK, PHASE.STOPPING, PHASE.RECOVERING],
  [CLEANUP.CLEAR]: [PHASE.FAILED, PHASE.STOPPED],
  [CLEANUP.RESIDUE]: [PHASE.RESIDUE],
  [CLEANUP.UNKNOWN]: [PHASE.RECOVERING, PHASE.RESIDUE]
}[cleanup] || []).includes(phase)

const validateSnapshot = (operation, authority) => {
  assertAuthority(authority)
  validateIdentityInputs(operation?.operation_id, operation?.graph_sha256, operation?.binding_sha256)
  if (operation.schema_version !== 'launcher_operation.v2' || operation.graph_sha256 !== authority.identities.graphSha256 ||
      operation.binding_sha256 !== authority.identities.bindingSha256) fail('operation_store_identity_mismatch')
  const exact = ['schema_version', 'graph_sha256', 'binding_sha256', 'operation_id', 'supervisor_generation', 'intent', 'phase', 'reason', 'cleanup', 'primary_result', 'cleanup_result', 'revision', 'joined_existing', 'rollback_required', 'recovery_required', 'services', 'residue_service_ids'].sort()
  if (!operation || Object.keys(operation).sort().some((key, index) => key !== exact[index]) || Object.keys(operation).length !== exact.length) fail('operation_store_record_invalid')
  if (!Number.isSafeInteger(operation.supervisor_generation) || operation.supervisor_generation < 1 || operation.supervisor_generation > Number.MAX_SAFE_INTEGER ||
      !operation.primary_result || Object.keys(operation.primary_result).sort().join(',') !== 'action_certainty,class,responsible_id' ||
      !allowed.reason.has(operation.primary_result.class) || !['not_attempted', 'not_submitted', 'may_have_occurred', 'observed'].includes(operation.primary_result.action_certainty) ||
      !(operation.primary_result.responsible_id === null || typeof operation.primary_result.responsible_id === 'string') ||
      !operation.cleanup_result || Object.keys(operation.cleanup_result).sort().join(',') !== 'class,responsible_id' ||
      !['not_started', 'in_progress', 'clear', 'rollback_failed', 'stop_failed', 'residue', 'unknown'].includes(operation.cleanup_result.class) ||
      !(operation.cleanup_result.responsible_id === null || typeof operation.cleanup_result.responsible_id === 'string') ||
      !['start', 'stop'].includes(operation.intent) || !Number.isSafeInteger(operation.revision) || operation.revision < 0 || operation.revision > Number.MAX_SAFE_INTEGER ||
      !allowed.phase.has(operation.phase) || !allowed.reason.has(operation.reason) || !allowed.cleanup.has(operation.cleanup) ||
      typeof operation.joined_existing !== 'boolean' || typeof operation.rollback_required !== 'boolean' || typeof operation.recovery_required !== 'boolean' ||
      !Array.isArray(operation.services) || !Array.isArray(operation.residue_service_ids)) fail('operation_store_record_invalid')
  const expected = authority.graph.services.map((service) => service.service_id).sort()
  const actual = operation.services.map((service) => service.service_id).sort()
  if (JSON.stringify(expected) !== JSON.stringify(actual) || new Set(actual).size !== actual.length ||
      operation.services.some((service) => Object.keys(service).sort().join(',') !== 'attempt_sequence,pending_action,pending_dispatch_id,service_id,state' ||
        !allowed.service.has(service.state) || !Number.isSafeInteger(service.attempt_sequence) || service.attempt_sequence < 0 ||
        !(service.pending_dispatch_id === null || (typeof service.pending_dispatch_id === 'string' && DISPATCH_ID.test(service.pending_dispatch_id))) ||
        ![null, 'start', 'probe', 'stop'].includes(service.pending_action) || ((service.pending_dispatch_id === null) !== (service.pending_action === null))) ||
      new Set(operation.residue_service_ids).size !== operation.residue_service_ids.length || operation.residue_service_ids.some((id) => !expected.includes(id))) fail('operation_store_record_invalid')

  const specs = new Map(authority.graph.services.map((service) => [service.service_id, service]))
  const states = new Map(operation.services.map((service) => [service.service_id, service.state]))
  const residueIds = new Set(operation.residue_service_ids)
  for (const [serviceId, state] of states) {
    const spec = specs.get(serviceId)
    if (state === SERVICE.OPTIONAL_ABSENT && spec.requirement !== 'optional') fail('operation_store_record_invalid')
    if (spec.ownership === 'external') {
      if (![SERVICE.PENDING, SERVICE.EXTERNAL_READY, SERVICE.FAILED].includes(state) || residueIds.has(serviceId)) fail('operation_store_record_invalid')
    } else if (state === SERVICE.EXTERNAL_READY) fail('operation_store_record_invalid')
    if ([SERVICE.RESIDUE, SERVICE.UNKNOWN].includes(state) && !residueIds.has(serviceId)) fail('operation_store_record_invalid')
  }
  if (operation.reason !== operation.primary_result.class) fail('operation_store_record_invalid')
  for (const serviceId of residueIds) {
    if (specs.get(serviceId).ownership !== 'owned' || ![SERVICE.RESIDUE, SERVICE.UNKNOWN, SERVICE.STOPPED].includes(states.get(serviceId))) fail('operation_store_record_invalid')
  }

  const ownedStates = authority.graph.services.filter((service) => service.ownership === 'owned').map((service) => states.get(service.service_id))
  const allInitial = operation.services.every((service) => service.state === SERVICE.PENDING)
  const allOwnedClear = ownedStates.every((state) => [SERVICE.STOPPED, SERVICE.OPTIONAL_ABSENT].includes(state))
  const outstanding = authority.graph.services.filter((service) => service.ownership === 'owned' && ![SERVICE.PENDING, SERVICE.STOPPED, SERVICE.OPTIONAL_ABSENT].includes(states.get(service.service_id))).map((service) => service.service_id)
  const allAreReady = allReady(operation, authority)
  const dependenciesSatisfied = operation.services.filter((service) => service.state !== SERVICE.PENDING).every((service) => dependenciesReady(operation, specs.get(service.service_id), authority))

  if (operation.cleanup === CLEANUP.CLEAR && (![PHASE.FAILED, PHASE.STOPPED].includes(operation.phase) || !allOwnedClear || residueIds.size !== 0 || operation.rollback_required || operation.recovery_required)) fail('operation_store_record_invalid')
  if (!cleanupMatches(operation.phase, operation.cleanup)) fail('operation_store_record_invalid')
  if (operation.rollback_required !== (operation.phase === PHASE.ROLLING_BACK) || operation.recovery_required !== [PHASE.RECOVERING, PHASE.RESIDUE].includes(operation.phase)) fail('operation_store_record_invalid')
  const activeReason = [REASON.NONE, REASON.INVALID_EVENT].includes(operation.reason)
  if ([PHASE.PLANNED, PHASE.PREFLIGHT, PHASE.PREPARED].includes(operation.phase) && (operation.intent !== 'start' || !allInitial || residueIds.size !== 0 || !activeReason)) fail('operation_store_record_invalid')
  if ([PHASE.STARTING, PHASE.WAITING_READY].includes(operation.phase) && (operation.intent !== 'start' || residueIds.size !== 0 ||
      operation.services.some((service) => ![SERVICE.PENDING, SERVICE.STARTING, SERVICE.READY, SERVICE.OPTIONAL_ABSENT, SERVICE.EXTERNAL_READY].includes(service.state)) || !dependenciesSatisfied || !activeReason)) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.READY && (operation.intent !== 'start' || !allAreReady || !dependenciesSatisfied || residueIds.size !== 0 || !activeReason)) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.ROLLING_BACK && (operation.intent !== 'start' || operation.reason === REASON.NONE || residueIds.size !== 0)) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.FAILED && (operation.intent !== 'start' || operation.reason === REASON.NONE)) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.STOPPING && (operation.intent !== 'stop' || residueIds.size !== 0 || ownedStates.some((state) => ![SERVICE.STOP_REQUESTED, SERVICE.STOPPED, SERVICE.OPTIONAL_ABSENT].includes(state)))) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.STOPPED && operation.intent !== 'stop') fail('operation_store_record_invalid')
  if (operation.phase === PHASE.RECOVERING && (operation.reason === REASON.NONE || residueIds.size !== 0)) fail('operation_store_record_invalid')
  if (operation.phase === PHASE.RESIDUE && (operation.reason === REASON.NONE || residueIds.size === 0 || outstanding.some((id) => !residueIds.has(id)))) fail('operation_store_record_invalid')
  return operation
}

const workerResultToEvent = (result, current, expectedRequest, authority) => {
  assertAuthority(authority)
  validateSnapshot(current, authority)
  validateWorkerRequestAgainstAuthority(expectedRequest, authority)
  validateWorkerMessage(result, authority)
  const pending = current.services.find((service) => service.service_id === expectedRequest.service_id)
  if (expectedRequest.message_type !== 'request' || result.message_type !== 'result' ||
      expectedRequest.operation_id !== current.operation_id || expectedRequest.supervisor_generation !== current.supervisor_generation ||
      !pending || pending.pending_dispatch_id !== expectedRequest.dispatch_id || pending.pending_action !== expectedRequest.action ||
      expectedRequest.graph_sha256 !== authority.identities.graphSha256 || expectedRequest.binding_sha256 !== authority.identities.bindingSha256 ||
      result.operation_id !== expectedRequest.operation_id || result.service_id !== expectedRequest.service_id ||
      result.supervisor_generation !== expectedRequest.supervisor_generation || result.authority_lease_proof !== expectedRequest.authority_lease_proof ||
      result.dispatch_id !== expectedRequest.dispatch_id ||
      result.action !== expectedRequest.action || result.expected_revision !== expectedRequest.expected_revision ||
      result.worker_nonce !== expectedRequest.worker_nonce) fail('worker_result_correlation_mismatch')
  const spec = specOf(authority, expectedRequest.service_id)
  if (!spec) fail('worker_result_service_unknown')
  const compatibleResults = {
    start: new Set(['accepted', 'spawn_failed', 'early_exit', 'listener_mismatch', 'deadline', 'cancelled', 'invalid_request', 'internal_failure']),
    probe: new Set(['ready', 'optional_absent', 'external_ready', 'early_exit', 'listener_mismatch', 'readiness_timeout', 'deadline', 'cancelled', 'invalid_request', 'internal_failure']),
    stop: new Set(['stopped', 'listener_mismatch', 'stop_failed', 'deadline', 'cancelled', 'invalid_request', 'internal_failure'])
  }
  if (!compatibleResults[result.action].has(result.result_class)) fail('worker_result_action_incompatible')
  if (result.result_class === 'optional_absent' && spec.requirement !== 'optional') fail('worker_result_ownership_incompatible')
  if (result.result_class === 'external_ready' && spec.ownership !== 'external') fail('worker_result_ownership_incompatible')
  if (['accepted', 'ready', 'stopped'].includes(result.result_class) && spec.ownership !== 'owned') fail('worker_result_ownership_incompatible')
  if (result.ownership_class === 'mismatch' || result.listener_class === 'mismatch' || result.descendant_class === 'foreign') {
    return { event_type: 'listener_mismatch', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
  }
  if (spec.ownership === 'owned') {
    if (['accepted', 'ready', 'stopped'].includes(result.result_class) && result.ownership_class !== 'matched') {
      return { event_type: 'listener_mismatch', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
    }
    if (['accepted', 'ready'].includes(result.result_class) && result.descendant_class !== 'owned_active') {
      return { event_type: 'listener_mismatch', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
    }
    if (result.result_class === 'ready' && result.listener_class !== 'matched') {
      return { event_type: 'listener_mismatch', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
    }
    if (result.result_class === 'stopped' && result.descendant_class !== 'owned_clear') {
      return { event_type: 'stop_failed', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
    }
  } else if (result.result_class === 'external_ready' &&
      (result.ownership_class !== 'not_applicable' || result.listener_class !== 'matched' || result.descendant_class !== 'not_applicable')) {
    return { event_type: 'listener_mismatch', operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
  }
  const byResult = {
    accepted: 'spawn_succeeded',
    ready: 'service_ready',
    optional_absent: 'optional_absent',
    external_ready: 'external_ready',
    stopped: 'service_stopped',
    spawn_failed: 'spawn_failed',
    early_exit: 'early_exit',
    listener_mismatch: 'listener_mismatch',
    readiness_timeout: 'readiness_timeout',
    stop_failed: 'stop_failed',
    deadline: result.action === 'stop' ? 'stop_failed' : 'readiness_timeout',
    cancelled: result.action === 'stop' ? 'stop_failed' : 'early_exit',
    invalid_request: result.action === 'stop' ? 'stop_failed' : 'spawn_failed',
    internal_failure: result.action === 'stop' ? 'stop_failed' : 'spawn_failed'
  }
  return { event_type: byResult[result.result_class], operation_id: current.operation_id, service_id: result.service_id, dispatch_id: result.dispatch_id }
}

module.exports = { CLEANUP, PHASE, REASON, SERVICE, createOperation, reduce, startOperation, validateIdentityInputs, validateSnapshot, workerResultToEvent }
