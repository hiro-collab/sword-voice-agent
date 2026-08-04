'use strict'

const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  LauncherContractError,
  loadAuthority
} = require('../tools/home-control-launcher/launcher-supervisor-contract')
const realStore = require('../tools/home-control-launcher/launcher-operation-store')
const reducer = require('../tools/home-control-launcher/launcher-supervisor-reducer')
const { LauncherJobWorkerError } = require('../tools/home-control-launcher/launcher-job-worker-client')
const {
  LauncherPrivatePlanError,
  compilePrivateServicePlan,
  deriveEffectiveConfigIdentity,
  expectedEventJournalDirectory,
  readPrivateServicePlan,
  serializePrivateServicePlan,
  verifyTrustedWindowsWorkerExecutable,
  writePrivateServicePlan
} = require('../tools/home-control-launcher/launcher-private-service-plan')
const { LauncherProbeExecutorError } = require('../tools/home-control-launcher/launcher-probe-executor')
const {
  LauncherSupervisorRuntime,
  publicOperation
} = require('../tools/home-control-launcher/launcher-supervisor-runtime')

const ROOT = path.resolve(__dirname, '..')
const authority = loadAuthority(ROOT)
const requiredOwnedIds = authority.graph.services
  .filter((service) => service.ownership === 'owned' && service.requirement === 'required')
  .map((service) => service.service_id)
const allOwnedIds = authority.graph.services
  .filter((service) => service.ownership === 'owned')
  .map((service) => service.service_id)
const PLAN_IDENTITY = Object.freeze({
  private_plan_sha256: '1'.repeat(64),
  worker_executable_class: 'powershell_7_program_files',
  worker_executable_sha256: '2'.repeat(64)
})
const PROBE_CONFIG_SHA256 = '0'.repeat(64)
const simulatedWindowsReparseIo = ({ filePath, reparseName, onWorkerRead }) => {
  const target = path.win32.normalize(filePath)
  const parsed = path.win32.parse(target)
  const segments = target.slice(parsed.root.length).split(/[\\/]+/u).filter(Boolean)
  const key = (value) => path.win32.normalize(value).toLowerCase()
  const parentIndex = new Map()
  for (let index = 0; index < segments.length; index += 1) {
    parentIndex.set(key(path.win32.join(parsed.root, ...segments.slice(0, index))), index)
  }
  return {
    existsSync: () => true,
    lstatSync: (candidate) => {
      const leaf = key(candidate) === key(target)
      return { isFile: () => leaf, isDirectory: () => !leaf, isSymbolicLink: () => false }
    },
    readdirSync: (parent) => {
      const index = parentIndex.get(key(parent))
      if (index === undefined) throw new Error('PRIVATE_REPARSE_PARENT')
      const name = segments[index]
      const leaf = index === segments.length - 1
      return [{
        name,
        isFile: () => leaf,
        isDirectory: () => !leaf,
        isSymbolicLink: () => name.toLowerCase() === reparseName.toLowerCase()
      }]
    },
    realpathSync: (candidate) => candidate,
    readFileSync: () => {
      onWorkerRead()
      return Buffer.from('PRIVATE_WORKER_BYTES')
    }
  }
}

const resultFor = (request, values = {}) => {
  const defaults = request.action === 'start'
    ? {
        result_class: 'accepted',
        ownership_class: 'matched',
        listener_class: 'not_applicable',
        descendant_class: 'owned_active'
      }
    : request.action === 'probe' && request.service_id === 'voicevox'
      ? {
          result_class: 'external_ready',
          ownership_class: 'not_applicable',
          listener_class: 'matched',
          descendant_class: 'not_applicable'
        }
      : request.action === 'probe'
        ? {
            result_class: 'ready',
            ownership_class: 'matched',
            listener_class: 'matched',
            descendant_class: 'owned_active'
          }
        : {
            result_class: 'stopped',
            ownership_class: 'matched',
            listener_class: 'not_applicable',
            descendant_class: 'owned_clear'
          }
  return {
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
    ...defaults,
    ...values
  }
}

const successfulSemanticProbeResult = (expected) => {
  const descriptor = authority.probeDocument.descriptors.find((candidate) => (
    candidate.service_id === expected.service_id && candidate.probe_id === expected.probe_id
  ))
  return {
    schema_version: 'launcher_probe_result.v1',
    message_type: 'result',
    ...expected,
    observed_at: expected.requested_at,
    source_observed_at: expected.requested_at,
    freshness_class: 'fresh',
    semantic_class: descriptor.success_semantic_classes[0],
    reason_class: 'none',
    ready: true,
    proof_ceiling: descriptor.proof_ceiling
  }
}

class FakeWorker {
  constructor ({ responses = {}, events = [], onExecute = null, onClose = null }) {
    this.responses = responses
    this.events = events
    this.onExecute = onExecute
    this.onClose = onClose
    this.requests = []
  }

  async execute (request) {
    this.requests.push(request)
    this.events.push(`exchange:${request.service_id}:${request.action}`)
    if (this.onExecute) await this.onExecute(request)
    const configured = this.responses[`${request.service_id}:${request.action}`]
    if (configured instanceof Error) throw configured
    if (typeof configured === 'function') return configured(request)
    return resultFor(request, configured || {})
  }

  async close () {
    this.events.push('worker:close')
    if (this.onClose) await this.onClose()
  }
}

const makeHarness = ({
  includedServiceIds = allOwnedIds,
  configIdentity = CONFIG_IDENTITY,
  workerBuilders = [],
  responses = {},
  operationPrefix = 'runtime',
  planCompiler = null,
  planReader = null,
  planRemover = null,
  probeExecutor = undefined,
  probeExecutorFactory = null,
  storeOverrides = {}
} = {}) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-runtime-'))
  const events = []
  const workers = []
  let planPresent = false
  let operationSequence = 0
  let nonceSequence = 0
  let dispatchSequence = 0
  const compiled = {
    document: {
      schema_version: 'launcher_private_service_plans.v1',
      graph_sha256: authority.identities.graphSha256,
      binding_sha256: authority.identities.bindingSha256,
      profile_id: configIdentity.profile_id,
      effective_config_sha256: configIdentity.effective_config_sha256,
      camera_policy: configIdentity.camera_policy,
      worker_file_path: process.execPath,
      services: [{
        service_id: 'thought_core_api',
        environment: {
          THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: '1',
          THOUGHT_CORE_EVENT_JOURNAL_ENABLED: '1',
          THOUGHT_CORE_EVENT_JOURNAL_DIR: expectedEventJournalDirectory(root)
        }
      }]
    },
    powershell_path: process.execPath,
    ...PLAN_IDENTITY,
    included_service_ids: [...includedServiceIds]
  }
  const store = {
    ...realStore,
    startAndPersist (...args) {
      events.push('store:start')
      return realStore.startAndPersist(...args)
    },
    reduceAndPersist (current, event, ...args) {
      events.push(`store:${event.event_type}${event.service_id ? `:${event.service_id}` : ''}`)
      return realStore.reduceAndPersist(current, event, ...args)
    },
    readOperation: (...args) => realStore.readOperation(...args),
    releaseSupervisorLease (...args) {
      events.push('store:lease:release')
      return realStore.releaseSupervisorLease(...args)
    },
    ...storeOverrides
  }
  const createRuntime = () => new LauncherSupervisorRuntime({
    repositoryRoot: ROOT,
    workspaceRoot: ROOT,
    privateRuntimeRoot: root,
    authority,
    store,
    planCompiler: planCompiler || (() => {
      events.push('plan:compile')
      return compiled
    }),
    planReader: planReader || (() => {
      events.push('plan:read')
      if (!planPresent) throw new LauncherPrivatePlanError('private_plan_config_invalid')
      return Object.freeze({ ...compiled, plan_path: __filename })
    }),
    planWriter: () => {
      events.push('plan:write')
      planPresent = true
      return __filename
    },
    planRemover: () => {
      if (planRemover) planRemover()
      else events.push('plan:remove')
      planPresent = false
    },
    workerFactory: ({ supervisorLease }) => {
      events.push('worker:create')
      assert.ok(supervisorLease)
      const builder = workerBuilders[workers.length]
      const worker = builder
        ? builder({ events, root, store })
        : new FakeWorker({ responses, events })
      workers.push(worker)
      return worker
    },
    ...(probeExecutorFactory
      ? { probeExecutorFactory }
      : {
          probeExecutor: probeExecutor === undefined ? {
            configSha256: '0'.repeat(64),
            async execute (expected) {
              events.push(`probe:execute:${expected.service_id}`)
              assert.equal(expected.config_sha256, '0'.repeat(64))
              return successfulSemanticProbeResult(expected)
            }
          } : probeExecutor
        }),
    operationIdFactory: () => {
      operationSequence += 1
      return `lop_${operationPrefix}${String(operationSequence).padStart(8, '0')}`
    },
    workerNonceFactory: () => {
      nonceSequence += 1
      return `lw_${operationPrefix}${String(nonceSequence).padStart(16, '0')}`
    },
    dispatchIdFactory: () => {
      dispatchSequence += 1
      return `ld_${String(dispatchSequence).padStart(16, '0')}`
    }
  })
  const runtime = createRuntime()
  const startWithIdentity = runtime.start.bind(runtime)
  runtime.start = (request) => startWithIdentity({
    ...request,
    configIdentity: request?.configIdentity || configIdentity
  })
  return {
    root,
    runtime,
    createRuntime,
    compiled,
    events,
    workers,
    cleanup: () => fs.rmSync(root, { recursive: true, force: true })
  }
}

