'use strict'

const crypto = require('node:crypto')

const { LauncherContractError, assertAuthority, canonicalJsonSha256, loadAuthority } = require('./launcher-supervisor-contract')
const operationStore = require('./launcher-operation-store')
const reducer = require('./launcher-supervisor-reducer')
const {
  LauncherJobWorkerClient,
  LauncherJobWorkerError,
  PowerShellJsonLineTransport
} = require('./launcher-job-worker-client')
const {
  LauncherPrivatePlanError,
  compilePrivateServicePlan,
  deriveEffectiveConfigIdentity,
  validateClosedLoopJournalBinding,
  readPrivateServicePlan,
  removePrivateServicePlan,
  writePrivateServicePlan
} = require('./launcher-private-service-plan')
const { LauncherProbeExecutorError } = require('./launcher-probe-executor')
const { LauncherProbeContractError } = require('./launcher-probe-result-binding')
const { LauncherProbeRuntimeContextError } = require('./launcher-probe-runtime-context')

class LauncherOperationStorePersistenceError extends Error {
  constructor () {
    super('operation_store_persistence_failed')
    this.name = 'LauncherOperationStorePersistenceError'
  }
}

const SEMANTIC_PROBE_STAGE_IDS = new Set([
  'semantic_probe_expectation',
  'semantic_probe_executor',
  'semantic_probe_result'
])

class LauncherSemanticProbeStageError extends Error {
  constructor (responsibleId) {
    super('semantic_probe_stage_failed')
    this.name = 'LauncherSemanticProbeStageError'
    this.responsibleId = SEMANTIC_PROBE_STAGE_IDS.has(responsibleId)
      ? responsibleId
      : 'launcher_supervisor'
  }
}

const serviceLoopFailureResponsibleId = (error, serviceId) =>
  error instanceof LauncherOperationStorePersistenceError
    ? 'operation_store'
    : error instanceof LauncherSemanticProbeStageError
      ? error.responsibleId
      : serviceId

const ACTIVE_PHASES = new Set([
  reducer.PHASE.PLANNED,
  reducer.PHASE.PREFLIGHT,
  reducer.PHASE.PREPARED,
  reducer.PHASE.STARTING,
  reducer.PHASE.WAITING_READY,
  reducer.PHASE.READY,
  reducer.PHASE.ROLLING_BACK,
  reducer.PHASE.STOPPING,
  reducer.PHASE.RECOVERING,
  reducer.PHASE.RESIDUE
])
const PUBLIC_RESULT_CLASSES = new Set([
  'already_stopped',
  'failed',
  'idle',
  'joined_existing',
  'operation_in_progress',
  'preflight_failed',
  'ready',
  'residue',
  'stopped'
])
const PUBLIC_ERROR_CLASSES = new Set([
  'none',
  'private_plan_invalid',
  'supervisor_runtime_failed'
])
const planIdentityOf = (compiled) => {
  const identity = {
    private_plan_sha256: compiled?.private_plan_sha256,
    worker_executable_class: compiled?.worker_executable_class,
    worker_executable_sha256: compiled?.worker_executable_sha256
  }
  try { reducer.validatePlanIdentity(identity) } catch { throw new LauncherPrivatePlanError('private_plan_identity_invalid') }
  return Object.freeze(identity)
}
const samePlanIdentity = (operation, identity) => Boolean(operation) &&
  operation.private_plan_sha256 === identity.private_plan_sha256 &&
  operation.worker_executable_class === identity.worker_executable_class &&
  operation.worker_executable_sha256 === identity.worker_executable_sha256
const SHA256 = /^[a-f0-9]{64}$/u

const safeResultClass = (value) => PUBLIC_RESULT_CLASSES.has(value) ? value : 'failed'
const safeErrorClass = (value) => PUBLIC_ERROR_CLASSES.has(value) ? value : 'supervisor_runtime_failed'
const sameConfigIdentity = (left, right) => Boolean(left) && Boolean(right) &&
  left.profile_id === right.profile_id &&
  left.effective_config_sha256 === right.effective_config_sha256 &&
  left.camera_policy === right.camera_policy
const sameStoredOperation = (left, right) => Boolean(left) && Boolean(right) &&
  canonicalJsonSha256(left) === canonicalJsonSha256(right)
const eventFor = (operation, eventType, serviceId = null, fields = {}) => ({
  event_type: eventType,
  operation_id: operation.operation_id,
  ...(serviceId ? { service_id: serviceId } : {}),
  ...fields
})

