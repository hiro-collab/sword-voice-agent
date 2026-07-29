'use strict'

const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  LauncherContractError,
  loadAuthority
} = require('../tools/home-control-launcher/launcher-supervisor-contract')
const realStore = require('../tools/home-control-launcher/launcher-operation-store')
const { LauncherJobWorkerError } = require('../tools/home-control-launcher/launcher-job-worker-client')
const {
  compilePrivateServicePlan
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
  workerBuilders = [],
  responses = {},
  operationPrefix = 'runtime',
  planCompiler = null,
  planRemover = null,
  probeExecutor = undefined,
  probeExecutorFactory = null,
  storeOverrides = {}
} = {}) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launcher-runtime-'))
  const events = []
  const workers = []
  let operationSequence = 0
  let nonceSequence = 0
  let dispatchSequence = 0
  const compiled = {
    document: {
      schema_version: 'launcher_private_service_plans.v1',
      graph_sha256: authority.identities.graphSha256,
      binding_sha256: authority.identities.bindingSha256,
      services: []
    },
    powershell_path: process.execPath,
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
  const runtime = new LauncherSupervisorRuntime({
    repositoryRoot: ROOT,
    workspaceRoot: ROOT,
    privateRuntimeRoot: root,
    authority,
    store,
    planCompiler: planCompiler || (() => {
      events.push('plan:compile')
      return compiled
    }),
    planWriter: () => {
      events.push('plan:write')
      return __filename
    },
    planRemover: planRemover || (() => events.push('plan:remove')),
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
  return {
    root,
    runtime,
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
    const started = await harness.runtime.start({ profileId: 'thought-core-v0', options: {} })
    assert.equal(started.result_class, 'ready')
    assert.equal(calls.length, 1)
    assert.equal(calls[0].validatedAuthority, authority)
    assert.equal(calls[0].compiled.document.schema_version, 'launcher_private_service_plans.v1')
    assert.ok(harness.events.indexOf('plan:compile') < harness.events.indexOf('store:start'))
    assert.ok(harness.runtime.probeExecutor)
    const stopped = await harness.runtime.stop({ profileId: 'thought-core-v0', options: {} })
    assert.equal(stopped.result_class, 'stopped')
    assert.equal(harness.runtime.probeExecutor, null)
  } finally {
    harness.cleanup()
  }
})

test('an in-flight duplicate joins without a second worker exchange', async () => {
  let releaseFirst
  let firstSeen
  const firstGate = new Promise((resolve) => { firstSeen = resolve })
  const releaseGate = new Promise((resolve) => { releaseFirst = resolve })
  let gated = false
  const harness = makeHarness({
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
    const duplicate = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(duplicate.result_class, 'joined_existing')
    assert.equal(harness.workers.length, 1)
    assert.equal(harness.workers[0].requests.length, 1)
    releaseFirst()
    assert.equal((await first).result_class, 'ready')
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
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: {} })
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

test('optional camera plans may be absent without worker exchange', async () => {
  const harness = makeHarness({ includedServiceIds: requiredOwnedIds })
  try {
    const result = await harness.runtime.start({ profileId: 'thought-core-v0', options: canonicalOptions })
    assert.equal(result.result_class, 'ready')
    const states = new Map(result.operation.services.map((service) => [service.service_id, service.state]))
    assert.equal(states.get('mediapipe_camera_hub_stack'), 'optional_absent')
    assert.equal(states.get('vision_snapshot_processor'), 'optional_absent')
    const optionalRequests = harness.workers[0].requests.filter((request) =>
      ['mediapipe_camera_hub_stack', 'vision_snapshot_processor'].includes(request.service_id)
    )
    assert.deepEqual(optionalRequests, [])
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

test('failed initial worker or plan cleanup cannot become clear through a fresh cleanup worker', async () => {
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
      assert.equal(result.operation.phase, 'recovering', failureMode)
      assert.equal(result.operation.cleanup, 'unknown', failureMode)
      assert.equal(result.operation.recovery_required, true, failureMode)
      assert.notEqual(harness.runtime.supervisorLease, null, failureMode)
      assert.equal(harness.events.includes('store:recovery_completed'), false, failureMode)
      assert.equal(harness.events.includes('store:lease:release'), false, failureMode)
      assert.equal(harness.workers.length, 1, failureMode)
      assert.equal(harness.workers[0].requests.filter((request) => request.action === 'stop').length, 0, failureMode)
      assert.equal(result.operation.services
        .filter((service) => allOwnedIds.includes(service.service_id))
        .some((service) => service.state === 'ready'), false, failureMode)
      assert.deepEqual(result.operation.residue_service_ids, [], failureMode)
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
    'cleanup',
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
})

const reducerFixture = () => ({
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
    makeFile('organs/action/home-assistant-server/.env', 'HOME_CONTROL_API_TOKEN=0123456789abcdef\nENVIRONMENT_API_TOKEN=fedcba9876543210\n')
    const liveConfig = makeFile('local/env/home-control.live.yaml', 'profile: local\n')
    makeFile('organs/environment/vision-snapshot-processor/src/vision_snapshot_processor/main.py')
    const nextEntrypoint = makeFile('organs/expression/aituber-kit/node_modules/next/dist/bin/next')
    fs.mkdirSync(executableRoot, { recursive: true })
    const executables = Object.fromEntries(['uv', 'node', 'pwsh'].map((name) => [name, makeFile(`bin/${name}.exe`)]))
    const compiled = compilePrivateServicePlan({
      repositoryRoot: ROOT,
      workspaceRoot: workspace,
      privateRuntimeRoot: path.join(workspace, 'state'),
      profileId: 'thought-core-v0',
      options: { ...canonicalOptions, HomeControlConfigPath: liveConfig },
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
      nonceFactory: () => '00112233445566778899aabbccddeeff'
    })
    assert.equal(compiled.document.services.some((service) => service.service_id === 'voicevox'), false)
    const aituberPlan = compiled.document.services.find((service) => service.service_id === 'aituber_kit')
    assert.equal(path.basename(aituberPlan.file_path), 'node.exe')
    assert.equal(aituberPlan.arguments[0], nextEntrypoint)
    assert.deepEqual(aituberPlan.arguments.slice(1), [
      'dev', '--hostname', canonicalOptions.AituberHost, '--port', String(canonicalOptions.AituberPort)
    ])
    assert.deepEqual(
      compiled.included_service_ids,
      [...compiled.included_service_ids].sort()
    )
    assert.throws(
      () => compilePrivateServicePlan({
        repositoryRoot: ROOT,
        workspaceRoot: workspace,
        privateRuntimeRoot: path.join(workspace, 'state'),
        profileId: 'thought-core-v0',
        options: { ...canonicalOptions, HomeControlConfigPath: liveConfig, VoicevoxReadyTimeoutSeconds: 46 },
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
        nonceFactory: () => '00112233445566778899aabbccddeeff'
      }),
      (error) => error?.code === 'private_plan_config_invalid'
    )
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true })
  }
})