const canonicalOptions = {
  HomeAssistantBridgePort: 8787,
  HomeAssistantBridgeHost: '127.0.0.1',
  EnvironmentStatePort: 8790,
  MediapipePort: 8765,
  MediapipeBrowserMonitorPort: 8770,
  VisionSnapshotProcessorPort: 8776,
  AituberPort: 3000,
  AituberHost: '127.0.0.1',
  TouchDesignerGuiPort: 8788,
  TouchDesignerGuiHost: '127.0.0.1',
  ThoughtCoreHost: '127.0.0.1',
  ThoughtCorePort: 18787,
  OpenAIBrokerPort: 18786,
  ThoughtCoreLlmProvider: 'sword-openai-broker',
  VoicevoxUrl: 'http://127.0.0.1:50021',
  VoicevoxReadyTimeoutSeconds: 45,
  MediapipeReadyTimeoutSeconds: 90,
  SkipHomeAssistantBridge: false,
  SkipEnvironmentState: false,
  SkipMediapipe: false,
  SkipVisionSnapshotProcessor: false,
  SkipAituber: false,
  SkipTouchDesignerGui: false,
  EnableThoughtCore: true,
  EnableThoughtCoreWatch: true,
  SkipVoicevoxCheck: false,
  MediapipeMode: 'mediamtx'
}
const exact3Options = {
  ...canonicalOptions,
  VoicevoxUrl: 'configuration-excluded',
  SkipHomeAssistantBridge: true,
  SkipEnvironmentState: true,
  SkipMediapipe: true,
  SkipVisionSnapshotProcessor: true,
  SkipTouchDesignerGui: true,
  EnableThoughtCoreWatch: false,
  SkipVoicevoxCheck: true
}
const configIdentityFor = (options) => deriveEffectiveConfigIdentity({
  profileId: authority.graph.profile_id,
  options,
  authority
})
const CONFIG_IDENTITY = configIdentityFor(canonicalOptions)

test('preflight is persisted before exchange; Node orders, finalizes, and keeps public state private', async () => {
  let harness
  const closePhases = []
  harness = makeHarness({
    workerBuilders: [({ events }) => new FakeWorker({
      events,
      onExecute: () => {
        const operation = realStore.readOperation(authority, harness.root)
        assert.notEqual(operation.phase, 'planned')
        assert.notEqual(operation.phase, 'preflight')
        assert.ok(operation.services.some((service) => service.pending_dispatch_id !== null))
      },
      onClose: () => {
        closePhases.push(realStore.readOperation(authority, harness.root).phase)
      }
    })]
  })
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.ok, true)
    assert.equal(started.result_class, 'ready')
    assert.equal(started.error_class, 'none')
    assert.equal(started.operation.phase, 'ready')
    const firstExchange = harness.events.findIndex((event) => event.startsWith('exchange:'))
    assert.ok(harness.events.indexOf('plan:compile') < harness.events.indexOf('store:start'))
    assert.ok(harness.events.indexOf('store:preflight_passed') < firstExchange)
    assert.ok(harness.events.indexOf('plan:write') < firstExchange)
    assert.ok(harness.events.indexOf('store:spawn_requested:aituber_kit') < firstExchange)
    const externalRequests = harness.workers[0].requests.filter((request) => request.service_id === 'voicevox')
    assert.deepEqual(externalRequests.map((request) => request.action), ['probe'])

    const serialized = JSON.stringify(started)
    for (const forbidden of ['file_path', 'working_directory', '"environment":', '"arguments":', 'command_line', '"pid"']) {
      assert.equal(serialized.includes(forbidden), false, forbidden)
    }

    const stopped = await harness.runtime.stop({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.result_class, 'stopped')
    assert.equal(stopped.operation.cleanup, 'clear')
    assert.deepEqual(closePhases, ['stopping'])
    assert.equal(realStore.readOperation(authority, harness.root).phase, 'stopped')
    assert.ok(harness.events.indexOf('worker:close') < harness.events.indexOf('store:lease:release'))
    assert.equal(harness.runtime.supervisorLease, null)
    assert.deepEqual(
      harness.workers[0].requests.filter((request) => request.service_id === 'voicevox').map((request) => request.action),
      ['probe']
    )
  } finally {
    harness.cleanup()
  }
})

test('Stop rejects a different profile before cleanup mutation', async () => {
  const harness = makeHarness()
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.result_class, 'ready')
    const requestCount = harness.workers[0].requests.length
    const mismatched = await harness.runtime.stop({ profileId: 'camera-debug', options: canonicalOptions })
    assert.equal(mismatched.ok, false)
    assert.equal(mismatched.result_class, 'preflight_failed')
    assert.equal(mismatched.error_class, 'private_plan_invalid')
    assert.equal(harness.workers[0].requests.length, requestCount)
    const stopped = await harness.runtime.stop({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.result_class, 'stopped')
  } finally {
    harness.cleanup()
  }
})

test('a fresh runtime stops from the immutable start plan without recompiling drifted options', async () => {
  const harness = makeHarness()
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.result_class, 'ready')
    assert.equal(harness.events.filter((event) => event === 'plan:compile').length, 1)

    // Model the old Launcher owner exiting: the operation and private plan remain,
    // while the process-local client, compiled plan and supervisor lease do not.
    assert.equal(harness.runtime.releaseSupervisorLease(), true)
    const replacement = harness.createRuntime()

    const stopped = await replacement.stop({ profileId: 'thought-core-v0' })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.result_class, 'stopped')
    assert.equal(stopped.operation.cleanup, 'clear')
    assert.equal(harness.events.filter((event) => event === 'plan:compile').length, 1)
    assert.equal(harness.events.filter((event) => event === 'plan:read').length, 1)
    assert.equal(harness.events.filter((event) => event === 'plan:write').length, 1)
    assert.equal(harness.workers.length, 2)
    assert.ok(harness.workers[1].requests.some((request) => request.action === 'stop'))
    assert.equal(replacement.supervisorLease, null)
    for (const privateMarker of ['file_path', 'working_directory', '"arguments":', '"environment":']) {
      assert.equal(JSON.stringify(stopped).includes(privateMarker), false)
    }
  } finally {
    harness.cleanup()
  }
})

test('fresh Start recovers with the joined operation plan identity before publishing replacement identity', async () => {
  let harness
  let compileCalls = 0
  const replacementIdentity = Object.freeze({
    private_plan_sha256: '3'.repeat(64),
    worker_executable_class: 'windows_powershell_system32',
    worker_executable_sha256: '4'.repeat(64)
  })
  const recoveredIdentities = []
  harness = makeHarness({
    operationPrefix: 'planidentity',
    planCompiler: () => {
      compileCalls += 1
      return compileCalls === 1
        ? harness.compiled
        : Object.freeze({ ...harness.compiled, ...replacementIdentity })
    },
    planReader: ({ planIdentity }) => {
      recoveredIdentities.push(planIdentity)
      return Object.freeze({ ...harness.compiled, plan_path: __filename })
    }
  })
  try {
    const first = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(first.result_class, 'ready')
    assert.equal(harness.runtime.releaseSupervisorLease(), true)

    const replacement = harness.createRuntime()
    const restarted = await replacement.start({
      profileId: 'thought-core-v0',
      options: canonicalOptions,
      configIdentity: CONFIG_IDENTITY
    })
    assert.equal(restarted.result_class, 'ready')
    assert.deepEqual(recoveredIdentities, [PLAN_IDENTITY])
    assert.equal(replacement.current.private_plan_sha256, replacementIdentity.private_plan_sha256)
    assert.equal(replacement.current.worker_executable_class, replacementIdentity.worker_executable_class)
    assert.equal(replacement.current.worker_executable_sha256, replacementIdentity.worker_executable_sha256)
    assert.equal(harness.workers.length, 3)
  } finally {
    harness.cleanup()
  }
})

test('fresh-runtime Stop releases its new lease and makes no cleanup mutation when the plan is unavailable', async () => {
  const harness = makeHarness()
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.result_class, 'ready')
    assert.equal(harness.runtime.releaseSupervisorLease(), true)
    harness.runtime.planRemover(harness.root)
    const replacement = harness.createRuntime()
    const requestCount = harness.workers[0].requests.length

    const stopped = await replacement.stop({ profileId: 'thought-core-v0' })
    assert.equal(stopped.ok, false)
    assert.equal(stopped.result_class, 'preflight_failed')
    assert.equal(stopped.error_class, 'private_plan_invalid')
    assert.equal(harness.workers.length, 1)
    assert.equal(harness.workers[0].requests.length, requestCount)
    assert.equal(stopped.operation.phase, 'ready')
    assert.equal(replacement.supervisorLease, null)
    assert.ok(harness.events.filter((event) => event === 'store:lease:release').length >= 2)
  } finally {
    harness.cleanup()
  }
})