const publicOperation = (operation, profileId = 'thought-core-v0') => {
  if (!operation) {
    return {
      schema_version: 'launcher_supervisor_public.v1',
      authority_class: 'node_supervisor',
      profile_id: profileId,
      effective_config_sha256: null,
      camera_policy: null,
      operation_id: null,
      intent: 'none',
      phase: 'idle',
      reason: 'none',
      cleanup: 'clear',
      revision: 0,
      joined_existing: false,
      rollback_required: false,
      recovery_required: false,
      services: [],
      residue_service_ids: [],
      raw_private_publication_flags: false
    }
  }
  return {
    schema_version: 'launcher_supervisor_public.v1',
    authority_class: 'node_supervisor',
    profile_id: operation.profile_id,
    effective_config_sha256: operation.effective_config_sha256,
    camera_policy: operation.camera_policy,
    operation_id: operation.operation_id,
    intent: operation.intent,
    phase: operation.phase,
    reason: operation.reason,
    cleanup: operation.cleanup,
    revision: operation.revision,
    joined_existing: operation.joined_existing,
    rollback_required: operation.rollback_required,
    recovery_required: operation.recovery_required,
    services: operation.services.map((service) => ({
      service_id: service.service_id,
      state: service.state
    })),
    residue_service_ids: [...operation.residue_service_ids],
    raw_private_publication_flags: false
  }
}

const publicOperationStoreFailure = (profileId = 'thought-core-v0') => ({
  schema_version: 'launcher_supervisor_public.v1',
  authority_class: 'node_supervisor',
  profile_id: profileId,
  effective_config_sha256: null,
  camera_policy: null,
  operation_id: null,
  intent: 'none',
  phase: 'failed',
  reason: 'invalid_event',
  cleanup: 'unknown',
  revision: 0,
  joined_existing: false,
  rollback_required: false,
  recovery_required: false,
  services: [],
  residue_service_ids: [],
  raw_private_publication_flags: false
})

const resultClassFor = (operation) => {
  if (!operation) return 'idle'
  if (operation.phase === reducer.PHASE.READY) return 'ready'
  if (operation.phase === reducer.PHASE.STOPPED) return 'stopped'
  if (operation.phase === reducer.PHASE.RESIDUE) return 'residue'
  if (operation.phase === reducer.PHASE.FAILED) return 'failed'
  return 'operation_in_progress'
}

const publicResult = ({
  ok,
  resultClass,
  operation,
  profileId,
  errorClass = 'none'
}) => ({
  ok: Boolean(ok),
  schema_version: 'launcher_supervisor_result.v1',
  result_class: safeResultClass(resultClass),
  error_class: safeErrorClass(errorClass),
  operation: publicOperation(operation, profileId),
  raw_private_publication_flags: false
})

class LauncherSupervisorRuntime {
  constructor ({
    repositoryRoot,
    workspaceRoot,
    privateRuntimeRoot,
    authority = null,
    store = operationStore,
    planCompiler = compilePrivateServicePlan,
    planReader = readPrivateServicePlan,
    planWriter = writePrivateServicePlan,
    planRemover = removePrivateServicePlan,
    workerFactory = null,
    probeExecutor = null,
    probeExecutorFactory = null,
    operationIdFactory = () => `lop_${crypto.randomBytes(16).toString('hex')}`,
    workerNonceFactory = () => `lw_${crypto.randomBytes(16).toString('hex')}`,
    dispatchIdFactory = () => `ld_${crypto.randomBytes(16).toString('hex')}`
  }) {
    this.repositoryRoot = repositoryRoot
    this.workspaceRoot = workspaceRoot
    this.privateRuntimeRoot = privateRuntimeRoot
    this.authority = authority || loadAuthority(repositoryRoot)
    assertAuthority(this.authority)
    if (
      !store ||
      typeof store.startAndPersist !== 'function' ||
      typeof store.reduceAndPersist !== 'function' ||
      typeof store.readOperation !== 'function' ||
      typeof planCompiler !== 'function' ||
      typeof planReader !== 'function' ||
      typeof planWriter !== 'function' ||
      typeof planRemover !== 'function' ||
      typeof operationIdFactory !== 'function' ||
      typeof workerNonceFactory !== 'function' ||
      typeof dispatchIdFactory !== 'function' ||
      !(probeExecutor === null || (probeExecutor && typeof probeExecutor.execute === 'function' &&
        typeof probeExecutor.configSha256 === 'string' && SHA256.test(probeExecutor.configSha256))) ||
      !(probeExecutorFactory === null || typeof probeExecutorFactory === 'function') ||
      (probeExecutor !== null && probeExecutorFactory !== null) ||
      typeof store.getSupervisorLeaseBinding !== 'function' ||
      typeof store.releaseSupervisorLease !== 'function' ||
      typeof store.acquireSupervisorLease !== 'function'
    ) {
      throw new Error('supervisor_runtime_configuration_invalid')
    }
    this.store = store
    this.planCompiler = planCompiler
    this.planReader = planReader
    this.planWriter = planWriter
    this.planRemover = planRemover
    this.workerFactory = workerFactory || (({ compiled, planPath, supervisorLease }) => new LauncherJobWorkerClient({
      authority: this.authority,
      supervisorLease,
      transport: new PowerShellJsonLineTransport({
        repositoryRoot: this.repositoryRoot,
        privatePlanPath: planPath,
        powershellPath: compiled.powershell_path,
        privatePlanSha256: compiled.private_plan_sha256,
        workerExecutableClass: compiled.worker_executable_class,
        workerExecutableSha256: compiled.worker_executable_sha256,
        authority: this.authority,
        supervisorLease
      })
    }))
    this.probeExecutor = probeExecutor
    this.probeExecutorFactory = probeExecutorFactory
    this.generatedProbeExecutor = false
    this.operationIdFactory = operationIdFactory
    this.workerNonceFactory = workerNonceFactory
    this.dispatchIdFactory = dispatchIdFactory
    this.current = null
    this.client = null
    this.compiled = null
    this.supervisorLease = null
    this.leaseBinding = null
    this.inflight = null
    this.profileId = 'thought-core-v0'
  }

  readStoredOperation () {
    try {
      return this.store.readOperation(this.authority, this.privateRuntimeRoot)
    } catch (error) {
      if (error instanceof LauncherContractError && error.code === 'operation_store_record_missing') {
        return null
      }
      throw error
    }
  }

  readCurrent () {
    this.current = this.readStoredOperation()
    return this.current
  }

  readAuthoritativeJoinOperation (cachedOperation, validatedConfigIdentity) {
    const storedOperation = this.readStoredOperation()
    if (!sameStoredOperation(cachedOperation, storedOperation) ||
        !sameConfigIdentity(storedOperation, validatedConfigIdentity)) {
      throw new LauncherPrivatePlanError('private_plan_config_invalid')
    }
    return storedOperation
  }

  publicState () {
    try {
      return publicOperation(this.readCurrent(), this.profileId)
    } catch {
      this.current = null
      return publicOperationStoreFailure(this.profileId)
    }
  }

  isClearTerminalFailure () {
    try {
      return reducer.isClearTerminalFailure(this.current, this.authority)
    } catch {
      return false
    }
  }

  status () {
    const operation = this.readCurrent()
    return publicResult({
      ok: true,
      resultClass: resultClassFor(operation),
      operation,
      profileId: this.profileId
    })
  }

  apply (eventType, serviceId = null, fields = {}) {
    if (!this.current) throw new Error('supervisor_runtime_operation_missing')
    this.current = this.store.reduceAndPersist(
      this.current,
      eventFor(this.current, eventType, serviceId, fields),
      this.authority,
      this.privateRuntimeRoot
    )
    return this.current
  }

  applyPersistedEvent (eventType, serviceId, fields = {}) {
    try {
      return this.apply(eventType, serviceId, fields)
    } catch (error) {
      if (error instanceof LauncherContractError) throw new LauncherOperationStorePersistenceError()
      throw error
    }
  }

  applySemanticProbeEvent (eventType, serviceId, fields = {}) {
    return this.applyPersistedEvent(eventType, serviceId, fields)
  }

  bindSupervisorLease (supervisorLease) {
    const binding = this.store.getSupervisorLeaseBinding(supervisorLease, this.authority)
    if (!this.current || binding.operation_id !== this.current.operation_id ||
        binding.supervisor_generation !== this.current.supervisor_generation) {
      throw new Error('supervisor_runtime_lease_invalid')
    }
    this.supervisorLease = supervisorLease
    this.leaseBinding = binding
  }

  requestFor (serviceId, action, dispatchId) {
    const spec = this.authority.graph.services.find((service) => service.service_id === serviceId)
    const pending = this.current?.services.find((service) => service.service_id === serviceId)
    if (!spec || !this.current || !this.leaseBinding ||
        pending?.pending_dispatch_id !== dispatchId || pending?.pending_action !== action) {
      throw new Error('supervisor_runtime_request_invalid')
    }
    const adapterClass = action === 'start'
      ? spec.start.adapter_id
      : action === 'stop'
        ? spec.stop.adapter_id
        : spec.ownership === 'external' ? 'external_probe_only' : 'job_worker_service'
    return {
      schema_version: 'launcher_worker.v2',
      message_type: 'request',
      operation_id: this.current.operation_id,
      supervisor_generation: this.current.supervisor_generation,
      authority_lease_proof: this.leaseBinding.authority_lease_proof,
      dispatch_id: dispatchId,
      graph_sha256: this.authority.identities.graphSha256,
      binding_sha256: this.authority.identities.bindingSha256,
      service_id: serviceId,
      action,
      adapter_class: adapterClass,
      expected_revision: this.current.revision,
      deadline_ms: action === 'stop' ? spec.stop.graceful_timeout_ms : spec.ready_deadline_ms,
      worker_nonce: this.workerNonceFactory()
    }
  }