test('fresh-runtime Stop terminalizes clear failure without a plan worker or owned action', async () => {
  const harness = makeHarness({
    operationPrefix: 'clearfailure',
    workerBuilders: [
      ({ events }) => new FakeWorker({
        events,
        responses: {
          'thought_core_watcher:start': {
            result_class: 'listener_mismatch',
            ownership_class: 'mismatch',
            listener_class: 'mismatch',
            descendant_class: 'foreign'
          }
        }
      }),
      ({ events }) => new FakeWorker({ events })
    ]
  })
  try {
    const failed = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(failed.result_class, 'failed')
    assert.equal(failed.operation.cleanup, 'clear')
    assert.deepEqual(failed.operation.residue_service_ids, [])
    assert.equal(harness.runtime.supervisorLease, null)
    const failedStored = realStore.readOperation(authority, harness.root)
    assert.equal(failedStored.services.some((service) =>
      service.pending_action === null && service.probe_status === 'ready' && service.last_probe_result !== null), true)
    const eventCount = harness.events.length
    const workerCount = harness.workers.length
    const requestCount = harness.workers.reduce((count, worker) => count + worker.requests.length, 0)

    const replacement = harness.createRuntime()
    const stopped = await replacement.stop({ profileId: 'thought-core-v0' })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.result_class, 'stopped')
    assert.equal(stopped.operation.cleanup, 'clear')
    assert.equal(stopped.operation.operation_id, failed.operation.operation_id)
    assert.equal(Object.hasOwn(stopped.operation, 'supervisor_generation'), false)
    assert.equal(harness.events.slice(eventCount).includes('plan:read'), false)
    assert.equal(harness.events.slice(eventCount).includes('worker:create'), false)
    assert.equal(harness.workers.length, workerCount)
    assert.equal(harness.workers.reduce((count, worker) => count + worker.requests.length, 0), requestCount)
    assert.equal(replacement.supervisorLease, null)
    const stoppedStored = realStore.readOperation(authority, harness.root)
    assert.equal(stoppedStored.phase, 'stopped')
    assert.equal(stoppedStored.cleanup, 'clear')
    assert.equal(stoppedStored.supervisor_generation, failedStored.supervisor_generation)
    assert.equal(
      stopped.operation.services.find((service) => service.service_id === 'voicevox').state,
      failed.operation.services.find((service) => service.service_id === 'voicevox').state
    )

    const changedOptions = {
      ...canonicalOptions,
      SkipMediapipe: true,
      SkipVisionSnapshotProcessor: true
    }
    const changedIdentity = configIdentityFor(changedOptions)
    const planCompileCount = harness.events.filter((event) => event === 'plan:compile').length
    const planWriteCount = harness.events.filter((event) => event === 'plan:write').length
    const restarted = await replacement.start({
      profileId: 'thought-core-v0',
      options: changedOptions,
      configIdentity: changedIdentity
    })
    assert.equal(restarted.result_class, 'ready')
    assert.equal(restarted.operation.joined_existing, false)
    assert.notEqual(restarted.operation.operation_id, stopped.operation.operation_id)
    assert.equal(harness.events.filter((event) => event === 'plan:compile').length, planCompileCount + 1)
    assert.equal(harness.events.filter((event) => event === 'plan:write').length, planWriteCount + 1)
    const restartedStored = realStore.readOperation(authority, harness.root)
    assert.equal(restartedStored.supervisor_generation, stoppedStored.supervisor_generation + 1)
    assert.equal(restarted.operation.effective_config_sha256, changedIdentity.effective_config_sha256)
  } finally {
    harness.cleanup()
  }
})