  async exchange (serviceId, action) {
    if (!this.client) throw new LauncherJobWorkerError('worker_transport_closed')
    const requestEvent = action === 'start' ? 'spawn_requested' : action === 'stop' ? 'stop_dispatch_requested' : 'probe_requested'
    const dispatchId = this.dispatchIdFactory()
    this.applyPersistedEvent(requestEvent, serviceId, { dispatch_id: dispatchId, action })
    const request = this.requestFor(serviceId, action, dispatchId)
    const result = await this.client.execute(request)
    return reducer.workerResultToEvent(result, this.current, request, this.authority)
  }

  compile (profileId, options, configIdentity) {
    const compiled = this.planCompiler({
      repositoryRoot: this.repositoryRoot,
      workspaceRoot: this.workspaceRoot,
      privateRuntimeRoot: this.privateRuntimeRoot,
      profileId,
      options,
      configIdentity,
      authority: this.authority
    })
    validateClosedLoopJournalBinding({
      document: compiled?.document,
      privateRuntimeRoot: this.privateRuntimeRoot
    })
    return compiled
  }

  readPersistedPlan (operation = this.current) {
    if (!operation) throw new LauncherPrivatePlanError('private_plan_identity_invalid')
    return this.planReader({
      privateRuntimeRoot: this.privateRuntimeRoot,
      configIdentity: {
        profile_id: operation.profile_id,
        effective_config_sha256: operation.effective_config_sha256,
        camera_policy: operation.camera_policy
      },
      planIdentity: {
        private_plan_sha256: operation.private_plan_sha256,
        worker_executable_class: operation.worker_executable_class,
        worker_executable_sha256: operation.worker_executable_sha256
      },
      authority: this.authority
    })
  }

  ensureClient (compiled) {
    if (this.client) return
    if (!this.supervisorLease || !this.leaseBinding) throw new Error('supervisor_runtime_lease_missing')
    const planIdentity = planIdentityOf(compiled)
    if (!samePlanIdentity(this.current, planIdentity)) throw new LauncherPrivatePlanError('private_plan_identity_invalid')
    const planPath = typeof compiled?.plan_path === 'string'
      ? compiled.plan_path
      : (() => {
          this.planRemover(this.privateRuntimeRoot)
          return this.planWriter(compiled, this.privateRuntimeRoot)
        })()
    const retainedCompiled = typeof compiled?.plan_path === 'string'
      ? compiled
      : Object.freeze({ ...compiled, plan_path: planPath })
    this.client = this.workerFactory({
      authority: this.authority,
      compiled: retainedCompiled,
      planPath,
      supervisorLease: this.supervisorLease
    })
    this.compiled = retainedCompiled
  }

  ensureProbeExecutor (compiled) {
    if (this.probeExecutor) return
    if (!this.probeExecutorFactory) throw new Error('supervisor_runtime_probe_executor_missing')
    const executor = this.probeExecutorFactory({
      repositoryRoot: this.repositoryRoot,
      privateRuntimeRoot: this.privateRuntimeRoot,
      compiled,
      authority: this.authority
    })
    if (!executor || typeof executor.execute !== 'function' ||
        typeof executor.configSha256 !== 'string' || !SHA256.test(executor.configSha256)) {
      throw new Error('supervisor_runtime_probe_executor_invalid')
    }
    this.probeExecutor = executor
    this.generatedProbeExecutor = true
  }

  async closeClientAndPlan ({ removePlan = true } = {}) {
    const client = this.client
    let clientClear = true
    if (client) {
      try { await client.close() } catch { clientClear = false }
    }
    if (clientClear) this.client = null
    let planClear = !removePlan
    if (removePlan && clientClear) {
      try {
        this.planRemover(this.privateRuntimeRoot)
        planClear = true
      } catch {
        planClear = false
      }
    }
    if (planClear) {
      if (removePlan) {
        this.compiled = null
        if (this.generatedProbeExecutor) {
          this.probeExecutor = null
          this.generatedProbeExecutor = false
        }
      }
    }
    return clientClear && planClear
  }

  probeExpectationFor (serviceId, dispatchId) {
    const spec = this.authority.graph.services.find((service) => service.service_id === serviceId)
    const service = this.current?.services.find((candidate) => candidate.service_id === serviceId)
    const descriptor = this.authority.probeDocument.descriptors.find((candidate) => (
      candidate.service_id === serviceId && candidate.probe_id === spec?.readiness?.probe_id
    ))
    if (!spec || !service || !descriptor || !this.compiled || service.probe_status !== 'transport_ready' ||
        service.pending_action !== 'probe' || service.pending_dispatch_id !== dispatchId ||
        !Number.isSafeInteger(service.probe_expected_revision)) {
      throw new Error('supervisor_runtime_probe_expectation_invalid')
    }
    return Object.freeze({
      operation_id: this.current.operation_id,
      supervisor_generation: this.current.supervisor_generation,
      dispatch_id: dispatchId,
      expected_revision: service.probe_expected_revision,
      service_id: serviceId,
      probe_id: descriptor.probe_id,
      graph_sha256: this.authority.identities.graphSha256,
      binding_sha256: this.authority.identities.bindingSha256,
      descriptor_sha256: canonicalJsonSha256(descriptor),
      config_sha256: this.probeExecutor.configSha256,
      requested_at: new Date().toISOString()
    })
  }

  async completeSemanticProbe (serviceId, dispatchId) {
    if (!this.probeExecutor) throw new LauncherSemanticProbeStageError('semantic_probe_executor')
    let expectation
    try {
      expectation = this.probeExpectationFor(serviceId, dispatchId)
    } catch {
      throw new LauncherSemanticProbeStageError('semantic_probe_expectation')
    }
    let probeResult
    try {
      probeResult = await this.probeExecutor.execute(expectation)
    } catch (error) {
      if (error instanceof LauncherProbeExecutorError || error instanceof LauncherProbeContractError ||
          error instanceof LauncherProbeRuntimeContextError) {
        this.applySemanticProbeEvent('probe_failed', serviceId, { dispatch_id: dispatchId })
        return
      }
      throw new LauncherSemanticProbeStageError('semantic_probe_executor')
    }
    try {
      this.applySemanticProbeEvent('semantic_probe_completed', serviceId, {
        dispatch_id: dispatchId,
        probe_result: probeResult
      })
    } catch (error) {
      if (error instanceof LauncherOperationStorePersistenceError) throw error
      throw new LauncherSemanticProbeStageError('semantic_probe_result')
    }
    const service = this.current.services.find((candidate) => candidate.service_id === serviceId)
    if (service?.pending_dispatch_id === dispatchId || service?.pending_action === 'probe') {
      throw new LauncherSemanticProbeStageError('semantic_probe_result')
    }
  }

  releaseSupervisorLease () {
    if (!this.supervisorLease) return true
    const lease = this.supervisorLease
    try {
      this.store.releaseSupervisorLease(lease, this.authority)
      this.supervisorLease = null
      this.leaseBinding = null
      return true
    } catch {
      return false
    }
  }

  acquireExistingSupervisorLease () {
    if (this.supervisorLease) return
    if (!this.current) throw new Error('supervisor_runtime_operation_missing')
    const lease = this.store.acquireSupervisorLease({
      operationId: this.current.operation_id,
      supervisorGeneration: this.current.supervisor_generation,
      authority: this.authority,
      authorizedPrivateRuntimeRoot: this.privateRuntimeRoot
    })
    this.bindSupervisorLease(lease)
  }

  stopCandidates () {
    if (!this.current) return []
    const byId = new Map(this.current.services.map((service) => [service.service_id, service.state]))
    return [...this.authority.bindingDocument.binding.service_order]
      .reverse()
      .filter((serviceId) => {
        const spec = this.authority.graph.services.find((service) => service.service_id === serviceId)
        const state = byId.get(serviceId)
        return spec?.ownership === 'owned' &&
          ![reducer.SERVICE.PENDING, reducer.SERVICE.STOPPED, reducer.SERVICE.OPTIONAL_ABSENT].includes(state)
      })
  }

  pendingDispatchId (serviceId, action) {
    const service = this.current?.services.find((candidate) => candidate.service_id === serviceId)
    return service?.pending_action === action ? service.pending_dispatch_id : null
  }

  cleanupFailure (mode, serviceId) {
    const dispatchId = this.pendingDispatchId(serviceId, 'stop')
    const fields = dispatchId ? { dispatch_id: dispatchId } : {}
    if (mode === 'rollback') return this.apply('rollback_failed', serviceId, fields)
    if (mode === 'recovery') return this.apply('residue_observed', serviceId, fields)
    return this.apply('stop_failed', serviceId, fields)
  }