test('fresh-runtime Stop rejects forged clear failure with correlated probe work before any action', async () => {
  const harness = makeHarness({
    operationPrefix: 'forgedclear',
    responses: {
      'home_assistant_bridge:start': {
        result_class: 'listener_mismatch',
        ownership_class: 'mismatch',
        listener_class: 'mismatch',
        descendant_class: 'foreign'
      }
    }
  })
  try {
    const failed = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(failed.operation.phase, 'failed')
    assert.equal(failed.operation.cleanup, 'clear')
    const stored = realStore.readOperation(authority, harness.root)
    const forged = {
      ...stored,
      services: stored.services.map((service) => service.service_id === 'home_assistant_bridge'
        ? {
            ...service,
            pending_action: 'probe',
            pending_dispatch_id: `ld_${'f'.repeat(16)}`,
            probe_status: 'pending',
            probe_expected_revision: stored.revision
          }
        : { ...service })
    }
    assert.doesNotThrow(() => reducer.validateSnapshot(forged, authority))
    assert.equal(reducer.isClearTerminalFailure(forged, authority), false)
    const recordPath = path.join(harness.root, realStore.STORE_DIRECTORY, realStore.RECORD_FILE)
    fs.writeFileSync(recordPath, `${JSON.stringify(forged)}\n`, 'utf8')
    const requestCount = harness.workers.reduce((count, worker) => count + worker.requests.length, 0)
    const workerCount = harness.workers.length
    const replacement = harness.createRuntime()

    const stopped = await replacement.stop({ profileId: 'thought-core-v0' })
    assert.equal(stopped.ok, false)
    assert.equal(stopped.result_class, 'preflight_failed')
    assert.equal(stopped.error_class, 'private_plan_invalid')
    assert.equal(stopped.operation.phase, 'failed')
    assert.equal(stopped.operation.revision, forged.revision)
    assert.equal(harness.workers.length, workerCount)
    assert.equal(harness.workers.reduce((count, worker) => count + worker.requests.length, 0), requestCount)
    assert.equal(replacement.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('fresh-runtime Stop keeps residue fail-closed when its private plan is missing', async () => {
  let planRemoveCalls = 0
  const harness = makeHarness({
    operationPrefix: 'residuemissing',
    workerBuilders: [
      ({ events }) => new FakeWorker({
        events,
        responses: {
          'aituber_kit:start': new LauncherJobWorkerError('worker_transport_failed')
        }
      })
    ],
    planRemover: () => {
      planRemoveCalls += 1
      if (planRemoveCalls === 2) throw new Error('private_plan_remove_failed')
    }
  })
  try {
    const residue = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(residue.operation.phase, 'residue')
    assert.notEqual(residue.operation.cleanup, 'clear')
    harness.runtime.planRemover(harness.root)
    const replacement = harness.createRuntime()
    const revision = residue.operation.revision
    const workerCount = harness.workers.length

    const stopped = await replacement.stop({ profileId: 'thought-core-v0' })
    assert.equal(stopped.ok, false)
    assert.equal(stopped.result_class, 'preflight_failed')
    assert.equal(stopped.error_class, 'private_plan_invalid')
    assert.equal(stopped.operation.phase, 'residue')
    assert.equal(stopped.operation.revision, revision)
    assert.equal(harness.workers.length, workerCount)
    assert.equal(replacement.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('probe executor factory binds the single compiled plan before store side effects', async () => {
  const calls = []
  const harness = makeHarness({
    probeExecutorFactory: ({ compiled, authority: validatedAuthority }) => {
      calls.push({ compiled, validatedAuthority })
      return {
        configSha256: '1'.repeat(64),
        async execute (expected) {
          assert.equal(expected.config_sha256, '1'.repeat(64))
          return successfulSemanticProbeResult(expected)
        }
      }
    }
  })
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.result_class, 'ready')
    assert.equal(calls.length, 1)
    assert.equal(calls[0].validatedAuthority, authority)
    assert.equal(calls[0].compiled.document.schema_version, 'launcher_private_service_plans.v1')
    assert.ok(harness.events.indexOf('plan:compile') < harness.events.indexOf('store:start'))
    assert.ok(harness.runtime.probeExecutor)
    const stopped = await harness.runtime.stop({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(stopped.result_class, 'stopped')
    assert.equal(harness.runtime.probeExecutor, null)
  } finally {
    harness.cleanup()
  }
})

test('fresh Start rebinds a factory probe executor after recovery before replacement publish', async () => {
  const calls = []
  const harness = makeHarness({
    operationPrefix: 'probefactoryrecovery',
    probeExecutorFactory: ({ compiled, authority: validatedAuthority }) => {
      calls.push({ compiled, validatedAuthority })
      return {
        configSha256: '1'.repeat(64),
        async execute (expected) {
          assert.equal(expected.config_sha256, '1'.repeat(64))
          return successfulSemanticProbeResult(expected)
        }
      }
    }
  })
  try {
    const first = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(first.result_class, 'ready')
    assert.equal(harness.runtime.releaseSupervisorLease(), true)

    const replacement = harness.createRuntime()
    const restarted = await replacement.start({
      profileId: 'thought-core-v0',
      options: canonicalOptions,
      configIdentity: CONFIG_IDENTITY
    })
    assert.equal(restarted.result_class, 'ready')
    assert.equal(restarted.operation.reason, 'none')
    assert.equal(calls.length, 3)
    assert.ok(calls.every(({ validatedAuthority }) => validatedAuthority === authority))
    assert.equal(replacement.probeExecutor.configSha256, '1'.repeat(64))
    assert.equal(replacement.current.probe_config_sha256, '1'.repeat(64))
    assert.equal(harness.workers.length, 3)
  } finally {
    harness.cleanup()
  }
})

test('an in-flight duplicate joins without a second worker exchange', async () => {
  let releaseFirst
  let firstSeen
  let readMode = 'normal'
  const firstGate = new Promise((resolve) => { firstSeen = resolve })
  const releaseGate = new Promise((resolve) => { releaseFirst = resolve })
  let gated = false
  const harness = makeHarness({
    storeOverrides: {
      readOperation: (...args) => {
        if (readMode === 'failed') throw new Error('private_store_failure_sentinel')
        const operation = realStore.readOperation(...args)
        return readMode === 'drifted' ? { ...operation, revision: operation.revision + 1 } : operation
      }
    },
    workerBuilders: [({ events }) => new FakeWorker({
      events,
      onExecute: async () => {
        if (gated) return
        gated = true
        firstSeen()
        await releaseGate
      }
    })]
  })
  try {
    const first = harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    await firstGate
    const mismatched = await harness.runtime.start({
      profileId: 'thought-core-v0',
      options: canonicalOptions,
      configIdentity: { ...CONFIG_IDENTITY, effective_config_sha256: 'f'.repeat(64) }
    })
    assert.equal(mismatched.ok, false)
    assert.equal(mismatched.result_class, 'preflight_failed')
    assert.equal(mismatched.error_class, 'private_plan_invalid')
    assert.equal(harness.workers[0].requests.length, 1)
    readMode = 'failed'
    const unreadableDuplicate = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(unreadableDuplicate.ok, false)
    assert.equal(unreadableDuplicate.result_class, 'preflight_failed')
    assert.equal(unreadableDuplicate.error_class, 'supervisor_runtime_failed')
    assert.equal(unreadableDuplicate.operation.phase, 'idle')
    assert.equal(unreadableDuplicate.operation.operation_id, null)
    assert.equal(harness.workers[0].requests.length, 1)
    readMode = 'normal'
    const duplicate = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(duplicate.result_class, 'joined_existing')
    assert.equal(harness.workers.length, 1)
    assert.equal(harness.workers[0].requests.length, 1)
    releaseFirst()
    assert.equal((await first).result_class, 'ready')
    readMode = 'failed'
    const unreadableReady = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(unreadableReady.ok, false)
    assert.equal(unreadableReady.result_class, 'preflight_failed')
    assert.equal(unreadableReady.error_class, 'supervisor_runtime_failed')
    assert.equal(unreadableReady.operation.phase, 'idle')
    assert.equal(unreadableReady.operation.operation_id, null)
    assert.equal(harness.workers.length, 1)
    readMode = 'drifted'
    const driftedReady = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(driftedReady.ok, false)
    assert.equal(driftedReady.result_class, 'preflight_failed')
    assert.equal(driftedReady.error_class, 'private_plan_invalid')
    assert.equal(driftedReady.operation.phase, 'idle')
    assert.equal(driftedReady.operation.operation_id, null)
    assert.equal(harness.workers.length, 1)
    readMode = 'normal'
    const readyDuplicate = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(readyDuplicate.ok, true)
    assert.equal(readyDuplicate.result_class, 'joined_existing')
    assert.equal(harness.workers.length, 1)
    const readyMismatch = await harness.runtime.start({
      profileId: 'thought-core-v0',
      options: { ...canonicalOptions, SkipMediapipe: true, SkipVisionSnapshotProcessor: true },
      configIdentity: {
        ...CONFIG_IDENTITY,
        effective_config_sha256: configIdentityFor({
          ...canonicalOptions,
          SkipMediapipe: true,
          SkipVisionSnapshotProcessor: true
        }).effective_config_sha256,
        camera_policy: 'camera_excluded_by_profile'
      }
    })
    assert.equal(readyMismatch.ok, false)
    assert.equal(readyMismatch.result_class, 'preflight_failed')
    assert.equal(readyMismatch.error_class, 'private_plan_invalid')
    assert.equal(harness.workers.length, 1)
  } finally {
    releaseFirst()
    harness.cleanup()
  }
})

test('worker v2 requests retain the lease tuple and a unique persisted dispatch', async () => {
  const harness = makeHarness()
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.result_class, 'ready')
    const requests = harness.workers[0].requests
    assert.ok(requests.length > 0)
    assert.ok(requests.every((request) => request.schema_version === 'launcher_worker.v2'))
    assert.ok(requests.every((request) => request.supervisor_generation === harness.runtime.current.supervisor_generation))
    assert.ok(requests.every((request) => /^lp_[a-f0-9]{64}$/u.test(request.authority_lease_proof)))
    assert.equal(new Set(requests.map((request) => request.dispatch_id)).size, requests.length)
    assert.equal(harness.runtime.supervisorLease !== null, true)
    await harness.runtime.stop({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('external readiness timeout is truthful, rolls back owned jobs, and never sends external stop', async () => {
  const harness = makeHarness({
    responses: {
      'voicevox:probe': {
        result_class: 'readiness_timeout',
        ownership_class: 'not_applicable',
        listener_class: 'unknown',
        descendant_class: 'not_applicable'
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'readiness_timeout')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.supervisorLease, null)
    assert.deepEqual(
      harness.workers.flatMap((worker) => worker.requests)
        .filter((request) => request.service_id === 'voicevox')
        .map((request) => request.action),
      ['probe']
    )
    assert.ok(harness.workers[0].requests.some((request) => request.action === 'stop'))
  } finally {
    harness.cleanup()
  }
})

test('external worker failure matrix is persisted non-Ready and rolls back without external stop', async () => {
  for (const resultClass of ['invalid_request', 'internal_failure', 'cancelled', 'early_exit', 'listener_mismatch']) {
    const harness = makeHarness({
      responses: {
        'voicevox:probe': {
          result_class: resultClass,
          ownership_class: 'not_applicable',
          listener_class: resultClass === 'listener_mismatch' ? 'mismatch' : 'not_applicable',
          descendant_class: 'not_applicable'
        }
      },
      operationPrefix: `external${resultClass.replace('_', '')}`
    })
    try {
      const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
      assert.equal(result.ok, false, resultClass)
      assert.equal(result.result_class, 'failed', resultClass)
      assert.equal(result.operation.reason, resultClass === 'listener_mismatch' ? 'listener_mismatch' : 'semantic_probe_failed', resultClass)
      assert.equal(result.operation.cleanup, 'clear', resultClass)
      assert.equal(harness.runtime.supervisorLease, null, resultClass)
      assert.deepEqual(
        harness.workers.flatMap((worker) => worker.requests)
          .filter((request) => request.service_id === 'voicevox')
          .map((request) => request.action),
        ['probe'],
        resultClass
      )
      assert.ok(harness.workers[0].requests.some((request) => request.action === 'stop'), resultClass)
    } finally {
      harness.cleanup()
    }
  }
})

test('bounded semantic probe executor failure rolls back without misclassifying the supervisor', async () => {
  const harness = makeHarness({
    probeExecutor: {
      configSha256: '0'.repeat(64),
      async execute () {
        throw new LauncherProbeExecutorError('probe_executor_target_resolution_invalid')
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'semantic_probe_failed')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(result.operation.services.find((service) => service.service_id === 'aituber_kit').state, 'stopped')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'aituber_kit')
    assert.equal(harness.workers.length, 1)
    assert.ok(harness.events.includes('store:probe_failed:aituber_kit'))
    assert.equal(harness.events.includes('store:supervisor_crashed'), false)
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('runtime retains a fresh pre-request Environment snapshot without invalid_event', async () => {
  const probeExecutor = {
    configSha256: '0'.repeat(64),
    async execute (expected) {
      const result = successfulSemanticProbeResult(expected)
      if (expected.service_id !== 'environment_state_server') return result
      const requestedAt = Date.parse(expected.requested_at)
      return {
        ...result,
        source_observed_at: new Date(requestedAt - 5000).toISOString(),
        observed_at: new Date(requestedAt + 100).toISOString()
      }
    }
  }
  const harness = makeHarness({ probeExecutor, operationPrefix: 'environmentpre' })
  try {
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(started.ok, true)
    assert.equal(started.result_class, 'ready')
    assert.equal(started.operation.reason, 'none')
    const environment = harness.runtime.current.services.find((service) => service.service_id === 'environment_state_server')
    assert.equal(environment.probe_status, 'ready')
    assert.ok(Date.parse(environment.last_probe_result.source_observed_at) < Date.parse(environment.last_probe_result.requested_at))
    assert.equal(harness.events.includes('store:supervisor_crashed'), false)

    const stopped = await harness.runtime.stop({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.operation.cleanup, 'clear')
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('missing semantic probe executor after transport readiness retains the executor substage', async () => {
  let harness
  harness = makeHarness({
    workerBuilders: [({ events }) => new FakeWorker({
      events,
      onExecute (request) {
        if (request.service_id === 'aituber_kit' && request.action === 'probe') {
          harness.runtime.probeExecutor = null
        }
      }
    })]
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'semantic_probe_executor')
    assert.equal(result.operation.services.some((service) => service.state === 'ready'), false)
    assert.ok(harness.events.includes('store:probe_transport_ready:aituber_kit'))
    assert.ok(harness.events.includes('store:supervisor_crashed'))
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('semantic probe persistence failure retains operation_store as the safe first boundary', async () => {
  let failedOnce = false
  const harness = makeHarness({
    storeOverrides: {
      reduceAndPersist (current, event, ...args) {
        harness.events.push(`store:${event.event_type}${event.service_id ? `:${event.service_id}` : ''}`)
        if (!failedOnce && event.event_type === 'semantic_probe_completed') {
          failedOnce = true
          throw new LauncherContractError('operation_store_write_failed')
        }
        return realStore.reduceAndPersist(current, event, ...args)
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'operation_store')
    assert.equal(harness.runtime.current.services.find((service) => service.service_id === 'aituber_kit').last_probe_result, null)
    assert.ok(harness.events.includes('store:semantic_probe_completed:aituber_kit'))
    assert.ok(harness.events.includes('store:supervisor_crashed'))
    assert.ok(harness.events.indexOf('store:semantic_probe_completed:aituber_kit') < harness.events.indexOf('store:supervisor_crashed'))
  } finally {
    harness.cleanup()
  }
})

test('service-loop persistence failure retains operation_store without raw detail', async () => {
  let failedOnce = false
  const privateSentinel = 'PRIVATE_SERVICE_STORE_FAILURE_SENTINEL'
  const harness = makeHarness({
    storeOverrides: {
      reduceAndPersist (current, event, ...args) {
        harness.events.push(`store:${event.event_type}${event.service_id ? `:${event.service_id}` : ''}`)
        if (!failedOnce && event.event_type === 'spawn_succeeded' && event.service_id === 'aituber_kit') {
          failedOnce = true
          throw new LauncherContractError(privateSentinel)
        }
        return realStore.reduceAndPersist(current, event, ...args)
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'operation_store')
    assert.equal(JSON.stringify(result).includes(privateSentinel), false)
    assert.equal(result.operation.services.some((service) => service.state === 'ready'), false)
    assert.ok(harness.events.includes('store:spawn_succeeded:aituber_kit'))
    assert.ok(harness.events.includes('store:supervisor_crashed'))
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('typed semantic probe failure with store failure retains operation_store without private detail', async () => {
  let failedOnce = false
  const harness = makeHarness({
    probeExecutor: {
      configSha256: '0'.repeat(64),
      async execute () {
        throw new LauncherProbeExecutorError('PRIVATE_PROBE_FAILURE_SENTINEL')
      }
    },
    storeOverrides: {
      reduceAndPersist (current, event, ...args) {
        harness.events.push(`store:${event.event_type}${event.service_id ? `:${event.service_id}` : ''}`)
        if (!failedOnce && event.event_type === 'probe_failed') {
          failedOnce = true
          throw new LauncherContractError('PRIVATE_STORE_FAILURE_SENTINEL')
        }
        return realStore.reduceAndPersist(current, event, ...args)
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'operation_store')
    assert.equal(harness.runtime.current.services.find((service) => service.service_id === 'aituber_kit').last_probe_result, null)
    assert.equal(result.operation.services.some((service) => service.state === 'ready'), false)
    assert.ok(harness.events.includes('store:probe_failed:aituber_kit'))
    assert.ok(harness.events.includes('store:supervisor_crashed'))
    assert.ok(harness.events.indexOf('store:probe_failed:aituber_kit') < harness.events.indexOf('store:supervisor_crashed'))
    assert.equal(JSON.stringify(result).includes('PRIVATE_PROBE_FAILURE_SENTINEL'), false)
    assert.equal(JSON.stringify(result).includes('PRIVATE_STORE_FAILURE_SENTINEL'), false)
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('untyped semantic probe failures retain only their fixed first substage', async () => {
  for (const responsibleId of [
    'semantic_probe_expectation',
    'semantic_probe_executor',
    'semantic_probe_result'
  ]) {
    const harness = makeHarness({
      ...(responsibleId === 'semantic_probe_executor'
        ? {
            probeExecutor: {
              configSha256: '0'.repeat(64),
              async execute () { throw new Error('PRIVATE_EXECUTOR_SENTINEL') }
            }
          }
        : {})
    })
    try {
      if (responsibleId === 'semantic_probe_expectation') {
        harness.runtime.probeExpectationFor = () => { throw new Error('PRIVATE_EXPECTATION_SENTINEL') }
      }
      if (responsibleId === 'semantic_probe_result') {
        const apply = harness.runtime.apply.bind(harness.runtime)
        harness.runtime.apply = (eventType, ...args) => {
          if (eventType === 'semantic_probe_completed') throw new Error('PRIVATE_RESULT_SENTINEL')
          return apply(eventType, ...args)
        }
      }
      const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
      assert.equal(result.ok, false, responsibleId)
      assert.equal(result.operation.reason, 'supervisor_crash', responsibleId)
      assert.equal(harness.runtime.current.primary_result.responsible_id, responsibleId)
      assert.equal(harness.runtime.current.services.find((service) => service.service_id === 'aituber_kit').last_probe_result, null)
      const serialized = JSON.stringify(result)
      assert.equal(serialized.includes('PRIVATE_'), false)
    } finally {
      harness.cleanup()
    }
  }
})

test('unknown supervisor crash attribution remains launcher_supervisor', () => {
  const operationId = 'lop_crashresponsible'
  const operation = reducer.reduce(
    reducer.reduce(
      reducer.startOperation(null, operationId, authority, CONFIG_IDENTITY, PLAN_IDENTITY, PROBE_CONFIG_SHA256).operation,
      { event_type: 'preflight_started', operation_id: operationId },
      authority
    ),
    { event_type: 'supervisor_crashed', operation_id: operationId, responsible_id: 'PRIVATE_UNKNOWN_SENTINEL' },
    authority
  )
  assert.equal(operation.primary_result.responsible_id, 'launcher_supervisor')
  assert.equal(JSON.stringify(operation).includes('PRIVATE_UNKNOWN_SENTINEL'), false)
})

test('exact3 reaches Ready without optional owned services or configuration-excluded VOICEVOX exchange', async () => {
  const exact3Identity = configIdentityFor(exact3Options)
  const harness = makeHarness({
    includedServiceIds: requiredOwnedIds,
    configIdentity: exact3Identity
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: exact3Options })
    assert.equal(result.result_class, 'ready')
    assert.equal(result.operation.effective_config_sha256, exact3Identity.effective_config_sha256)
    const states = new Map(result.operation.services.map((service) => [service.service_id, service.state]))
    const absentIds = authority.graph.services
      .filter((service) => !(service.ownership === 'owned' && service.requirement === 'required'))
      .map((service) => service.service_id)
    for (const serviceId of absentIds) assert.equal(states.get(serviceId), 'optional_absent', serviceId)
    assert.deepEqual(
      harness.workers[0].requests.filter((request) => absentIds.includes(request.service_id)),
      []
    )
    assert.deepEqual(
      [...new Set(harness.workers[0].requests.map((request) => request.service_id))].sort(),
      [...requiredOwnedIds].sort()
    )
  } finally {
    harness.cleanup()
  }
})

test('worker crash enters recovery and a fresh cleanup worker leaves a bounded final record', async () => {
  const harness = makeHarness({
    workerBuilders: [
      ({ events }) => new FakeWorker({
        events,
        responses: {
          'touchdesigner_control_gui:start': new LauncherJobWorkerError('worker_transport_failed')
        }
      }),
      ({ events }) => new FakeWorker({ events })
    ]
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'touchdesigner_control_gui')
    assert.equal(harness.workers.length, 2)
    assert.ok(harness.events.includes('store:recovery_started'))
    assert.ok(harness.events.includes('store:recovery_completed'))
    assert.equal(harness.runtime.supervisorLease, null)
    assert.ok(harness.events.includes('store:lease:release'))
    assert.equal(realStore.readOperation(authority, harness.root).phase, 'failed')
  } finally {
    harness.cleanup()
  }
})

test('AIT probe worker failures expose only bounded invocation-local error classes', async () => {
  const privateSentinel = 'PRIVATE_AIT_WORKER_FAILURE_DETAIL'
  const cases = [
    ['worker_transport_failed', 'worker_transport_failed', 'supervisor_crash'],
    ['worker_response_invalid', 'worker_response_invalid', 'supervisor_crash'],
    ['worker_response_mismatch', 'worker_response_mismatch', 'supervisor_crash'],
    ['worker_response_oversized', 'worker_response_oversized', 'supervisor_crash'],
    ['worker_transport_timeout', 'none', 'readiness_timeout'],
    [null, 'supervisor_runtime_failed', 'supervisor_crash']
  ]
  for (const [index, [workerCode, errorClass, reason]] of cases.entries()) {
    const failure = workerCode === null
      ? Object.assign(new Error(privateSentinel), { code: privateSentinel })
      : Object.assign(new LauncherJobWorkerError(workerCode), { private_detail: privateSentinel })
    const harness = makeHarness({
      operationPrefix: `workerclass${index}`,
      workerBuilders: [
        ({ events }) => new FakeWorker({
          events,
          responses: { 'aituber_kit:probe': failure }
        }),
        ({ events }) => new FakeWorker({ events })
      ]
    })
    try {
      const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
      const aituberRequests = harness.workers.flatMap((worker) => worker.requests)
        .filter((request) => request.service_id === 'aituber_kit')
      const attempts = aituberRequests.filter((request) => request.action !== 'stop').map((request) => request.action)
      const cleanupActions = aituberRequests.filter((request) => request.action === 'stop').map((request) => request.action)
      const stored = realStore.readOperation(authority, harness.root)
      assert.equal(result.error_class, errorClass, workerCode)
      assert.equal(result.operation.reason, reason, workerCode)
      assert.equal(result.operation.cleanup, 'clear', workerCode)
      assert.equal(harness.runtime.current.primary_result.responsible_id, 'aituber_kit', workerCode)
      assert.deepEqual(attempts, ['start', 'probe'], workerCode)
      assert.equal(attempts.length, 2, workerCode)
      assert.deepEqual(cleanupActions, ['stop'], workerCode)
      assert.deepEqual(result.operation.residue_service_ids, [], workerCode)
      assert.equal(result.raw_private_publication_flags, false, workerCode)
      assert.equal(result.operation.raw_private_publication_flags, false, workerCode)
      assert.equal(JSON.stringify({ result, stored }).includes(privateSentinel), false, workerCode)
      if (errorClass !== 'none') assert.equal(JSON.stringify(stored).includes(errorClass), false, workerCode)
    } finally {
      harness.cleanup()
    }
  }
})

test('worker result contract failure retains the responsible service without raw detail', async () => {
  const harness = makeHarness({
    workerBuilders: [
      ({ events }) => new FakeWorker({
        events,
        responses: {
          'aituber_kit:start': { worker_nonce: 'lw_privatewrongworker0001' }
        }
      }),
      ({ events }) => new FakeWorker({ events })
    ]
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.current.primary_result.responsible_id, 'aituber_kit')
    assert.equal(JSON.stringify(result).includes('lw_privatewrongworker0001'), false)
    assert.equal(harness.events.includes('store:semantic_probe_completed:aituber_kit'), false)
    assert.equal(harness.events.includes('store:probe_failed:aituber_kit'), false)
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('failed cleanup remains truthful and a retained plan can be retried by a fresh runtime', async () => {
  for (const failureMode of ['worker_close', 'plan_remove']) {
    let planRemoveCalls = 0
    const harness = makeHarness({
      operationPrefix: `cleanup${failureMode.replace('_', '')}`,
      workerBuilders: [
        ({ events }) => new FakeWorker({
          events,
          responses: {
            'aituber_kit:start': new LauncherJobWorkerError('worker_transport_failed')
          },
          onClose: failureMode === 'worker_close'
            ? async () => { throw new Error('private_close_failed') }
            : null
        })
      ],
      planRemover: failureMode === 'plan_remove'
        ? () => {
            planRemoveCalls += 1
            if (planRemoveCalls === 2) throw new Error('private_plan_remove_failed')
          }
        : null
    })
    try {
      const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
      assert.equal(result.ok, false, failureMode)
      assert.equal(result.operation.recovery_required, true, failureMode)
      assert.equal(harness.events.includes('store:recovery_completed'), false, failureMode)
      assert.equal(result.operation.services
        .filter((service) => allOwnedIds.includes(service.service_id))
        .some((service) => service.state === 'ready'), false, failureMode)
      if (failureMode === 'worker_close') {
        assert.equal(result.operation.phase, 'recovering')
        assert.equal(result.operation.cleanup, 'unknown')
        assert.notEqual(harness.runtime.supervisorLease, null)
        assert.equal(harness.events.includes('store:lease:release'), false)
        assert.equal(harness.workers.length, 1)
        assert.equal(harness.workers[0].requests.filter((request) => request.action === 'stop').length, 0)
        assert.deepEqual(result.operation.residue_service_ids, [])
      } else {
        assert.equal(result.operation.phase, 'residue')
        assert.equal(result.operation.cleanup, 'residue')
        assert.equal(harness.runtime.supervisorLease, null)
        assert.equal(planRemoveCalls, 2)
        assert.equal(harness.workers.length, 2)
        assert.ok(result.operation.residue_service_ids.length > 0)

        const replacement = harness.createRuntime()
        const retried = await replacement.stop({ profileId: 'thought-core-v0' })
        assert.equal(retried.ok, true)
        assert.equal(retried.result_class, 'stopped')
        assert.equal(retried.operation.cleanup, 'clear')
        assert.equal(replacement.supervisorLease, null)
        assert.equal(planRemoveCalls, 3)
      }
    } finally {
      harness.cleanup()
    }
  }
})

test('foreign ownership result is rejected into rollback without kill-by-port authority', async () => {
  const harness = makeHarness({
    responses: {
      'home_assistant_bridge:start': {
        result_class: 'listener_mismatch',
        ownership_class: 'mismatch',
        listener_class: 'mismatch',
        descendant_class: 'foreign'
      }
    }
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.result_class, 'failed')
    assert.equal(result.operation.reason, 'listener_mismatch')
    assert.equal(result.operation.cleanup, 'clear')
    assert.equal(harness.runtime.supervisorLease, null)
  } finally {
    harness.cleanup()
  }
})

test('replayed or mismatched worker result cannot advance the operation', async () => {
  const harness = makeHarness({
    workerBuilders: [
      ({ events }) => new FakeWorker({
        events,
        responses: {
          'aituber_kit:start': (request) => resultFor(request, {
            expected_revision: request.expected_revision - 1
          })
        }
      }),
      ({ events }) => new FakeWorker({ events })
    ]
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.notEqual(result.operation.phase, 'ready')
    assert.equal(result.operation.reason, 'supervisor_crash')
    assert.equal(result.operation.cleanup, 'clear')
  } finally {
    harness.cleanup()
  }
})

test('private plan preflight rejects noncanonical profiles before operation-store writes', () => {
  const harness = makeHarness({
    planCompiler: () => {
      throw Object.assign(new Error('private_plan_profile_invalid'), {
        name: 'LauncherPrivatePlanError',
        code: 'private_plan_profile_invalid'
      })
    }
  })
  return harness.runtime.start({ profileId: 'demo-fast', options: canonicalOptions }).then((result) => {
    try {
      assert.equal(result.result_class, 'preflight_failed')
      assert.equal(harness.events.includes('store:start'), false)
      assert.equal(harness.workers.length, 0)
    } finally {
      harness.cleanup()
    }
  })
})

test('closed-loop private plan without the task-owned Event Journal binding fails before store or worker actions', async () => {
  const privateSentinel = 'must-not-publish'
  const harness = makeHarness({
    planCompiler: () => ({
      document: {
        schema_version: 'launcher_private_service_plans.v1',
        graph_sha256: authority.identities.graphSha256,
        binding_sha256: authority.identities.bindingSha256,
        profile_id: CONFIG_IDENTITY.profile_id,
        effective_config_sha256: CONFIG_IDENTITY.effective_config_sha256,
        camera_policy: CONFIG_IDENTITY.camera_policy,
        worker_file_path: process.execPath,
        services: [{
          service_id: 'thought_core_api',
          environment: {
            THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: '1',
            PRIVATE_SENTINEL: privateSentinel
          }
        }]
      },
      powershell_path: process.execPath,
      ...PLAN_IDENTITY,
      included_service_ids: [...allOwnedIds]
    })
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.result_class, 'preflight_failed')
    assert.equal(result.error_class, 'private_plan_invalid')
    assert.equal(harness.events.includes('store:start'), false)
    assert.equal(harness.workers.length, 0)
    assert.equal(JSON.stringify(result).includes(privateSentinel), false)
  } finally {
    harness.cleanup()
  }
})

test('private plan preflight rejects an intermediate non-symlink Windows reparse before worker bytes or actions', async () => {
  const workerPath = 'C:\\Program Files\\PowerShell\\7\\pwsh.exe'
  let workerReads = 0
  const harness = makeHarness({
    planCompiler: () => verifyTrustedWindowsWorkerExecutable({
      filePath: workerPath,
      io: simulatedWindowsReparseIo({
        filePath: workerPath,
        reparseName: 'PowerShell',
        onWorkerRead: () => { workerReads += 1 }
      })
    })
  })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.ok, false)
    assert.equal(result.error_class, 'private_plan_invalid')
    assert.equal(workerReads, 0)
    assert.equal(harness.workers.length, 0)
    assert.equal(harness.events.some((event) => event.startsWith('exchange:')), false)
    assert.equal(JSON.stringify(result).includes(workerPath), false)
    assert.equal(JSON.stringify(result).includes('PRIVATE_REPARSE_PARENT'), false)
  } finally {
    harness.cleanup()
  }
})

test('idle stop does not compile a private plan or create a worker', async () => {
  let compileCalls = 0
  const harness = makeHarness({
    planCompiler: () => {
      compileCalls += 1
      throw new Error('must not compile')
    }
  })
  try {
    const stopped = await harness.runtime.stop({
      profileId: 'thought-core-v0',
      options: canonicalOptions
    })
    assert.equal(stopped.ok, true)
    assert.equal(stopped.result_class, 'already_stopped')
    assert.equal(compileCalls, 0)
    assert.equal(harness.workers.length, 0)
  } finally {
    harness.cleanup()
  }
})

test('public projection exposes only bounded reducer fields', () => {
  const operation = reducerFixture()
  const projected = publicOperation(operation)
  assert.deepEqual(Object.keys(projected).sort(), [
    'authority_class',
    'camera_policy',
    'cleanup',
    'effective_config_sha256',
    'intent',
    'joined_existing',
    'operation_id',
    'phase',
    'profile_id',
    'raw_private_publication_flags',
    'reason',
    'recovery_required',
    'residue_service_ids',
    'revision',
    'rollback_required',
    'schema_version',
    'services'
  ])
  assert.equal(projected.raw_private_publication_flags, false)
  assert.equal(Object.hasOwn(projected, 'private_plan_sha256'), false)
  assert.equal(Object.hasOwn(projected, 'worker_executable_class'), false)
  assert.equal(Object.hasOwn(projected, 'worker_executable_sha256'), false)
  assert.equal(Object.hasOwn(projected, 'probe_config_sha256'), false)
})

const reducerFixture = () => ({
  profile_id: CONFIG_IDENTITY.profile_id,
  effective_config_sha256: CONFIG_IDENTITY.effective_config_sha256,
  camera_policy: CONFIG_IDENTITY.camera_policy,
  ...PLAN_IDENTITY,
  probe_config_sha256: PROBE_CONFIG_SHA256,
  operation_id: 'lop_publicfixture01',
  intent: 'start',
  phase: 'ready',
  reason: 'none',
  cleanup: 'not_started',
  revision: 1,
  joined_existing: false,
  rollback_required: false,
  recovery_required: false,
  services: [{ service_id: 'voicevox', state: 'external_ready' }],
  residue_service_ids: []
})

test('operation-store read failure cannot publish cached ready or idle state', () => {
  const readFailure = new LauncherContractError('operation_store_record_invalid')
  const harness = makeHarness({
    storeOverrides: {
      readOperation: () => { throw readFailure }
    }
  })
  try {
    harness.runtime.current = reducerFixture()
    const projected = harness.runtime.publicState()
    assert.equal(projected.phase, 'failed')
    assert.notEqual(projected.phase, 'ready')
    assert.notEqual(projected.phase, 'idle')
    assert.equal(projected.reason, 'invalid_event')
    assert.equal(projected.cleanup, 'unknown')
    assert.equal(projected.operation_id, null)
    assert.deepEqual(projected.services, [])
    assert.equal(JSON.stringify(projected).includes('operation_store_record_invalid'), false)
    assert.throws(
      () => harness.runtime.status(),
      (error) => error instanceof LauncherContractError && error.code === 'operation_store_record_invalid'
    )
  } finally {
    harness.cleanup()
  }
})

test('real private compiler never puts external VOICEVOX in the owned plan', () => {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-plan-'))
  const executableRoot = path.join(workspace, 'bin')
  const makeDirectory = (relative) => fs.mkdirSync(path.join(workspace, relative), { recursive: true })
  const makeFile = (relative, content = '') => {
    const target = path.join(workspace, relative)
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, content, 'utf8')
    return target
  }
  const verifyTestWorkerExecutable = ({ filePath, expectedClass = null, expectedSha256 = null }) => {
    if (expectedClass !== null) assert.equal(expectedClass, PLAN_IDENTITY.worker_executable_class)
    if (expectedSha256 !== null) assert.equal(expectedSha256, PLAN_IDENTITY.worker_executable_sha256)
    return Object.freeze({
      worker_file_path: path.resolve(filePath),
      worker_executable_class: PLAN_IDENTITY.worker_executable_class,
      worker_executable_sha256: PLAN_IDENTITY.worker_executable_sha256
    })
  }
  try {
    for (const relative of [
      'organs/action/home-assistant-server/config',
      'organs/environment/environment-state-server',
      'organs/reflex/mediapipe-sword-sign',
      'organs/environment/vision-snapshot-processor/src/vision_snapshot_processor',
      'organs/expression/aituber-kit',
      'organs/display/touchdesigner-ai-controller/tools',
      'organs/speech-input/ai-talk-core',
      'local/env'
    ]) makeDirectory(relative)
    makeFile('organs/action/home-assistant-server/.env', 'HOME_ASSISTANT_TOKEN=home-assistant-secret\nHOME_CONTROL_API_TOKEN=0123456789abcdef\nENVIRONMENT_API_TOKEN=fedcba9876543210\n')
    const liveConfig = makeFile('local/env/home-control.live.yaml', 'profile: local\n')
    makeFile('organs/environment/vision-snapshot-processor/src/vision_snapshot_processor/main.py')
    const nextEntrypoint = makeFile('organs/expression/aituber-kit/node_modules/next/dist/bin/next')
    fs.mkdirSync(executableRoot, { recursive: true })
    const executables = Object.fromEntries(['uv', 'node', 'pwsh'].map((name) => [name, makeFile(`bin/${name}.exe`)]))
    const effectiveOptions = { ...canonicalOptions, HomeControlConfigPath: liveConfig }
    const compiled = compilePrivateServicePlan({
      repositoryRoot: ROOT,
      workspaceRoot: workspace,
      privateRuntimeRoot: path.join(workspace, 'state'),
      profileId: 'thought-core-v0',
      options: effectiveOptions,
      configIdentity: configIdentityFor(effectiveOptions),
      authority,
      processEnvironment: {
        PATH: executableRoot,
        SYSTEMROOT: 'C:\\Windows',
        TEMP: workspace,
        TMP: workspace,
        HOME_CONTROL_API_TOKEN: '0123456789abcdef',
        ENVIRONMENT_API_TOKEN: 'fedcba9876543210',
        THOUGHT_CORE_EVENT_JOURNAL_ENABLED: '0',
        THOUGHT_CORE_EVENT_JOURNAL_DIR: path.join(workspace, 'ambient-journal'),
        THOUGHT_CORE_EVENT_JOURNAL_PATH: path.join(workspace, 'ambient-journal.jsonl'),
        PRIVATE_SENTINEL: 'must-not-be-inherited',
        SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE: 'must-not-be-inherited'
      },
      resolveExecutable: (name) => executables[name],
      verifyWorkerExecutable: verifyTestWorkerExecutable,
      nonceFactory: () => '00112233445566778899aabbccddeeff'
    })
    const privateRuntimeRoot = path.join(workspace, 'state')
    const planPath = writePrivateServicePlan(compiled, privateRuntimeRoot)
    const recovered = readPrivateServicePlan({
      privateRuntimeRoot,
      configIdentity: configIdentityFor(effectiveOptions),
      planIdentity: {
        private_plan_sha256: compiled.private_plan_sha256,
        worker_executable_class: compiled.worker_executable_class,
        worker_executable_sha256: compiled.worker_executable_sha256
      },
      verifyWorkerExecutable: verifyTestWorkerExecutable,
      authority
    })
    assert.deepEqual(recovered.document, compiled.document)
    assert.deepEqual(recovered.included_service_ids, compiled.included_service_ids)
    assert.equal(recovered.plan_path, planPath)
    assert.equal(recovered.powershell_path, executables.pwsh)
    const canonicalBytes = fs.readFileSync(planPath)
    assert.equal(canonicalBytes.toString('utf8'), serializePrivateServicePlan(compiled.document))
    assert.equal(crypto.createHash('sha256').update(canonicalBytes).digest('hex'), compiled.private_plan_sha256)
    const driftedPlan = JSON.parse(fs.readFileSync(planPath, 'utf8'))
    driftedPlan.effective_config_sha256 = 'f'.repeat(64)
    fs.writeFileSync(planPath, `${JSON.stringify(driftedPlan)}\n`, 'utf8')
    assert.throws(
      () => readPrivateServicePlan({
        privateRuntimeRoot,
        configIdentity: configIdentityFor(effectiveOptions),
        planIdentity: {
          private_plan_sha256: compiled.private_plan_sha256,
          worker_executable_class: compiled.worker_executable_class,
          worker_executable_sha256: compiled.worker_executable_sha256
        },
        verifyWorkerExecutable: verifyTestWorkerExecutable,
        authority
      }),
      (error) => error?.code === 'private_plan_identity_invalid'
    )
    assert.equal(compiled.document.services.some((service) => service.service_id === 'voicevox'), false)
    const aituberPlan = compiled.document.services.find((service) => service.service_id === 'aituber_kit')
    const thoughtCorePlan = compiled.document.services.find((service) => service.service_id === 'thought_core_api')
    const homePlan = compiled.document.services.find((service) => service.service_id === 'home_assistant_bridge')
    const environmentPlan = compiled.document.services.find((service) => service.service_id === 'environment_state_server')
    for (const plan of [homePlan, environmentPlan]) {
      assert.equal(plan.arguments.includes('--env-file'), false)
      assert.equal(plan.clear_inherited_environment, true)
      assert.deepEqual(plan.remove_environment, [])
      assert.equal(plan.environment.HOME_CONTROL_API_TOKEN, '0123456789abcdef')
      assert.equal(Object.hasOwn(plan.environment, 'PRIVATE_SENTINEL'), false)
      assert.equal(Object.hasOwn(plan.environment, 'SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE'), false)
    }
    assert.equal(Object.hasOwn(homePlan.environment, 'ENVIRONMENT_API_TOKEN'), false)
    assert.equal(homePlan.environment.HOME_ASSISTANT_TOKEN, 'home-assistant-secret')
    assert.equal(environmentPlan.environment.ENVIRONMENT_API_TOKEN, 'fedcba9876543210')
    assert.equal(Object.hasOwn(environmentPlan.environment, 'HOME_ASSISTANT_TOKEN'), false)
    assert.equal(homePlan.environment.HOME_CONTROL_CONFIG, liveConfig)
    assert.equal(Object.hasOwn(environmentPlan.environment, 'HOME_CONTROL_CONFIG'), false)
    assert.equal(Object.hasOwn(environmentPlan.environment, 'HOME_CONTROL_FAULT_MODE'), false)
    assert.equal(thoughtCorePlan.environment.THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED, '1')
    assert.equal(thoughtCorePlan.environment.THOUGHT_CORE_EVENT_JOURNAL_ENABLED, '1')
    assert.equal(
      thoughtCorePlan.environment.THOUGHT_CORE_EVENT_JOURNAL_DIR,
      expectedEventJournalDirectory(privateRuntimeRoot)
    )
    assert.equal(Object.hasOwn(thoughtCorePlan.environment, 'THOUGHT_CORE_EVENT_JOURNAL_PATH'), false)
    assert.equal(Object.hasOwn(thoughtCorePlan.environment, 'PRIVATE_SENTINEL'), false)
    assert.deepEqual(homePlan.arguments.slice(0, 5), [
      'run', 'python', '-m', 'uvicorn', 'home_control_bridge.main:app'
    ])
    assert.deepEqual(environmentPlan.arguments.slice(0, 4), [
      'run', 'python', '-m', 'environment_state_server.main'
    ])
    assert.deepEqual(
      environmentPlan.arguments.slice(
        environmentPlan.arguments.indexOf('--profile-id'),
        environmentPlan.arguments.indexOf('--profile-id') + 6
      ),
      [
        '--profile-id', configIdentityFor(effectiveOptions).profile_id,
        '--effective-config-sha256', configIdentityFor(effectiveOptions).effective_config_sha256,
        '--camera-policy', configIdentityFor(effectiveOptions).camera_policy
      ]
    )
    assert.equal(environmentPlan.arguments.includes('--startup-config-sha256'), false)
    assert.equal(compiled.document.profile_id, configIdentityFor(effectiveOptions).profile_id)
    assert.equal(
      compiled.document.effective_config_sha256,
      configIdentityFor(effectiveOptions).effective_config_sha256
    )
    assert.equal(compiled.document.camera_policy, configIdentityFor(effectiveOptions).camera_policy)
    const directNameOptions = {
      ...effectiveOptions,
      MediapipeCameraName: 'direct-camera-name',
      MediapipeCameraSelectionKey: ''
    }
    assert.notEqual(
      configIdentityFor({ ...directNameOptions, MediapipeCameraName: 'different-camera' }).effective_config_sha256,
      configIdentityFor(directNameOptions).effective_config_sha256
    )
    const savedSelectionOptions = {
      ...effectiveOptions,
      MediapipeCameraName: 'operator-facing-label',
      MediapipeCameraSelectionKey: 'camera_selection_0001'
    }
    const executionSelectionOptions = {
      ...savedSelectionOptions,
      MediapipeCameraName: '@device_pnp_private_runtime_name'
    }
    assert.equal(
      configIdentityFor(executionSelectionOptions).effective_config_sha256,
      configIdentityFor(savedSelectionOptions).effective_config_sha256
    )
    assert.notEqual(
      configIdentityFor({
        ...savedSelectionOptions,
        MediapipeCameraSelectionKey: 'camera_selection_0002'
      }).effective_config_sha256,
      configIdentityFor(savedSelectionOptions).effective_config_sha256
    )
    const selectedCompiled = compilePrivateServicePlan({
      repositoryRoot: ROOT,
      workspaceRoot: workspace,
      privateRuntimeRoot: path.join(workspace, 'state-selected'),
      profileId: 'thought-core-v0',
      options: executionSelectionOptions,
      configIdentity: configIdentityFor(savedSelectionOptions),
      authority,
      processEnvironment: {
        PATH: executableRoot,
        SYSTEMROOT: 'C:\\Windows',
        TEMP: workspace,
        TMP: workspace,
        HOME_CONTROL_API_TOKEN: '0123456789abcdef',
        ENVIRONMENT_API_TOKEN: 'fedcba9876543210'
      },
      resolveExecutable: (name) => executables[name],
      verifyWorkerExecutable: verifyTestWorkerExecutable,
      nonceFactory: () => 'ffeeddccbbaa99887766554433221100'
    })
    assert.equal(
      selectedCompiled.document.effective_config_sha256,
      configIdentityFor(savedSelectionOptions).effective_config_sha256
    )
    assert.equal(path.basename(aituberPlan.file_path), 'node.exe')
    assert.equal(aituberPlan.arguments[0], nextEntrypoint)
    assert.deepEqual(aituberPlan.arguments.slice(1), [
      'dev', '--hostname', canonicalOptions.AituberHost, '--port', String(canonicalOptions.AituberPort)
    ])
    assert.deepEqual(
      compiled.included_service_ids,
      [...compiled.included_service_ids].sort()
    )
    const exact3EffectiveOptions = {
      ...exact3Options,
      HomeControlConfigPath: 'configuration-excluded'
    }
    const exact3Compiled = compilePrivateServicePlan({
      repositoryRoot: ROOT,
      workspaceRoot: workspace,
      privateRuntimeRoot: path.join(workspace, 'state-exact3'),
      profileId: 'thought-core-v0',
      options: exact3EffectiveOptions,
      configIdentity: configIdentityFor(exact3EffectiveOptions),
      authority,
      processEnvironment: {
        PATH: executableRoot,
        SYSTEMROOT: 'C:\\Windows',
        TEMP: workspace,
        TMP: workspace
      },
      resolveExecutable: (name) => executables[name],
      verifyWorkerExecutable: verifyTestWorkerExecutable,
      nonceFactory: () => '11223344556677889900aabbccddeeff'
    })
    assert.deepEqual(
      exact3Compiled.document.services.map((service) => service.service_id),
      ['openai_provider_broker', 'thought_core_api', 'aituber_kit']
    )
    assert.deepEqual(exact3Compiled.included_service_ids, [...requiredOwnedIds].sort())
    const exact3Serialized = serializePrivateServicePlan(exact3Compiled.document)
    for (const excluded of [
      'home_assistant_bridge', 'environment_state_server', 'thought_core_watcher',
      'touchdesigner_control_gui', 'mediapipe_camera_hub_stack',
      'vision_snapshot_processor', 'voicevox', 'direct_send'
    ]) assert.equal(exact3Serialized.includes(excluded), false, excluded)
    assert.throws(
      () => compilePrivateServicePlan({
        repositoryRoot: ROOT,
        workspaceRoot: workspace,
        privateRuntimeRoot: path.join(workspace, 'state'),
        profileId: 'thought-core-v0',
        options: { ...canonicalOptions, HomeControlConfigPath: liveConfig, VoicevoxReadyTimeoutSeconds: 46 },
        configIdentity: CONFIG_IDENTITY,
        authority,
        processEnvironment: {
          PATH: executableRoot,
          SYSTEMROOT: 'C:\\Windows',
          TEMP: workspace,
          TMP: workspace,
          HOME_CONTROL_API_TOKEN: '0123456789abcdef',
          ENVIRONMENT_API_TOKEN: 'fedcba9876543210'
        },
        resolveExecutable: (name) => executables[name],
        verifyWorkerExecutable: verifyTestWorkerExecutable,
        nonceFactory: () => '00112233445566778899aabbccddeeff'
      }),
      (error) => error?.code === 'private_plan_config_invalid'
    )
    makeFile('organs/action/home-assistant-server/.env', 'HOME_CONTROL_API_TOKEN=0123456789abcdef\nENVIRONMENT_API_TOKEN=fedcba9876543210\n')
    assert.throws(
      () => compilePrivateServicePlan({
        repositoryRoot: ROOT,
        workspaceRoot: workspace,
        privateRuntimeRoot: path.join(workspace, 'state'),
        profileId: 'thought-core-v0',
        options: effectiveOptions,
        configIdentity: configIdentityFor(effectiveOptions),
        authority,
        processEnvironment: {
          PATH: executableRoot,
          SYSTEMROOT: 'C:\\Windows',
          TEMP: workspace,
          TMP: workspace,
          HOME_CONTROL_API_TOKEN: '0123456789abcdef',
          ENVIRONMENT_API_TOKEN: 'fedcba9876543210'
        },
        resolveExecutable: (name) => executables[name],
        verifyWorkerExecutable: verifyTestWorkerExecutable,
        nonceFactory: () => '00112233445566778899aabbccddeeff'
      }),
      (error) => error?.code === 'private_plan_config_invalid'
    )
    assert.throws(
      () => compilePrivateServicePlan({
        repositoryRoot: ROOT,
        workspaceRoot: workspace,
        privateRuntimeRoot: path.join(workspace, 'state'),
        profileId: 'thought-core-v0',
        options: effectiveOptions,
        configIdentity: configIdentityFor(effectiveOptions),
        authority,
        processEnvironment: {
          PATH: executableRoot,
          SYSTEMROOT: 'C:\\Windows',
          TEMP: workspace,
          TMP: workspace,
          HOME_ASSISTANT_TOKEN: 'home-assistant-secret',
          HOME_CONTROL_API_TOKEN: '0123456789abcdef\u0000',
          ENVIRONMENT_API_TOKEN: 'fedcba9876543210'
        },
        resolveExecutable: (name) => executables[name],
        verifyWorkerExecutable: verifyTestWorkerExecutable,
        nonceFactory: () => '00112233445566778899aabbccddeeff'
      }),
      (error) => error?.code === 'private_plan_config_invalid'
    )
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true })
  }
})