  async stopOwnedServices (mode, compiled) {
    const candidates = this.stopCandidates()
    let held = null
    let heldServiceId = null
    try {
      this.ensureClient(compiled)
      for (let index = 0; index < candidates.length; index += 1) {
        const serviceId = candidates[index]
        let workerEvent
        try {
          workerEvent = await this.exchange(serviceId, 'stop')
        } catch {
          this.cleanupFailure(mode, serviceId)
          const closed = await this.closeClientAndPlan({ removePlan: false })
          if (closed && [reducer.PHASE.FAILED, reducer.PHASE.RESIDUE].includes(this.current?.phase)) {
            this.releaseSupervisorLease()
          }
          return false
        }
        if (workerEvent.event_type !== 'service_stopped') {
          this.cleanupFailure(mode, serviceId)
          const closed = await this.closeClientAndPlan({ removePlan: false })
          if (closed && [reducer.PHASE.FAILED, reducer.PHASE.RESIDUE].includes(this.current?.phase)) {
            this.releaseSupervisorLease()
          }
          return false
        }
        if (index === candidates.length - 1) {
          held = workerEvent
          heldServiceId = serviceId
        } else {
          this.apply(workerEvent.event_type, serviceId, { dispatch_id: workerEvent.dispatch_id })
        }
      }
      const cleanupClear = await this.closeClientAndPlan()
      if (!cleanupClear) {
        const serviceId = heldServiceId || candidates[0]
        if (serviceId) this.cleanupFailure(mode, serviceId)
        return false
      }
      if (held) this.apply(held.event_type, held.service_id, { dispatch_id: held.dispatch_id })
      return true
    } catch {
      const closed = await this.closeClientAndPlan({ removePlan: false })
      const serviceId = heldServiceId || candidates[0]
      if (serviceId && this.current && this.current.phase !== reducer.PHASE.RESIDUE) {
        try { this.cleanupFailure(mode, serviceId) } catch {}
      }
      if (closed && [reducer.PHASE.FAILED, reducer.PHASE.RESIDUE].includes(this.current?.phase)) {
        this.releaseSupervisorLease()
      }
      return false
    }
  }

  async rollback (compiled) {
    if (this.current?.phase !== reducer.PHASE.ROLLING_BACK) return
    const retainedCompiled = this.compiled || compiled
    this.apply('rollback_started')
    const clear = await this.stopOwnedServices('rollback', retainedCompiled)
    if (clear && this.current.phase === reducer.PHASE.ROLLING_BACK) this.apply('rollback_completed')
    if ([reducer.PHASE.FAILED, reducer.PHASE.RESIDUE].includes(this.current?.phase)) this.releaseSupervisorLease()
  }

  async recover (compiled) {
    if (!this.current || !ACTIVE_PHASES.has(this.current.phase)) return
    const retainedCompiled = this.compiled || compiled
    if (this.current.phase !== reducer.PHASE.RECOVERING) this.apply('supervisor_crashed')
    if (this.current.phase !== reducer.PHASE.RECOVERING) return
    if (!(await this.closeClientAndPlan({ removePlan: false }))) return false
    this.apply('recovery_started')
    const clear = await this.stopOwnedServices('recovery', retainedCompiled)
    if (clear && this.current.phase === reducer.PHASE.RECOVERING) this.apply('recovery_completed')
    if ([reducer.PHASE.FAILED, reducer.PHASE.RESIDUE].includes(this.current?.phase)) this.releaseSupervisorLease()
    return clear
  }

  async start ({ profileId, options, configIdentity }) {
    let validatedConfigIdentity
    try {
      reducer.validateConfigIdentity(configIdentity, this.authority)
      validatedConfigIdentity = deriveEffectiveConfigIdentity({
        profileId,
        options,
        authority: this.authority
      })
      if (!sameConfigIdentity(configIdentity, validatedConfigIdentity)) {
        throw new LauncherPrivatePlanError('private_plan_config_invalid')
      }
    } catch (error) {
      return publicResult({
        ok: false,
        resultClass: 'preflight_failed',
        operation: null,
        profileId,
        errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
      })
    }
    if (this.inflight) {
      let current
      try {
        current = this.readAuthoritativeJoinOperation(this.current, validatedConfigIdentity)
      } catch (error) {
        return publicResult({
          ok: false,
          resultClass: 'preflight_failed',
          operation: null,
          profileId,
          errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
        })
      }
      return publicResult({
        ok: this.inflight === 'start',
        resultClass: this.inflight === 'start' ? 'joined_existing' : 'operation_in_progress',
        operation: current,
        profileId
      })
    }
    this.inflight = 'start'
    this.profileId = profileId
    try {
      if (this.client && this.supervisorLease && this.current?.phase === reducer.PHASE.READY) {
        let current
        try {
          current = this.readAuthoritativeJoinOperation(this.current, validatedConfigIdentity)
        } catch (error) {
          return publicResult({
            ok: false,
            resultClass: 'preflight_failed',
            operation: null,
            profileId,
            errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
          })
        }
        return publicResult({ ok: true, resultClass: 'joined_existing', operation: current, profileId })
      }
      let compiled
      let compiledPlanIdentity
      try {
        compiled = this.compile(profileId, options, validatedConfigIdentity)
        compiledPlanIdentity = planIdentityOf(compiled)
        this.ensureProbeExecutor(compiled)
      } catch (error) {
        if (this.generatedProbeExecutor) {
          this.probeExecutor = null
          this.generatedProbeExecutor = false
        }
        return publicResult({
          ok: false,
          resultClass: 'preflight_failed',
          operation: null,
          profileId,
          errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
        })
      }

      let decision = this.store.startAndPersist(
        this.operationIdFactory(),
        validatedConfigIdentity,
        compiledPlanIdentity,
        this.probeExecutor.configSha256,
        this.authority,
        this.privateRuntimeRoot
      )
      this.current = decision.operation
      this.bindSupervisorLease(decision.supervisorLease)
      if (decision.joined_existing && this.client) {
        return publicResult({
          ok: true,
          resultClass: 'joined_existing',
          operation: this.current,
          profileId
        })
      }
      if (decision.joined_existing) {
        let persistedPlan
        try {
          persistedPlan = this.readPersistedPlan(this.current)
        } catch (error) {
          if (this.generatedProbeExecutor) {
            this.probeExecutor = null
            this.generatedProbeExecutor = false
          }
          this.releaseSupervisorLease()
          return publicResult({
            ok: false,
            resultClass: 'preflight_failed',
            operation: this.current,
            profileId,
            errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
          })
        }
        if (this.generatedProbeExecutor) {
          this.probeExecutor = null
          this.generatedProbeExecutor = false
        }
        if (!(await this.recover(persistedPlan))) {
          return publicResult({
            ok: false,
            resultClass: resultClassFor(this.current),
            operation: this.current,
            profileId
          })
        }
        this.ensureProbeExecutor(compiled)
        decision = this.store.startAndPersist(
          this.operationIdFactory(),
          validatedConfigIdentity,
          compiledPlanIdentity,
          this.probeExecutor.configSha256,
          this.authority,
          this.privateRuntimeRoot
        )
        this.current = decision.operation
        this.bindSupervisorLease(decision.supervisorLease)
        if (decision.joined_existing) {
          return publicResult({
            ok: false,
            resultClass: 'operation_in_progress',
            operation: this.current,
            profileId
          })
        }
      }

      this.apply('preflight_started')
      this.apply('preflight_passed')
      try {
        this.ensureClient(compiled)
      } catch {
        await this.recover(compiled)
        return publicResult({
          ok: false,
          resultClass: resultClassFor(this.current),
          operation: this.current,
          profileId
        })
      }
      this.apply('start_requested')

      const included = new Set(compiled.included_service_ids)
      for (const serviceId of this.authority.bindingDocument.binding.service_order) {
        const spec = this.authority.graph.services.find((service) => service.service_id === serviceId)
        if (spec.ownership === 'external' && options.SkipVoicevoxCheck) {
          const dispatchId = this.dispatchIdFactory()
          this.apply('probe_requested', serviceId, { dispatch_id: dispatchId, action: 'probe' })
          this.apply('optional_absent', serviceId, { dispatch_id: dispatchId })
        } else if (spec.ownership === 'external') {
          try {
            const workerEvent = await this.exchange(serviceId, 'probe')
            this.applyPersistedEvent(workerEvent.event_type, serviceId, { dispatch_id: workerEvent.dispatch_id })
            if (workerEvent.event_type === 'probe_transport_ready') {
              await this.completeSemanticProbe(serviceId, workerEvent.dispatch_id)
            }
          } catch (error) {
            if (error instanceof LauncherJobWorkerError && error.code === 'worker_transport_timeout') {
              const dispatchId = this.pendingDispatchId(serviceId, 'probe')
              this.apply('readiness_timeout', serviceId, dispatchId ? { dispatch_id: dispatchId } : {})
            } else this.apply('supervisor_crashed', null, {
              responsible_id: serviceLoopFailureResponsibleId(error, serviceId)
            })
          }
        } else if (spec.requirement === 'optional' && !included.has(serviceId)) {
          const dispatchId = this.dispatchIdFactory()
          this.apply('probe_requested', serviceId, { dispatch_id: dispatchId, action: 'probe' })
          this.apply('optional_absent', serviceId, { dispatch_id: dispatchId })
        } else {
          try {
            const startEvent = await this.exchange(serviceId, 'start')
            this.applyPersistedEvent(startEvent.event_type, serviceId, { dispatch_id: startEvent.dispatch_id })
            if (this.current.phase === reducer.PHASE.ROLLING_BACK) break
            const probeEvent = await this.exchange(serviceId, 'probe')
            this.applyPersistedEvent(probeEvent.event_type, serviceId, { dispatch_id: probeEvent.dispatch_id })
            if (probeEvent.event_type === 'probe_transport_ready') {
              await this.completeSemanticProbe(serviceId, probeEvent.dispatch_id)
            }
          } catch (error) {
            if (error instanceof LauncherJobWorkerError && error.code === 'worker_transport_timeout') {
              const dispatchId = this.pendingDispatchId(serviceId, 'probe')
              this.apply('readiness_timeout', serviceId, dispatchId ? { dispatch_id: dispatchId } : {})
            } else this.apply('supervisor_crashed', null, {
              responsible_id: serviceLoopFailureResponsibleId(error, serviceId)
            })
          }
        }
        if ([reducer.PHASE.ROLLING_BACK, reducer.PHASE.RECOVERING, reducer.PHASE.RESIDUE, reducer.PHASE.FAILED].includes(this.current.phase)) break
      }

      if (this.current.phase === reducer.PHASE.ROLLING_BACK) await this.rollback(compiled)
      if (this.current.phase === reducer.PHASE.RECOVERING) await this.recover(compiled)
      const resultClass = resultClassFor(this.current)
      return publicResult({
        ok: resultClass === 'ready',
        resultClass,
        operation: this.current,
        profileId
      })
    } catch {
      if (this.current && ACTIVE_PHASES.has(this.current.phase)) {
        try {
          const compiled = this.compiled
          if (compiled) await this.recover(compiled)
        } catch {}
      }
      return publicResult({
        ok: false,
        resultClass: resultClassFor(this.current),
        operation: this.current,
        profileId,
        errorClass: 'supervisor_runtime_failed'
      })
    } finally {
      this.inflight = null
    }
  }

  async stop ({ profileId }) {
    if (this.inflight) {
      return publicResult({
        ok: false,
        resultClass: 'operation_in_progress',
        operation: this.current,
        profileId
      })
    }
    this.inflight = 'stop'
    this.profileId = profileId
    try {
      const current = this.readCurrent()
      if (!current) {
        return publicResult({
          ok: true,
          resultClass: 'already_stopped',
          operation: null,
          profileId
        })
      }
      if (profileId !== current.profile_id) {
        return publicResult({
          ok: false,
          resultClass: 'preflight_failed',
          operation: current,
          profileId,
          errorClass: 'private_plan_invalid'
        })
      }
      if (current.phase === reducer.PHASE.STOPPED) {
        const closed = await this.closeClientAndPlan()
        if (closed) this.releaseSupervisorLease()
        return publicResult({
          ok: true,
          resultClass: 'already_stopped',
          operation: current,
          profileId
        })
      }
      if (reducer.isClearTerminalFailure(current, this.authority)) {
        if (!this.supervisorLease) this.acquireExistingSupervisorLease()
        this.apply('stop_requested')
        const released = this.current.phase === reducer.PHASE.STOPPED
          ? this.releaseSupervisorLease()
          : false
        return publicResult({
          ok: released && this.current.phase === reducer.PHASE.STOPPED,
          resultClass: resultClassFor(this.current),
          operation: this.current,
          profileId
        })
      }
      const acquiredSupervisorLease = !this.supervisorLease
      if (acquiredSupervisorLease) this.acquireExistingSupervisorLease()
      let compiled = this.compiled
      if (!compiled) {
        try {
          compiled = this.readPersistedPlan(this.current)
        } catch (error) {
          if (acquiredSupervisorLease) this.releaseSupervisorLease()
          return publicResult({
            ok: false,
            resultClass: 'preflight_failed',
            operation: this.current,
            profileId,
            errorClass: error instanceof LauncherPrivatePlanError ? 'private_plan_invalid' : 'supervisor_runtime_failed'
          })
        }
      }
      this.apply('stop_requested')
      const clear = await this.stopOwnedServices('stop', compiled)
      const released = clear && this.current.phase === reducer.PHASE.STOPPED
        ? this.releaseSupervisorLease()
        : false
      return publicResult({
        ok: clear && released && this.current.phase === reducer.PHASE.STOPPED,
        resultClass: resultClassFor(this.current),
        operation: this.current,
        profileId
      })
    } catch {
      return publicResult({
        ok: false,
        resultClass: resultClassFor(this.current),
        operation: this.current,
        profileId,
        errorClass: 'supervisor_runtime_failed'
      })
    } finally {
      this.inflight = null
    }
  }
}

module.exports = {
  LauncherSupervisorRuntime,
  publicOperation,
  publicResult,
  resultClassFor
}
