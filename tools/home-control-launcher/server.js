/**
 * Sword System Launcher の HTTP 合成ルート。
 *
 * 読み順は ARCHITECTURE.md を参照する。ここは利用者/UI/APIの入口であり、
 * lifecycle の意味は launcher-supervisor-runtime.js と reducer が所有する。
 * service plan、永続状態、worker実行をこのファイルだけで推測しないこと。
 */

const childProcess = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const http = require('http')
const https = require('https')
const net = require('net')
const os = require('os')
const path = require('path')
const { TextDecoder } = require('node:util')
const {
  assertLauncherRuntimeAlignment,
  loadContract: loadOrdinaryRouteContract,
  publicContractPayload: ordinaryRouteContractPayload
} = require('./ordinary-route-contract')
const {
  LauncherSupervisorRuntime
} = require('./launcher-supervisor-runtime')
const {
  LauncherProbeRuntimeContext
} = require('./launcher-probe-runtime-context')
const { buildLauncherSurfaceCatalog } = require('./launcher-surface-catalog')
const {
  projectLauncherServiceStatus
} = require('./launcher-public-status-projection')
const {
  createLauncherCameraAdapter,
  redactCameraSelectionArgument,
  redactCameraSelectionInCommandText,
  sanitizeVideoInputCaptureName,
  sanitizeVideoInputDeviceName,
  sanitizeVideoInputSelectionKey,
  withoutLocalCameraSelection
} = require('./launcher-camera-adapter')
const {
  deriveEffectiveConfigIdentity,
  expectedEventJournalDirectory
} = require('./launcher-private-service-plan')
const {
  membershipOptionDefaults,
  selectedServiceIdsForOptions,
  validateProfileRecords
} = require('./launcher-service-selection')

const args = process.argv.slice(2)

const readArg = (name, fallback) => {
  const index = args.indexOf(name)
  if (index >= 0 && args[index + 1]) {
    return args[index + 1]
  }
  return fallback
}

const parseIntArg = (name, fallback) => {
  const value = Number(readArg(name, fallback))
  return Number.isInteger(value) ? value : fallback
}

const PROJECT_ROOT = path.resolve(__dirname, '..', '..')
const DEFAULT_WORKSPACE_ROOT = path.resolve(PROJECT_ROOT, '..')
const WORKSPACE_ROOT = path.resolve(
  readArg(
    '--workspace',
    process.env.HOME_CONTROL_WORKSPACE_ROOT || DEFAULT_WORKSPACE_ROOT
  )
)
const HOST = readArg(
  '--host',
  process.env.HOME_CONTROL_LAUNCHER_HOST || '127.0.0.1'
)
const PORT = parseIntArg(
  '--port',
  Number(process.env.HOME_CONTROL_LAUNCHER_PORT || 8799)
)
const ALLOW_REMOTE =
  args.includes('--allow-remote') ||
  process.env.HOME_CONTROL_LAUNCHER_ALLOW_REMOTE === 'true'
const OPEN_BROWSER =
  args.includes('--open-browser') ||
  process.env.HOME_CONTROL_LAUNCHER_OPEN_BROWSER === 'true'
const PORT_MODE = readArg(
  '--port-mode',
  process.env.HOME_CONTROL_LAUNCHER_PORT_MODE || 'manifest_default'
)

const OPENAI_BROKER_PORT_BY_MODE = {
  manifest_default: 18786,
  isolated_override: 18886
}
if (!Object.prototype.hasOwnProperty.call(OPENAI_BROKER_PORT_BY_MODE, PORT_MODE)) {
  throw new Error('invalid_port_mode')
}
const OPENAI_BROKER_PORT =
  OPENAI_BROKER_PORT_BY_MODE[PORT_MODE]

const PUBLIC_DIR = path.join(__dirname, 'public')
const PROFILE_FILE = path.join(__dirname, 'config', 'default-profiles.json')
const PROFILE_MANIFEST_DIR = path.join(PROJECT_ROOT, 'ops', 'manifests', 'profiles')
const OPS_SCRIPT_ROOT = path.join(PROJECT_ROOT, 'ops', 'scripts')
const COMMON_SCRIPT = path.join(PROJECT_ROOT, 'scripts', 'common.ps1')
const STATE_DIR = resolveStackStateDir()
const LOG_DIR = path.join(STATE_DIR, 'logs')
const PID_FILE = path.join(STATE_DIR, 'pids.json')
const LAUNCHER_CONFIG_FILE = path.join(STATE_DIR, 'launcher-config.json')
const LAUNCHER_STATE_FILE = path.join(STATE_DIR, 'launcher-state.json')
const DEMO_SAFE_SETTINGS_FILE = path.join(STATE_DIR, 'demo-safe-settings.json')
const STACK_LOG_FILE = path.join(LOG_DIR, 'launcher-stack.log')
const STACK_LOG_MAX_BYTES = Number(
  process.env.HOME_CONTROL_LAUNCHER_STACK_LOG_MAX_BYTES || 5 * 1024 * 1024
)
const STACK_LOG_BACKUPS = Number(
  process.env.HOME_CONTROL_LAUNCHER_STACK_LOG_BACKUPS || 3
)
const STOP_VERIFY_TIMEOUT_MS = Number(
  process.env.HOME_CONTROL_LAUNCHER_STOP_VERIFY_TIMEOUT_MS || 12000
)
const STOP_VERIFY_INTERVAL_MS = Number(
  process.env.HOME_CONTROL_LAUNCHER_STOP_VERIFY_INTERVAL_MS || 600
)
const DEFAULT_MODE_ID = 'thought-core-v0'
const ORDINARY_ROUTE_MODE_ID = 'full-system-v0'
const MAX_PROFILE_BYTES = 64 * 1024
const ORDINARY_ROUTE_CONTRACT = loadOrdinaryRouteContract()
const ORDINARY_ROUTE_PUBLIC_SURFACES = ORDINARY_ROUTE_CONTRACT.public_surfaces
const isTemporaryTestPath = (target) => {
  const relative = path.relative(path.resolve(os.tmpdir()), path.resolve(target))
  return Boolean(relative) &&
    relative !== '..' &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
}
const TEST_FAKE_SUPERVISOR =
  process.env.NODE_ENV === 'test' &&
  process.env.HOME_CONTROL_LAUNCHER_TEST_FAKE_SUPERVISOR === 'deterministic_v1' &&
  !ALLOW_REMOTE &&
  isTemporaryTestPath(WORKSPACE_ROOT) &&
  isTemporaryTestPath(STATE_DIR)
let deterministicTestClearFailurePending =
  TEST_FAKE_SUPERVISOR &&
  process.env.HOME_CONTROL_LAUNCHER_TEST_FAKE_FAILURE === 'clear_terminal_once'

const deterministicTestWorker = () => ({
  async execute (request) {
    const common = {
      schema_version: 'launcher_worker.v2',
      message_type: 'result',
      operation_id: request.operation_id,
      supervisor_generation: request.supervisor_generation,
      authority_lease_proof: request.authority_lease_proof,
      dispatch_id: request.dispatch_id,
      service_id: request.service_id,
      action: request.action,
      expected_revision: request.expected_revision,
      worker_nonce: request.worker_nonce
    }
    if (request.action === 'start') {
      if (deterministicTestClearFailurePending) {
        deterministicTestClearFailurePending = false
        return {
          ...common,
          result_class: 'listener_mismatch',
          ownership_class: 'mismatch',
          listener_class: 'mismatch',
          descendant_class: 'foreign'
        }
      }
      return {
        ...common,
        result_class: 'accepted',
        ownership_class: 'matched',
        listener_class: 'not_applicable',
        descendant_class: 'owned_active'
      }
    }
    if (request.action === 'probe') {
      const external = request.service_id === 'voicevox'
      return {
        ...common,
        result_class: external ? 'external_ready' : 'ready',
        ownership_class: external ? 'not_applicable' : 'matched',
        listener_class: 'matched',
        descendant_class: external ? 'not_applicable' : 'owned_active'
      }
    }
    return {
      ...common,
      result_class: 'stopped',
      ownership_class: 'matched',
      listener_class: 'not_applicable',
      descendant_class: 'owned_clear'
    }
  },
  async close () {}
})

const DETERMINISTIC_TEST_PROBE_CONFIG_SHA256 = '0'.repeat(64)
const deterministicTestProbeExecutor = Object.freeze({
  configSha256: DETERMINISTIC_TEST_PROBE_CONFIG_SHA256,
  async execute (expected) {
    const descriptor = launcherRuntime.authority.probeDocument.descriptors.find((candidate) => (
      candidate.service_id === expected.service_id && candidate.probe_id === expected.probe_id
    ))
    if (!descriptor) throw new Error('deterministic_probe_descriptor_missing')
    return Object.freeze({
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
    })
  }
})

const launcherRuntimeOptions = {
  repositoryRoot: PROJECT_ROOT,
  workspaceRoot: WORKSPACE_ROOT,
  privateRuntimeRoot: STATE_DIR,
  probeExecutorFactory: (contextOptions) =>
    new LauncherProbeRuntimeContext(contextOptions)
}
if (TEST_FAKE_SUPERVISOR) {
  Object.assign(launcherRuntimeOptions, {
    planCompiler: ({ authority, configIdentity }) => ({
      document: {
        schema_version: 'launcher_private_service_plans.v1',
        graph_sha256: authority.identities.graphSha256,
        binding_sha256: authority.identities.bindingSha256,
        profile_id: configIdentity.profile_id,
        effective_config_sha256: configIdentity.effective_config_sha256,
        camera_policy: configIdentity.camera_policy,
        services: [{
          service_id: 'thought_core_api',
          environment: {
            THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: '1',
            THOUGHT_CORE_EVENT_JOURNAL_ENABLED: '1',
            THOUGHT_CORE_EVENT_JOURNAL_DIR: expectedEventJournalDirectory(STATE_DIR)
          }
        }]
      },
      powershell_path: process.execPath,
      private_plan_sha256: '1'.repeat(64),
      worker_executable_class: 'windows_powershell_system32',
      worker_executable_sha256: '2'.repeat(64),
      included_service_ids: authority.graph.services
        .filter((service) => service.ownership === 'owned')
        .map((service) => service.service_id)
    }),
    planWriter: () => 'test-private-plan',
    planRemover: () => {},
    workerFactory: deterministicTestWorker,
    probeExecutor: deterministicTestProbeExecutor,
    probeExecutorFactory: null,
    operationIdFactory: (() => {
      let operation = 0
      return () => {
        operation += 1
        return `lop_testfake${String(operation).padStart(16, '0')}`
      }
    })(),
    workerNonceFactory: (() => {
      let nonce = 0
      return () => {
        nonce += 1
        return `lw_testfake${String(nonce).padStart(16, '0')}`
      }
    })()
  })
}
const launcherRuntime = new LauncherSupervisorRuntime(launcherRuntimeOptions)
const DEFAULT_HOME_CONTROL_LIVE_CONFIG = path.join(
  WORKSPACE_ROOT,
  'local',
  'env',
  'home-control.live.yaml'
)
const DEMO_SAFE_DEFAULTS_CANDIDATES = [
  path.join(WORKSPACE_ROOT, 'manifests', 'demo-safe-settings', 'defaults.json'),
  path.join(PROJECT_ROOT, '..', '..', 'manifests', 'demo-safe-settings', 'defaults.json')
]
const PRODUCT_ROOT_CANDIDATES = [
  WORKSPACE_ROOT,
  path.join(PROJECT_ROOT, '..', '..')
]

function resolveStackStateDir() {
  const configured = readArg(
    '--state-dir',
    process.env.HOME_CONTROL_STACK_STATE_DIR || ''
  )
  if (!configured.trim()) {
    return path.join(WORKSPACE_ROOT, '.cache', 'home-control-stack')
  }
  if (path.isAbsolute(configured)) {
    return configured
  }
  return path.join(WORKSPACE_ROOT, configured)
}

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml; charset=utf-8'
}

// ─────────────────────────────────────────────────────────────
// 設定の枝: profile既定値、型、保存済みconfig identity
// lifecycleを直接進めず、Startへ渡す確定入力を作る。
// ─────────────────────────────────────────────────────────────
const PORT_MODE_OPTIONS = {
  isolated_override: {
    HomeAssistantBridgePort: 18887,
    EnvironmentStatePort: 18890,
    MediapipePort: 18865,
    MediapipeBrowserMonitorPort: 18870,
    VisionSnapshotProcessorPort: 18876,
    AituberPort: 18880,
    TouchDesignerGuiPort: 18889,
    ThoughtCorePort: 18888
  }
}

const DEFAULT_OPTIONS = {
  HomeAssistantBridgeHost: '127.0.0.1',
  HomeAssistantBridgePort: 8787,
  EnvironmentStatePort: 8790,
  MediapipePort: 8765,
  MediapipeBrowserMonitorPort: 8770,
  VisionSnapshotProcessorPort: 8776,
  AituberHost: '127.0.0.1',
  AituberPort: 3000,
  TouchDesignerGuiHost: '127.0.0.1',
  TouchDesignerGuiPort: 8788,
  ThoughtCoreHost: '127.0.0.1',
  ThoughtCorePort: 18787,
  OpenAIBrokerPort: OPENAI_BROKER_PORT,
  OpenAIBrokerRequestBudget: 64,
  ThoughtCoreLlmProvider: 'configured',
  VoicevoxReadyTimeoutSeconds: 45,
  MediapipeReadyTimeoutSeconds: 90,
  VoicevoxUrl: '',
  HomeControlConfigPath: '',
  MediapipeMode: 'mediamtx',
  // The ordinary Launcher route has no tracked camera selection. A local
  // operator selection is persisted under the launcher state directory.
  MediapipeCameraName: '',
  MediapipeCameraSelectionKey: '',
  MediapipeCameraWidth: 1920,
  MediapipeCameraHeight: 1080,
  MediapipeCameraFps: 30,
  MediapipeCameraInputCodec: 'mjpeg',
  MediapipeOpenBrowser: false,
  MediapipeNoBrowser: true,
  MediapipePythonGui: false,
  SkipVoicevoxCheck: false,
  SkipHomeAssistantBridge: false,
  SkipEnvironmentState: false,
  SkipMediapipe: false,
  SkipVisionSnapshotProcessor: false,
  SkipAituber: false,
  SkipTouchDesignerGui: false,
  EnableThoughtCore: false,
  EnableThoughtCoreWatch: false,
  ThoughtCoreNoProvider: false,
  StopExisting: true,
  EnableHomeControlFaultInjection: false,
  ...(PORT_MODE_OPTIONS[PORT_MODE] || {})
}

const NUMBER_FIELDS = new Set([
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'MediapipeBrowserMonitorPort',
  'VisionSnapshotProcessorPort',
  'AituberPort',
  'TouchDesignerGuiPort',
  'ThoughtCorePort',
  'OpenAIBrokerPort',
  'OpenAIBrokerRequestBudget',
  'VoicevoxReadyTimeoutSeconds',
  'MediapipeReadyTimeoutSeconds',
  'MediapipeCameraWidth',
  'MediapipeCameraHeight',
  'MediapipeCameraFps'
])

const STRING_FIELDS = new Set([
  'HomeAssistantBridgeHost',
  'AituberHost',
  'TouchDesignerGuiHost',
  'ThoughtCoreHost',
  'ThoughtCoreLlmProvider',
  'VoicevoxUrl',
  'HomeControlConfigPath',
  'MediapipeMode',
  'MediapipeCameraName',
  'MediapipeCameraSelectionKey',
  'MediapipeCameraInputCodec'
])

const NUMBER_LIMITS = {
  OpenAIBrokerRequestBudget: { min: 1, max: 64 },
  MediapipeCameraWidth: { min: 160, max: 3840 },
  MediapipeCameraHeight: { min: 120, max: 2160 },
  MediapipeCameraFps: { min: 1, max: 120 }
}

const SWITCH_FIELDS = Object.keys(DEFAULT_OPTIONS).filter(
  (key) => typeof DEFAULT_OPTIONS[key] === 'boolean'
)

const ensureRuntimeDirs = () => {
  fs.mkdirSync(LOG_DIR, { recursive: true })
}

const nowIso = () => new Date().toISOString()

const defaultHomeControlConfigPath = () =>
  fs.existsSync(DEFAULT_HOME_CONTROL_LIVE_CONFIG)
    ? DEFAULT_HOME_CONTROL_LIVE_CONFIG
    : ''

const homeControlConfigProfileFromPath = (configPath) => {
  const normalized = String(configPath || '').replace(/\\/g, '/').toLowerCase()
  const name = path.basename(normalized)
  if (!normalized) return 'unknown'
  if (normalized.endsWith('/local/env/home-control.live.yaml')) return 'local'
  if (name.includes('example') || name.includes('demo')) return 'demo'
  if (name.includes('local') || name.includes('private') || normalized.includes('/local/')) return 'private'
  if (name.includes('generated') || normalized.includes('/.cache/')) return 'generated'
  return 'custom'
}

const compactHomeControlConfigState = (options, healthPayload) => {
  const expectedProfile = options.SkipHomeAssistantBridge
    ? 'skipped'
    : homeControlConfigProfileFromPath(options.HomeControlConfigPath)
  const health = isPlainObject(healthPayload) ? healthPayload : {}
  const activeProfile = typeof health.config_profile === 'string'
    ? health.config_profile
    : 'unknown'
  const lightDemoMappingsPresent = Boolean(health.light_demo_mappings_present)
  const demoMappingsPresent = Boolean(health.demo_mappings_present)
  const liveHomeInvalid = !options.SkipHomeAssistantBridge &&
    expectedProfile === 'local' &&
    lightDemoMappingsPresent
  return {
    expected_profile: expectedProfile,
    active_profile: activeProfile,
    demo_mappings_present: demoMappingsPresent,
    light_demo_mappings_present: lightDemoMappingsPresent,
    live_home_invalid: liveHomeInvalid,
    payload_policy: 'compact_redacted'
  }
}

const rotateStackLogIfNeeded = (incomingBytes = 0) => {
  if (!Number.isFinite(STACK_LOG_MAX_BYTES) || STACK_LOG_MAX_BYTES <= 0) {
    return
  }

  let stat
  try {
    stat = fs.statSync(STACK_LOG_FILE)
  } catch {
    return
  }

  if (stat.size + incomingBytes <= STACK_LOG_MAX_BYTES) {
    return
  }

  const backups = Number.isFinite(STACK_LOG_BACKUPS)
    ? Math.max(0, Math.floor(STACK_LOG_BACKUPS))
    : 3

  if (backups <= 0) {
    fs.rmSync(STACK_LOG_FILE, { force: true })
    return
  }

  for (let index = backups; index >= 1; index -= 1) {
    const from =
      index === 1 ? STACK_LOG_FILE : `${STACK_LOG_FILE}.${index - 1}`
    const to = `${STACK_LOG_FILE}.${index}`

    if (fs.existsSync(to)) {
      fs.rmSync(to, { force: true })
    }
    if (fs.existsSync(from)) {
      fs.renameSync(from, to)
    }
  }
}

const stripAnsiControlSequences = (content) =>
  String(content).replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '')

const appendStackLog = (content) => {
  ensureRuntimeDirs()
  const sanitizedContent = redactCameraSelectionInCommandText(
    stripAnsiControlSequences(
      Buffer.isBuffer(content) ? content.toString('utf8') : content
    )
  )
  const incomingBytes = Buffer.byteLength(sanitizedContent, 'utf8')
  rotateStackLogIfNeeded(incomingBytes)

  fs.appendFileSync(STACK_LOG_FILE, sanitizedContent, 'utf8')
}

const readJsonFile = (filePath, fallback = null) => {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

const readBoundedJsonFile = (filePath, fallback = null) => {
  try {
    const bytes = fs.readFileSync(filePath)
    if (bytes.length > MAX_PROFILE_BYTES) return fallback
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch {
    return fallback
  }
}

const writeJsonFile = (filePath, value) => {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

const readProfiles = () => readBoundedJsonFile(PROFILE_FILE, [])

const readProfileManifest = (profileId) => {
  const requested = String(profileId || '').trim()
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/u.test(requested)) {
    throw new Error('launcher_profile_manifest_invalid')
  }
  const manifest = readBoundedJsonFile(path.join(PROFILE_MANIFEST_DIR, `${requested}.json`), null)
  if (!manifest || manifest.profile_id !== requested || !Array.isArray(manifest.services)) {
    throw new Error('launcher_profile_manifest_invalid')
  }
  return manifest
}

const supportedProfileRecords = () => {
  const defaultProfiles = readProfiles()
  const profileManifests = []
  for (const profile of defaultProfiles) {
    const profileId = String(profile && profile.id || '').trim()
    if (!/^[a-z0-9][a-z0-9-]{0,63}$/u.test(profileId)) {
      throw new Error('launcher_profile_manifest_invalid')
    }
    const manifestPath = path.join(PROFILE_MANIFEST_DIR, `${profileId}.json`)
    if (fs.existsSync(manifestPath)) profileManifests.push(readProfileManifest(profileId))
  }
  try {
    return validateProfileRecords({
      graphProfileId: launcherRuntime.authority.graph.profile_id,
      graphServices: launcherRuntime.authority.graph.services,
      defaultProfiles,
      profileManifests
    })
  } catch {
    throw new Error('launcher_profile_manifest_invalid')
  }
}

const supportedProfileRecord = (profileId) =>
  supportedProfileRecords().find((record) => record.profile.id === profileId) || null

const lifecycleProfileIdFor = (profileId) => {
  const record = supportedProfileRecord(profileId)
  if (!record) throw new Error('unsupported_supervisor_profile')
  return record.manifest.lifecycle_profile_id
}

const compactProfileId = (profileId) => {
  const value = String(profileId || '').trim()
  if (!value) {
    return 'missing'
  }
  const compact = value.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 80)
  return compact || 'invalid'
}

const profileIds = () =>
  readProfiles()
    .map((profile) => String(profile && profile.id || '').trim())
    .filter(Boolean)

const unknownProfilePayload = (profileId) => ({
  ok: false,
  error: 'unknown_profile',
  resultClass: 'blocked_unknown_profile',
  requestedProfileClass: compactProfileId(profileId),
  knownProfileIds: profileIds()
})

const requireKnownProfile = (profileId) => {
  const requested = String(profileId || '').trim()
  if (!requested || !profileIds().includes(requested)) {
    return unknownProfilePayload(profileId)
  }
  return null
}

const supervisorProfileIds = () => supportedProfileRecords().map((record) => record.profile.id)

const unsupportedSupervisorProfilePayload = (profileId) => ({
  ok: false,
  error: 'unsupported_supervisor_profile',
  resultClass: 'blocked_unsupported_supervisor_profile',
  requestedProfileClass: compactProfileId(profileId),
  supportedProfileIds: supervisorProfileIds(),
  raw_private_publication_flags: false
})

const requireSupervisorProfile = (profileId) => {
  const requested = String(profileId || '').trim()
  if (!requested || !supervisorProfileIds().includes(requested)) {
    return unsupportedSupervisorProfilePayload(profileId)
  }
  return null
}

const readLauncherConfig = () =>
  readJsonFile(LAUNCHER_CONFIG_FILE, {
    selectedProfileId: DEFAULT_MODE_ID,
    options: {}
  })

const readLauncherState = () => readJsonFile(LAUNCHER_STATE_FILE, {})

const readPidState = () => readJsonFile(PID_FILE, { processes: [] })

const resolveDemoSafeDefaultsFile = () =>
  DEMO_SAFE_DEFAULTS_CANDIDATES.find((candidate) => fs.existsSync(candidate)) || null

const resolveProductRoot = () =>
  PRODUCT_ROOT_CANDIDATES
    .map((candidate) => path.resolve(candidate))
    .find((candidate) =>
      fs.existsSync(path.join(candidate, 'contracts')) &&
      fs.existsSync(path.join(candidate, 'runtime'))
    ) || path.resolve(PROJECT_ROOT, '..', '..')

const productRoot = () => resolveProductRoot()

const productPathExists = (relativePath) =>
  fs.existsSync(path.join(productRoot(), relativePath))

const productJsonFile = (relativePath, fallback = null) => {
  const filePath = path.join(productRoot(), relativePath)
  return readJsonFile(filePath, fallback)
}

const compactPathRef = (relativePath) =>
  String(relativePath || '')
    .replace(/\\/g, '/')
    .replace(/[^A-Za-z0-9_./:-]/g, '_')
    .slice(0, 160)

const diagnosticSurfaceRow = ({
  id,
  label,
  proofLayer,
  contractPath,
  routePath,
  implementationPath,
  readinessScriptPath = '',
  temporalClass,
  nextRouteClass,
  liveCaptureRequiredForRuntime = false
}) => {
  const expectedPaths = [
    contractPath,
    routePath,
    implementationPath,
    readinessScriptPath
  ].filter(Boolean)
  const missingPaths = expectedPaths.filter((item) => !productPathExists(item))
  const routeMap = routePath ? productJsonFile(routePath, {}) : {}
  const routeCount = Array.isArray(routeMap && routeMap.routes)
    ? routeMap.routes.length
    : 0
  return {
    id,
    label,
    status_class: missingPaths.length === 0
      ? 'source_static_ready_class'
      : 'source_static_hold_class',
    proof_layer: proofLayer,
    temporal_analysis_class: temporalClass,
    route_count: routeCount,
    missing_ref_classes: missingPaths.map(compactPathRef),
    contract_ref_class: compactPathRef(contractPath),
    route_ref_class: compactPathRef(routePath),
    implementation_ref_class: compactPathRef(implementationPath),
    readiness_ref_class: readinessScriptPath ? compactPathRef(readinessScriptPath) : '',
    live_capture_authorized_by_default: false,
    live_capture_required_for_runtime: Boolean(liveCaptureRequiredForRuntime),
    raw_artifact_publication_class: 'raw_artifacts_not_included_by_launcher_summary',
    next_route_class: nextRouteClass
  }
}

const diagnosticSurfacesSummary = () => {
  const surfaces = [
    diagnosticSurfaceRow({
      id: 'audio_input_awareness',
      label: 'Audio input awareness',
      proofLayer: 'audio_awareness_summary_only',
      contractPath: 'contracts/audio_awareness_summary/audio_awareness_summary.v0.schema.json',
      routePath: 'runtime/audio-awareness/audio-awareness-consumer-routes.json',
      implementationPath: 'runtime/audio-awareness/audio-awareness.mjs',
      readinessScriptPath: 'scripts/check-audio-awareness-readiness.ps1',
      temporalClass: 'windowed_audio_energy_and_legacy_vad_summary',
      nextRouteClass: 'reviewed_live_audio_summary_route_required_for_microphone_or_pc_output_capture',
      liveCaptureRequiredForRuntime: true
    }),
    diagnosticSurfaceRow({
      id: 'self_mirror_temporal_motion',
      label: 'Self Mirror temporal motion',
      proofLayer: 'self_mirror_metric_summary_only',
      contractPath: 'contracts/self_mirror_metric_summary/self_mirror_metric_summary.v0.schema.json',
      routePath: 'runtime/visual-motion-analyzer/self-mirror-consumer-routes.json',
      implementationPath: 'runtime/visual-motion-analyzer/src/self_mirror_visual_analyzer/summary.py',
      readinessScriptPath: 'scripts/run-self-mirror-proof.ps1',
      temporalClass: 'roi_window_motion_timeseries_summary',
      nextRouteClass: 'reviewed_browser_self_mirror_capture_route_required_for_runtime_motion_observation',
      liveCaptureRequiredForRuntime: true
    }),
    diagnosticSurfaceRow({
      id: 'projection_visual_display_audio',
      label: 'Projection Visual display/audio',
      proofLayer: 'projection_visual_display_tts_summary_only',
      contractPath: 'contracts/projection_visual_display_audio_summary/projection_visual_display_audio_summary.v0.schema.json',
      routePath: 'runtime/projection-visual-diagnostics/projection-visual-diagnostics-consumer-routes.json',
      implementationPath: 'runtime/projection-visual-diagnostics/README.md',
      temporalClass: 'bubble_tts_unit_status_and_safe_hash_summary',
      nextRouteClass: 'reviewed_projection_visual_runtime_summary_route_required_for_live_bubble_or_tts_claim',
      liveCaptureRequiredForRuntime: false
    }),
    diagnosticSurfaceRow({
      id: 'projection_visual_response_binding',
      label: 'Projection Visual response binding',
      proofLayer: 'projection_visual_receiver_binding_summary_only',
      contractPath: 'contracts/projection_visual_display_audio_summary/projection_visual_display_audio_summary.v0.schema.json',
      routePath: 'runtime/projection-visual-diagnostics/projection-visual-diagnostics-consumer-routes.json',
      implementationPath: 'runtime/projection-visual-diagnostics/README.md',
      temporalClass: 'message_receiver_client_binding_status_summary',
      nextRouteClass: 'reviewed_projection_visual_receiver_binding_runtime_route_required_for_live_response_claim',
      liveCaptureRequiredForRuntime: false
    }),
    diagnosticSurfaceRow({
      id: 'os_display_window_prompt',
      label: 'OS display/window prompt',
      proofLayer: 'os_display_diagnostic_summary_only',
      contractPath: 'contracts/os_display_diagnostic_summary/os_display_diagnostic_summary.v0.schema.json',
      routePath: 'runtime/os-display-diagnostics/os-display-diagnostics-consumer-routes.json',
      implementationPath: 'runtime/os-display-diagnostics/README.md',
      temporalClass: 'window_foreground_prompt_and_warning_class_summary',
      nextRouteClass: 'reviewed_os_display_capture_or_window_metadata_route_required_for_runtime_prompt_observation',
      liveCaptureRequiredForRuntime: true
    })
  ]
  const readyCount = surfaces.filter((row) => row.status_class === 'source_static_ready_class').length
  return {
    schema_version: 'launcher_diagnostic_surfaces.v0',
    summary_class: 'source_static_diagnostic_surface_inventory.v0',
    proof_ceiling: 'source_static_diagnostic_surface_readiness_only',
    product_root_class: productPathExists('contracts/README.md')
      ? 'product_root_resolved'
      : 'product_root_unresolved',
    counts: {
      total: surfaces.length,
      ready: readyCount,
      hold: surfaces.length - readyCount
    },
    lanes: {
      audio_input: 'source_static_field_map_ready_for_reviewed_live_audio_route',
      self_mirror: 'source_static_temporal_motion_field_map_ready_for_reviewed_browser_capture_route',
      startup_speed: 'launcher_startup_timing_summary_runtime_measurement_ready'
    },
    live_capture_default_class: 'disabled',
    live_capture_authorized_by_default: false,
    raw_private_publication_flags: false,
    surfaces,
    non_claims: [
      'no_live_microphone_capture',
      'no_system_audio_capture',
      'no_screen_recording',
      'no_raw_screenshot_or_video_publication',
      'no_browser_visible_avatar_motion_proof',
      'no_user_heard_audio_proof',
      'no_home_assistant_or_home_control_operation',
      'no_release_readiness_or_final_rr003_pass'
    ]
  }
}

const readDemoSafeDefaults = () => {
  const filePath = resolveDemoSafeDefaultsFile()
  const payload = filePath ? readJsonFile(filePath, null) : null
  const rows = Array.isArray(payload && payload.rows) ? payload.rows : []
  return {
    schema_version: payload && payload.schema_version || 'demo_safe_settings.v0',
    settings_class: 'demo_safe_settings',
    tracked_defaults_class: filePath
      ? 'repo_manifest_demo_safe_settings_defaults'
      : 'repo_manifest_demo_safe_settings_defaults_missing',
    local_override_class:
      payload && payload.local_override_class ||
      'launcher_state_dir_gitignored_demo_settings_json',
    fresh_clone_default_enabled: Boolean(payload && payload.fresh_clone_default_enabled),
    rows
  }
}

const compactDemoSafeId = (id) => {
  const value = String(id || '').trim()
  if (!value) {
    return 'missing'
  }
  return value.replace(/[^A-Za-z0-9_.-]/g, '_').slice(0, 96) || 'invalid'
}

const toBool = (value, fallback = false) =>
  typeof value === 'boolean' ? value : fallback

const toBoundedInt = (value, fallback, min = 0, max = 3600) => {
  const numberValue = Number(value)
  if (!Number.isInteger(numberValue)) {
    return fallback
  }
  return Math.max(min, Math.min(max, numberValue))
}

const toStringList = (value, sanitizer = compactDemoSafeId) =>
  Array.isArray(value)
    ? value
      .map((item) => sanitizer(item))
      .filter((item) => item && item !== 'missing' && item !== 'invalid')
    : []

const readDemoSafeOverrideMap = () => {
  const payload = readJsonFile(DEMO_SAFE_SETTINGS_FILE, {})
  const rows = payload && payload.rows && typeof payload.rows === 'object'
    ? payload.rows
    : {}
  return rows
}

const normalizeDemoSafeRow = (row, override = {}) => {
  const restoreSupported = toBool(row.restore_supported, false)
  return {
    id: compactDemoSafeId(row.id),
    area: compactDemoSafeId(row.area || 'general'),
    label: String(row.label || row.id || 'Demo-safe row'),
    description: String(row.description || ''),
    enabled: toBool(override.enabled, toBool(row.enabled, false)),
    restore_supported: restoreSupported,
    restore_required: restoreSupported
      ? toBool(override.restore_required, toBool(row.restore_required, false))
      : false,
    max_action_count: toBoundedInt(
      override.max_action_count,
      toBoundedInt(row.max_action_count, 0, 0, 25),
      0,
      25
    ),
    max_duration_sec: toBoundedInt(
      override.max_duration_sec,
      toBoundedInt(row.max_duration_sec, 0, 0, 3600),
      0,
      3600
    ),
    action_ids: toStringList(row.action_ids),
    feedback_stimulus_class: compactDemoSafeId(row.feedback_stimulus_class || 'not_applicable'),
    state_requirement_class: compactDemoSafeId(row.state_requirement_class || 'not_applicable'),
    timing_estimate_sec: toBoundedInt(row.timing_estimate_sec, 0, 0, 3600),
    timing_estimate_source_class: compactDemoSafeId(
      row.timing_estimate_source_class || 'not_applicable'
    ),
    measurement_required: toBool(row.measurement_required, false),
    source_class: compactDemoSafeId(row.source_class || 'source_static'),
    proof_ceiling: compactDemoSafeId(row.proof_ceiling || 'source_static_readiness'),
    does_not_prove: Array.isArray(row.does_not_prove)
      ? row.does_not_prove.map((item) => compactDemoSafeId(item))
      : [],
    hold_classes: Array.isArray(row.hold_classes)
      ? row.hold_classes.map((item) => compactDemoSafeId(item))
      : []
  }
}

const effectiveDemoSafeSettings = () => {
  const defaults = readDemoSafeDefaults()
  const overrides = readDemoSafeOverrideMap()
  const rows = defaults.rows.map((row) =>
    normalizeDemoSafeRow(row, overrides[compactDemoSafeId(row.id)] || {})
  )
  const enabled = rows.filter((row) => row.enabled)
  const defaultRowsAllOff = defaults.rows.every((row) => !toBool(row.enabled, false))
  return {
    schema_version: defaults.schema_version,
    settings_class: defaults.settings_class,
    rows,
    summary: {
      total: rows.length,
      enabled: enabled.length,
      enabled_appliance: enabled.filter((row) => row.area === 'appliance').length,
      enabled_readiness: enabled.filter((row) => row.area !== 'appliance').length,
      all_enabled_default_false:
        defaults.fresh_clone_default_enabled === false && defaultRowsAllOff
    },
    persistence: {
      tracked_defaults_class: defaults.tracked_defaults_class,
      local_override_class: defaults.local_override_class,
      local_override_present: fs.existsSync(DEMO_SAFE_SETTINGS_FILE),
      raw_path_publication: false
    }
  }
}

const saveDemoSafeSettings = (settings = {}) => {
  const inputRows = Array.isArray(settings.rows)
    ? settings.rows
    : Array.isArray(settings)
      ? settings
      : []
  const overrideById = new Map(
    inputRows.map((row) => [compactDemoSafeId(row.id), row])
  )
  const defaults = readDemoSafeDefaults()
  const rows = {}
  for (const row of defaults.rows) {
    const id = compactDemoSafeId(row.id)
    const override = overrideById.get(id) || {}
    const normalized = normalizeDemoSafeRow(row, override)
    rows[id] = {
      enabled: normalized.enabled,
      restore_required: normalized.restore_required,
      max_action_count: normalized.max_action_count,
      max_duration_sec: normalized.max_duration_sec
    }
  }
  writeJsonFile(DEMO_SAFE_SETTINGS_FILE, {
    schema_version: 'demo_safe_settings.local.v0',
    settings_class: 'demo_safe_settings',
    updated_at: nowIso(),
    rows
  })
  return effectiveDemoSafeSettings()
}

const readinessSourceForDemoSafeRow = (row, statusPayload) => {
  const services = statusPayload && statusPayload.services || {}
  const serviceState = (name) => String(services[name] && services[name].state || '').toUpperCase()
  if (row.id === 'audio.voicevox_local_speech') {
    return serviceState('voicevox')
  }
  if (row.id === 'avatar.aituber_projection_surface') {
    return serviceState('aituber_kit')
  }
  if (row.id === 'avatar.expression_or_motion_request') {
    return serviceState('thought_core_api')
  }
  if (row.id === 'display.projection_visual_mode') {
    return serviceState('touchdesigner_control_gui') || serviceState('aituber_kit')
  }
  if (row.area === 'appliance') {
    return serviceState('home_assistant_bridge')
  }
  return ''
}

const demoReadinessStatus = (settings, statusPayload) => ({
  schema_version: 'demo_readiness_status.v0',
  status_class: 'demo_readiness_status',
  rows: (settings.rows || []).map((row) => {
    const sourceState = readinessSourceForDemoSafeRow(row, statusPayload)
    const ok = sourceState === 'OK' || sourceState === 'OK_EXTERNAL'
    return {
      id: row.id,
      status_class: sourceState
        ? ok ? 'ready_class' : 'not_ready_class'
        : 'not_checked_class',
      source_class: row.source_class,
      proof_ceiling: row.proof_ceiling,
      does_not_prove: row.does_not_prove || [],
      last_checked_class: statusPayload && statusPayload.timestamp
        ? 'current_launcher_status_timestamp'
        : 'not_checked'
    }
  })
})

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const normalizeIpAddress = (value) => {
  const normalized = String(value || '')
    .trim()
    .toLowerCase()
  return normalized.startsWith('::ffff:')
    ? normalized.slice('::ffff:'.length)
    : normalized
}

const isLoopbackHost = (host) => {
  const normalized = String(host || '').toLowerCase()
  return (
    normalized === 'localhost' ||
    normalized === '::1' ||
    normalized === '[::1]' ||
    normalized === '127.0.0.1' ||
    normalized.startsWith('127.')
  )
}

const isLoopbackAddress = (address) => {
  const normalized = normalizeIpAddress(address)
  return normalized === '' || isLoopbackHost(normalized)
}

const getRemoteAddress = (request) =>
  normalizeIpAddress(
    process.env.NODE_ENV === 'test' &&
      process.env.HOME_CONTROL_LAUNCHER_TEST_REMOTE_ADDRESS
      ? process.env.HOME_CONTROL_LAUNCHER_TEST_REMOTE_ADDRESS
      : request.socket.remoteAddress
  )

const isTrustedOrigin = (request) => {
  const originHeader = request.headers.origin
  if (!originHeader) {
    return true
  }
  if (originHeader === 'null') {
    return false
  }
  try {
    const origin = new URL(originHeader)
    const host = new URL(`http://${request.headers.host || `${HOST}:${PORT}`}`)
    return (
      origin.host === host.host ||
      (isLoopbackHost(origin.hostname) && isLoopbackHost(host.hostname))
    )
  } catch {
    return false
  }
}

const rejectUntrustedRequest = (request, response) => {
  if (!ALLOW_REMOTE && !isLoopbackAddress(getRemoteAddress(request))) {
    sendJson(response, 403, { ok: false, error: 'local_access_required' })
    return true
  }
  if (!isTrustedOrigin(request)) {
    sendJson(response, 403, { ok: false, error: 'untrusted_origin' })
    return true
  }
  return false
}

const readBody = (request) =>
  new Promise((resolve, reject) => {
    const chunks = []
    let total = 0
    request.on('data', (chunk) => {
      total += chunk.length
      if (total > 1024 * 1024) {
        reject(new Error('request_body_too_large'))
        request.destroy()
        return
      }
      chunks.push(chunk)
    })
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw.trim()) {
        resolve({})
        return
      }
      try {
        resolve(JSON.parse(raw))
      } catch {
        reject(new Error('invalid_json'))
      }
    })
    request.on('error', reject)
  })

const normalizeOptions = (profileId, overrides = {}) => {
  const profiles = readProfiles()
  const selectedProfile = profiles.find((profile) => profile.id === profileId)
  const supported = supportedProfileRecord(profileId)
  const membershipDefaults = supported
    ? membershipOptionDefaults({
        graphServices: launcherRuntime.authority.graph.services,
        profileManifest: supported.manifest
      })
    : {}
  const base = {
    ...DEFAULT_OPTIONS,
    ...membershipDefaults,
    ...(selectedProfile ? selectedProfile.options || {} : {}),
    ...(overrides || {})
  }
  if (
    !Number.isInteger(Number(base.OpenAIBrokerPort)) ||
    Number(base.OpenAIBrokerPort) !== OPENAI_BROKER_PORT
  ) {
    throw new Error('invalid_openai_broker_port')
  }
  const normalized = { ...DEFAULT_OPTIONS }
  for (const [key, defaultValue] of Object.entries(DEFAULT_OPTIONS)) {
    const value = base[key]
    if (NUMBER_FIELDS.has(key)) {
      if (key === 'OpenAIBrokerRequestBudget') {
        if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1 || value > 64) {
          throw new Error('invalid_openai_broker_request_budget')
        }
        normalized[key] = value
        continue
      }
      const numberValue = Number(value)
      const limits = NUMBER_LIMITS[key]
      const withinLimits = !limits || (
        numberValue >= limits.min && numberValue <= limits.max
      )
      normalized[key] = Number.isInteger(numberValue) && numberValue > 0 && withinLimits
        ? numberValue
        : defaultValue
      continue
    }
    if (STRING_FIELDS.has(key)) {
      normalized[key] = String(value || '').trim()
      continue
    }
    normalized[key] = Boolean(value)
  }
  if (!['gui', 'camera-hub', 'mediamtx'].includes(normalized.MediapipeMode)) {
    throw new Error('invalid_mediapipe_mode')
  }
  if (!['auto', 'mjpeg'].includes(normalized.MediapipeCameraInputCodec)) {
    normalized.MediapipeCameraInputCodec = DEFAULT_OPTIONS.MediapipeCameraInputCodec
  }
  if (!['configured', 'openai-compatible', 'sword-openai-broker', 'codex-cli', 'codex-cli-luna'].includes(normalized.ThoughtCoreLlmProvider)) {
    normalized.ThoughtCoreLlmProvider = DEFAULT_OPTIONS.ThoughtCoreLlmProvider
  }
  const requestedCameraName = String(
    normalized.MediapipeCameraName || ''
  ).trim()
  normalized.MediapipeCameraName = sanitizeVideoInputDeviceName(
    requestedCameraName
  )
  if (requestedCameraName && !normalized.MediapipeCameraName) {
    throw new Error('invalid_camera_name')
  }
  const requestedCameraSelectionKey = String(
    normalized.MediapipeCameraSelectionKey || ''
  ).trim()
  normalized.MediapipeCameraSelectionKey = sanitizeVideoInputSelectionKey(
    requestedCameraSelectionKey
  )
  if (requestedCameraSelectionKey && !normalized.MediapipeCameraSelectionKey) {
    throw new Error('invalid_camera_selection_key')
  }
  if (normalized.MediapipeOpenBrowser) {
    normalized.MediapipeNoBrowser = false
  }
  if (!normalized.SkipHomeAssistantBridge && !normalized.HomeControlConfigPath) {
    normalized.HomeControlConfigPath = defaultHomeControlConfigPath()
  }
  return normalized
}

const resolveExecutable = (name) => {
  if (!name) {
    return name
  }
  if (path.isAbsolute(name) && fs.existsSync(name)) {
    return name
  }
  const finder = process.platform === 'win32' ? 'where' : 'which'
  try {
    const result = childProcess.spawnSync(finder, [name], {
      encoding: 'utf8',
      windowsHide: true
    })
    if (result.status === 0) {
      const first = String(result.stdout || '')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .find(Boolean)
      if (first) {
        return first
      }
    }
  } catch {
    // Fall back to PATH resolution in child_process.spawn.
  }
  return name
}

const psExecutable = () =>
  resolveExecutable(process.env.HOME_CONTROL_POWERSHELL || 'pwsh')

// ─────────────────────────────────────────────────────────────
// Camera入力の枝: local device名の列挙・選択・redaction
// privateなdevice名をremote/public状態へ出さない。
// ─────────────────────────────────────────────────────────────
const {
  getVideoInputDevicesPayload,
  resolveVideoInputSelectionForStart
} = createLauncherCameraAdapter({
  fileSystem: fs,
  processRunner: childProcess,
  platform: process.platform,
  environment: process.env,
  resolveExecutable,
  readLauncherConfig
})

const quoteArg = (arg) => {
  const value = String(arg)
  if (/^[A-Za-z0-9_./:=@-]+$/.test(value)) {
    return value
  }
  return `"${value.replace(/"/g, '\\"')}"`
}

const formatCommand = (command) => command.map(quoteArg).join(' ')

const publicCommandPreview = (preview) => {
  if (!preview || preview.ok === false) {
    return preview
  }
  const { command, options, ...publicFields } = preview
  const publicOptions = withoutLocalCameraSelection(options)
  return {
    ...publicFields,
    options: publicOptions
  }
}

const withPreservedLocalCameraSelection = (profileId, requestedOptions = {}) => {
  const saved = readLauncherConfig()
  const savedProfileId = saved.selectedProfileId || profileId || DEFAULT_MODE_ID
  const savedOptions = normalizeOptions(savedProfileId, saved.options || {})
  return {
    ...(requestedOptions || {}),
    MediapipeCameraName: savedOptions.MediapipeCameraName,
    MediapipeCameraSelectionKey: savedOptions.MediapipeCameraSelectionKey
  }
}

const previewCommand = (
  profileId,
  optionOverrides = {},
  { resolvedCameraName = '' } = {}
) => {
  const profileError = requireSupervisorProfile(profileId)
  if (profileError) {
    return profileError
  }
  const options = normalizeOptions(profileId, optionOverrides)
  const executionOptions = {
    ...options,
    MediapipeCameraName:
      sanitizeVideoInputCaptureName(resolvedCameraName) || options.MediapipeCameraName
  }
  return {
    ok: true,
    profileId,
    opsProfile: lifecycleProfileIdFor(profileId),
    options: executionOptions,
    demoSafeGate: effectiveDemoSafeSettings().summary,
    command_class: 'node_supervisor',
    execution_authority: 'node_supervisor',
    private_plan_publication_class: 'private_plan_not_exposed',
    raw_private_publication_flags: false
  }
}

const saveConfig = (profileId, options) => {
  const { OpenAIBrokerPort, ...persistedOptions } = options || {}
  const effectiveOptions = normalizeOptions(profileId, persistedOptions)
  const configIdentity = deriveEffectiveConfigIdentity({
    profileId: lifecycleProfileIdFor(profileId),
    options: effectiveOptions,
    authority: launcherRuntime.authority
  })
  const record = {
    schemaVersion: 'launcher_saved_config.v1',
    selectedProfileId: profileId,
    options: persistedOptions,
    effectiveConfigSha256: configIdentity.effective_config_sha256,
    cameraPolicy: configIdentity.camera_policy,
    updatedAt: nowIso()
  }
  writeJsonFile(LAUNCHER_CONFIG_FILE, record)
  return { record, effectiveOptions, configIdentity }
}

const resolveSavedStartConfig = (profileId, expectedConfigSha256) => {
  const saved = readLauncherConfig()
  if (saved.schemaVersion !== 'launcher_saved_config.v1' ||
      saved.selectedProfileId !== profileId ||
      typeof expectedConfigSha256 !== 'string' || !/^[a-f0-9]{64}$/u.test(expectedConfigSha256) ||
      saved.effectiveConfigSha256 !== expectedConfigSha256 ||
      !['required', 'camera_excluded_by_profile'].includes(saved.cameraPolicy)) {
    return { ok: false, error_class: 'saved_config_identity_invalid' }
  }
  let effectiveOptions
  try {
    effectiveOptions = normalizeOptions(profileId, saved.options || {})
  } catch {
    return { ok: false, error_class: 'saved_config_identity_invalid' }
  }
  let configIdentity
  try {
    configIdentity = deriveEffectiveConfigIdentity({
      profileId: lifecycleProfileIdFor(profileId),
      options: effectiveOptions,
      authority: launcherRuntime.authority
    })
  } catch {
    return { ok: false, error_class: 'saved_config_identity_invalid' }
  }
  if (configIdentity.effective_config_sha256 !== saved.effectiveConfigSha256 ||
      configIdentity.camera_policy !== saved.cameraPolicy) {
    return { ok: false, error_class: 'saved_config_identity_mismatch' }
  }
  return { ok: true, effectiveOptions, configIdentity }
}

const operationState = () => launcherRuntime.publicState()

const activeOperationConfigLock = () => {
  const operation = operationState()
  return ['idle', 'stopped'].includes(operation.phase) || launcherRuntime.isClearTerminalFailure()
    ? null
    : {
        ok: false,
        error: 'operation_config_locked',
        resultClass: 'blocked_operation_config_locked',
        operation,
        raw_private_publication_flags: false
      }
}

const runExclusiveStackOperation = async (type, action) => {
  const payload = await action()
  return {
    statusCode: payload && payload.ok === false ? 409 : 200,
    payload: {
      ...payload,
      requested_operation_class: type,
      raw_private_publication_flags: false
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Lifecycle入口: 保存済みconfigをSupervisor Runtimeへ渡す。
// Start/Stopのphase意味はruntime/reducer側がauthority。
// ─────────────────────────────────────────────────────────────
const FIXED_START_FAILURE_CLASSES = new Set([
  'camera_selection_missing',
  'voicevox_unavailable',
  'required_token_missing_or_short',
  'required_port_conflict',
  'dependency_or_tool_missing',
  'first_service_spawn_failed',
  'stack_start_failed_unknown',
  'system_preflight_failed',
  'profile_preflight_failed',
  'delegated_stack_preflight_failed',
  'entrypoint_missing',
  'stack_preflight_failed',
  'stack_config_preflight_failed',
  'previous_stack_preflight_failed',
  'pid_registry_write_failed',
  'launcher_pre_source_failed'
])
const FIXED_START_FAILURE_MARKER = /^SWORD_FIXED_START_FAILURE_CLASS:([a-z_]+)$/
const FIXED_START_MAX_PARTIAL_BYTES = 96
const FIXED_START_MAX_CAPTURE_BYTES = 192
const FIXED_START_MAX_LINES = 127
const FIXED_START_FAILURE_ARTIFACT_ENV = 'SWORD_FIXED_START_FAILURE_FILE'
const FIXED_START_FAILURE_ARTIFACT_MAX_BYTES = 96

const newFixedStartFailureArtifactPath = () => path.join(
  STATE_DIR,
  `.launcher-fixed-start-${process.pid}-${crypto.randomUUID()}.cause`
)

const readFixedStartFailureArtifact = (artifactPath) => {
  let descriptor = null
  try {
    const artifactStat = fs.lstatSync(artifactPath)
    if (
      !artifactStat.isFile() ||
      artifactStat.size < 1 ||
      artifactStat.size > FIXED_START_FAILURE_ARTIFACT_MAX_BYTES
    ) {
      return ''
    }
    descriptor = fs.openSync(artifactPath, 'r')
    const openedStat = fs.fstatSync(descriptor)
    if (
      !openedStat.isFile() ||
      openedStat.size !== artifactStat.size ||
      openedStat.size > FIXED_START_FAILURE_ARTIFACT_MAX_BYTES
    ) {
      return ''
    }
    const value = Buffer.alloc(openedStat.size)
    const bytesRead = fs.readSync(descriptor, value, 0, value.length, 0)
    if (bytesRead !== value.length) {
      return ''
    }
    const failureClass = value.toString('ascii')
    return FIXED_START_FAILURE_CLASSES.has(failureClass) ? failureClass : ''
  } catch {
    return ''
  } finally {
    if (descriptor !== null) {
      try {
        fs.closeSync(descriptor)
      } catch {
        // Reading a diagnostic artifact must not affect supervisor cleanup.
      }
    }
  }
}

const removeFixedStartFailureArtifact = (artifactPath) => {
  try {
    fs.rmSync(artifactPath, { force: true })
  } catch {
    // A fixed diagnostic artifact must never affect stack lifecycle handling.
  }
}

const fixedStartSummary = (failureClass, classificationOrigin, counters) => ({
  schema_version: 'launcher_fixed_start_summary.v1',
  status: 'failed',
  failure_class: failureClass,
  classification_origin: classificationOrigin,
  captured_bytes: counters.totalCaptureBytes,
  captured_lines: counters.lineCount,
  capture_limited: counters.captureLimited,
  proof_ceiling: 'bounded_source_marker_diagnostic_only'
})

const createFixedStartSummaryCollector = ({
  onSummary,
  onClose
}) => {
  const pending = { stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) }
  const discardUntilNewline = { stdout: false, stderr: false }
  let totalCaptureBytes = 0
  let lineCount = 0
  let failureClass = ''
  let captureLimited = false
  let inspectionClosed = false
  let closed = false

  const counters = () => ({ totalCaptureBytes, lineCount, captureLimited })

  const considerLine = (line) => {
    if (failureClass) {
      return true
    }
    if (lineCount >= FIXED_START_MAX_LINES || line.length > FIXED_START_MAX_PARTIAL_BYTES) {
      captureLimited = true
      inspectionClosed = true
      return true
    }
    lineCount += 1
    const match = FIXED_START_FAILURE_MARKER.exec(line.toString('utf8').replace(/\r$/, ''))
    if (match && FIXED_START_FAILURE_CLASSES.has(match[1])) {
      failureClass = match[1]
      inspectionClosed = true
    }
    if (lineCount >= FIXED_START_MAX_LINES) {
      inspectionClosed = true
    }
    return inspectionClosed
  }

  const finishPendingLine = (stream) => {
    if (pending[stream].length > 0) {
      const closedInspection = considerLine(pending[stream])
      pending[stream] = Buffer.alloc(0)
      return closedInspection
    }
    return inspectionClosed
  }

  const finalize = (
    fallbackClass = null,
    fallbackOrigin = 'launcher_fallback',
    artifactFailureClass = ''
  ) => {
    if (closed) {
      return
    }
    closed = true
    finishPendingLine('stdout')
    finishPendingLine('stderr')
    pending.stdout = Buffer.alloc(0)
    pending.stderr = Buffer.alloc(0)
    onClose()
    const artifactClass = FIXED_START_FAILURE_CLASSES.has(artifactFailureClass)
      ? artifactFailureClass
      : ''
    const summaryClass = artifactClass || failureClass || fallbackClass
    if (summaryClass) {
      onSummary(fixedStartSummary(
        summaryClass,
        artifactClass || failureClass ? 'source_marker' : fallbackOrigin,
        counters()
      ))
    }
  }

  return {
    consume: (stream, chunk) => {
      if (closed || !Object.hasOwn(pending, stream)) {
        return
      }
      if (inspectionClosed || totalCaptureBytes >= FIXED_START_MAX_CAPTURE_BYTES) {
        inspectionClosed = true
        if (chunk && chunk.length > 0) {
          captureLimited = true
        }
        return
      }
      if (!Buffer.isBuffer(chunk)) {
        inspectionClosed = true
        captureLimited = Boolean(chunk)
        return
      }
      for (let index = 0; index < chunk.length; index += 1) {
        if (totalCaptureBytes >= FIXED_START_MAX_CAPTURE_BYTES) {
          inspectionClosed = true
          captureLimited = true
          return
        }
        const byte = chunk[index]
        totalCaptureBytes += 1
        if (discardUntilNewline[stream]) {
          if (byte === 0x0a) {
            discardUntilNewline[stream] = false
          }
          continue
        }
        if (byte === 0x0a) {
          if (finishPendingLine(stream)) {
            if (index + 1 < chunk.length) {
              captureLimited = true
            }
            return
          }
          continue
        }
        if (lineCount >= FIXED_START_MAX_LINES || pending[stream].length >= FIXED_START_MAX_PARTIAL_BYTES) {
          pending[stream] = Buffer.alloc(0)
          discardUntilNewline[stream] = true
          captureLimited = true
          continue
        }
        pending[stream] = Buffer.concat([pending[stream], Buffer.from([byte])])
      }
    },
    finalize,
    counters
  }
}

const publicFixedStartDiagnostic = (launcherState) => {
  const summary = launcherState && launcherState.fixedStartSummary
  const allowed = summary && FIXED_START_FAILURE_CLASSES.has(summary.failure_class)
    ? {
        schema_version: 'launcher_fixed_start_summary.v1',
        status: 'failed',
        failure_class: summary.failure_class,
        classification_origin: summary.classification_origin === 'source_marker'
          ? 'source_marker'
          : 'launcher_fallback',
        captured_bytes: Number.isInteger(summary.captured_bytes) ? summary.captured_bytes : 0,
        captured_lines: Number.isInteger(summary.captured_lines) ? summary.captured_lines : 0,
        capture_limited: Boolean(summary.capture_limited),
        proof_ceiling: 'bounded_source_marker_diagnostic_only'
      }
    : null
  return {
    command_class: launcherState && launcherState.command_class === 'home_control_stack'
      ? 'home_control_stack'
      : null,
    fixed_start_summary: allowed
  }
}

const startStack = async (profileId, savedStartConfig) => {
  ensureRuntimeDirs()
  if (!savedStartConfig?.ok) {
    return {
      ok: false,
      schema_version: 'launcher_supervisor_result.v1',
      result_class: 'preflight_failed',
      error_class: savedStartConfig?.error_class || 'saved_config_identity_invalid',
      raw_private_publication_flags: false
    }
  }
  const persistedOptions = savedStartConfig.effectiveOptions
  const cameraSelection = resolveVideoInputSelectionForStart(persistedOptions)
  if (!cameraSelection.ok) {
    return cameraSelection
  }
  const preview = previewCommand(profileId, persistedOptions, {
    resolvedCameraName: cameraSelection.captureName
  })
  if (!preview.ok) {
    return preview
  }
  const result = await launcherRuntime.start({
    profileId: lifecycleProfileIdFor(profileId),
    options: preview.options,
    configIdentity: savedStartConfig.configIdentity
  })
  return result
}

const runPowerShellInlineAndCollect = (script, timeoutMs = 6000) =>
  new Promise((resolve) => {
    const command = [
      psExecutable(),
      '-NoLogo',
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-Command',
      script
    ]
    const child = childProcess.spawn(command[0], command.slice(1), {
      cwd: PROJECT_ROOT,
      windowsHide: true,
      env: {
        ...process.env,
        HOME_CONTROL_WORKSPACE_ROOT: WORKSPACE_ROOT,
        HOME_CONTROL_STACK_STATE_DIR: STATE_DIR,
        NO_COLOR: '1',
        FORCE_COLOR: '0',
        TERM: 'dumb'
      }
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill()
      resolve({
        ok: false,
        timedOut: true,
        commandLine: formatCommand(command.slice(0, -1).concat('<inline>')),
        stdout,
        stderr
      })
    }, timeoutMs)
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString('utf8')
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString('utf8')
    })
    child.on('error', (error) => {
      clearTimeout(timer)
      resolve({
        ok: false,
        commandLine: formatCommand(command.slice(0, -1).concat('<inline>')),
        stdout,
        stderr: `${stderr}${error.message}`
      })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({
        ok: code === 0,
        code,
        commandLine: formatCommand(command.slice(0, -1).concat('<inline>')),
        stdout,
        stderr
      })
    })
  })

const parseJsonArray = (text) => {
  const trimmed = String(text || '').trim()
  if (!trimmed) return []
  try {
    const parsed = JSON.parse(trimmed)
    return Array.isArray(parsed) ? parsed : [parsed]
  } catch {
    return []
  }
}

const normalizedProcessName = (value) =>
  String(value || '').toLowerCase().replace(/\.exe$/, '')

const EXTERNAL_PROCESS_DENY_LIST = new Set([
  'chrome',
  'msedge',
  'firefox',
  'brave',
  'brave-browser',
  'opera',
  'vivaldi',
  'updater',
  'googleupdate',
  'microsoftedgeupdate'
])
const STALE_RECORDED_CLASS = 'stale_or_unowned_registry_entry'
const UNVERIFIED_RECORDED_CLASS = 'unverified_registry_entry'
// The registry is written after Process.Start. Allow only clock/serialization jitter.
const RECORDED_PROCESS_START_AFTER_TOLERANCE_MS = 2000

const SEALED_LISTENER_CLASS_BY_TARGET = {
  home_assistant_bridge: 'home_control_bridge_descendant_listener.v0',
  vision_snapshot_processor: 'vsp_descendant_listener.v0',
  thought_core_api: 'thought_core_descendant_listener.v0'
}
const stopStack = async (body) => {
  const activeOperation = operationState()
  const activeProfileId = activeOperation.operation_id ? activeOperation.profile_id : null
  const explicitlyRequestedProfileId = body && body.profileId
  if (activeProfileId && !explicitlyRequestedProfileId) {
    return launcherRuntime.stop({ profileId: activeProfileId })
  }
  const config = readLauncherConfig()
  const requestedProfileId = explicitlyRequestedProfileId || config.selectedProfileId || DEFAULT_MODE_ID
  const profileError = requireSupervisorProfile(requestedProfileId)
  if (profileError) {
    return profileError
  }
  const lifecycleProfileId = lifecycleProfileIdFor(requestedProfileId)
  if (activeProfileId && lifecycleProfileId !== activeProfileId) {
    return unsupportedSupervisorProfilePayload(requestedProfileId)
  }
  return launcherRuntime.stop({ profileId: activeProfileId || lifecycleProfileId })
}

const reclaimManagedPortsFromLauncher = async (body) => {
  const config = readLauncherConfig()
  const profileId = (body && body.profileId) || config.selectedProfileId || DEFAULT_MODE_ID
  const profileError = requireKnownProfile(profileId)
  if (profileError) {
    return profileError
  }
  return {
    ok: false,
    schema_version: 'launcher_managed_port_reclaim.v1',
    result_class: 'independent_reclaim_retired',
    authority_class: 'node_supervisor',
    profileId,
    operation: launcherRuntime.publicState(),
    kill_authority: false,
    raw_private_publication_flags: false
  }
}

const isProcessAlive = (pid) => {
  const value = Number(pid)
  if (!Number.isInteger(value) || value <= 0) {
    return false
  }
  try {
    process.kill(value, 0)
    return true
  } catch (error) {
    return Boolean(error && error.code === 'EPERM')
  }
}

const checkTcp = (port, host = '127.0.0.1', timeoutMs = 1200) =>
  new Promise((resolve) => {
    if (
      process.env.NODE_ENV === 'test' &&
      process.env.HOME_CONTROL_LAUNCHER_TEST_TCP_INSPECTION_FAILURE === 'true'
    ) {
      resolve({ ok: false, state: 'inspection_failed', detail: 'inspection_failed' })
      return
    }
    const socket = new net.Socket()
    let settled = false
    const finish = (ok, state, detail) => {
      if (settled) {
        return
      }
      settled = true
      socket.destroy()
      resolve({ ok, state, detail })
    }
    socket.setTimeout(timeoutMs)
    socket.once('connect', () => finish(true, 'open', 'listen'))
    socket.once('timeout', () => finish(false, 'inspection_failed', 'inspection_failed'))
    socket.once('error', (error) => {
      if (error && error.code === 'ECONNREFUSED') {
        finish(false, 'closed', 'closed')
        return
      }
      finish(false, 'inspection_failed', 'inspection_failed')
    })
    socket.connect(port, host)
  })

const loopbackHost = (host) =>
  !host || host === '0.0.0.0' || host === 'localhost' ? '127.0.0.1' : host

const managedStopPortTargets = (options) => {
  const mediamtxEnabled = !options.SkipMediapipe && options.MediapipeMode === 'mediamtx'
  return [
    {
      key: 'home_assistant_bridge',
      label: 'Action bridge',
      host: loopbackHost(options.HomeAssistantBridgeHost),
      port: options.HomeAssistantBridgePort,
      enabled: !options.SkipHomeAssistantBridge
    },
    {
      key: 'environment_state_server',
      label: 'Environment state',
      host: '127.0.0.1',
      port: options.EnvironmentStatePort,
      enabled: !options.SkipEnvironmentState
    },
    {
      key: 'mediapipe_camera_hub',
      label: 'Reflex Camera Hub',
      host: '127.0.0.1',
      port: options.MediapipePort,
      enabled: !options.SkipMediapipe
    },
    {
      key: 'mediapipe_browser_monitor',
      label: 'Reflex monitor',
      host: '127.0.0.1',
      port: options.MediapipeBrowserMonitorPort,
      enabled: mediamtxEnabled
    },
    {
      key: 'mediapipe_rtsp',
      label: 'Reflex RTSP',
      host: '127.0.0.1',
      port: 8554,
      enabled: mediamtxEnabled
    },
    {
      key: 'mediapipe_web_media',
      label: 'Reflex web media',
      host: '127.0.0.1',
      port: 8889,
      enabled: mediamtxEnabled
    },
    {
      key: 'vision_snapshot_processor',
      label: 'Vision snapshot',
      host: '127.0.0.1',
      port: options.VisionSnapshotProcessorPort,
      enabled: !options.SkipVisionSnapshotProcessor && !options.SkipMediapipe
    },
    {
      key: 'aituber_kit',
      label: 'Expression runtime',
      host: loopbackHost(options.AituberHost),
      port: options.AituberPort,
      enabled: !options.SkipAituber
    },
    {
      key: 'touchdesigner_control_gui',
      label: 'Display runtime GUI',
      host: loopbackHost(options.TouchDesignerGuiHost),
      port: options.TouchDesignerGuiPort,
      enabled: !options.SkipTouchDesignerGui
    },
    {
      key: 'thought_core_api',
      label: 'Thought Core API',
      host: loopbackHost(options.ThoughtCoreHost),
      port: options.ThoughtCorePort,
      enabled: options.EnableThoughtCore
    }
  ].filter((target) => target.enabled && Number.isInteger(Number(target.port)))
}

const compactPidEntry = (entry) => ({
  name: String(entry.name || 'unknown'),
  module: String(entry.module || ''),
  role: String(entry.role || ''),
  pid: Number(entry.pid) || null
})

let testRecordedProcessInspectionCount = 0

const inspectRecordedProcesses = async (recordedProcesses) => {
  if (process.env.NODE_ENV === 'test') {
    testRecordedProcessInspectionCount += 1
  }
  if (
    process.env.NODE_ENV === 'test' &&
    (
      process.env.HOME_CONTROL_LAUNCHER_TEST_INSPECTION_FAILURE === 'true' ||
      (
        process.env.HOME_CONTROL_LAUNCHER_TEST_INSPECTION_FAILURE_ONCE === 'true' &&
        testRecordedProcessInspectionCount === 1
      )
    )
  ) {
    return { ok: false, processes: new Map() }
  }
  const pids = [...new Set(
    recordedProcesses
      .map((entry) => Number(entry && entry.pid))
      .filter((pid) => Number.isInteger(pid) && pid > 0)
  )]
  if (pids.length === 0) {
    return { ok: true, processes: new Map() }
  }
  const result = await runPowerShellInlineAndCollect(`
$items = @()
foreach ($pidValue in @(${pids.join(',')})) {
  $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
  $items += [pscustomobject]@{
    pid = [int]$pidValue
    alive = ($null -ne $process)
    processName = if ($null -ne $process) { [string]$process.ProcessName } else { '' }
    startedAt = if ($null -ne $process) { ([DateTimeOffset]$process.StartTime).ToString('o') } else { '' }
  }
}
$items | ConvertTo-Json -Compress
`)
  if (!result.ok) {
    return { ok: false, processes: new Map() }
  }
  return {
    ok: true,
    processes: new Map(
      parseJsonArray(result.stdout).map((entry) => [Number(entry.pid), entry])
    )
  }
}

const recordedProcessStartTimeMatches = (entry, process) => {
  const recordedAt = String(entry && entry.started_at || '').trim()
  if (!recordedAt) {
    return false
  }
  const recordedMs = Date.parse(recordedAt)
  const startedMs = Date.parse(String(process && process.startedAt || ''))
  if (!Number.isFinite(recordedMs) || !Number.isFinite(startedMs)) {
    return false
  }
  return startedMs >= recordedMs - 10000 &&
    startedMs <= recordedMs + RECORDED_PROCESS_START_AFTER_TOLERANCE_MS
}

const recordedProcessIsOwned = (entry, process) => {
  if (!process || !process.alive || !recordedProcessStartTimeMatches(entry, process)) {
    return false
  }
  const processName = normalizedProcessName(process.processName)
  if (!processName || EXTERNAL_PROCESS_DENY_LIST.has(processName)) {
    return false
  }
  const allowedNames = Array.isArray(entry && entry.allowed_process_names)
    ? entry.allowed_process_names
    : []
  const allowed = new Set(allowedNames.map(normalizedProcessName))
  return allowed.size > 0 && allowed.has(processName)
}

const safeRecordedEntry = (pid, classification) => ({
  name: classification,
  module: '',
  role: '',
  pid: Number(pid) || null
})

const mergeRecordedEntries = (recordedProcesses, carriedUnverifiedEntries) => {
  const merged = new Map()
  for (const entry of [...recordedProcesses, ...carriedUnverifiedEntries]) {
    const pid = Number(entry && entry.pid)
    if (Number.isInteger(pid) && pid > 0) {
      merged.set(pid, entry)
    }
  }
  return [...merged.values()]
}

const PID_REGISTRY_TOP_LEVEL_KEYS = [
  'processes',
  'schema_version',
  'started_at',
  'workspace_root'
]
const PID_REGISTRY_GENERIC_ENTRY_KEYS = [
  'allowed_process_names',
  'child_process_file',
  'command',
  'module',
  'name',
  'pid',
  'role',
  'started_at',
  'stop_strategy',
  'working_directory'
]
const PID_REGISTRY_SEALED_ENTRY_KEYS = [
  ...PID_REGISTRY_GENERIC_ENTRY_KEYS,
  'expected_module',
  'expected_port',
  'ownership_class',
  'ownership_lineage',
  'ownership_parent_pid',
  'ownership_root_pid',
  'ownership_root_started_at',
  'ownership_seal'
].sort()
const PID_REGISTRY_LINEAGE_KEYS = [
  'parent_pid',
  'pid',
  'process_name',
  'started_at'
]

const hasExactObjectKeys = (value, expected) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value).sort()
  const required = [...expected].sort()
  return actual.length === required.length &&
    actual.every((key, index) => key === required[index])
}

const isNonEmptyString = (value) =>
  typeof value === 'string' && value.trim().length > 0

const isValidRecordedTimestamp = (value) =>
  isNonEmptyString(value) && Number.isFinite(Date.parse(value))

const isValidPid = (value) => Number.isInteger(value) && value > 0

const sealedRegistryEntryIsStructurallyValid = (entry) => {
  if (!hasExactObjectKeys(entry, PID_REGISTRY_SEALED_ENTRY_KEYS)) return false
  if (entry.stop_strategy !== 'role_scoped_descendant') return false
  if (!Object.values(SEALED_LISTENER_CLASS_BY_TARGET).includes(entry.ownership_class)) {
    return false
  }
  if (
    !isValidPid(entry.ownership_root_pid) ||
    !isValidPid(entry.ownership_parent_pid) ||
    !isValidRecordedTimestamp(entry.ownership_root_started_at) ||
    !isNonEmptyString(entry.expected_module) ||
    !Number.isInteger(entry.expected_port) ||
    entry.expected_port < 1 ||
    !Array.isArray(entry.ownership_lineage) ||
    entry.ownership_lineage.length < 1 ||
    !/^[a-f0-9]{64}$/.test(String(entry.ownership_seal || ''))
  ) {
    return false
  }
  for (const row of entry.ownership_lineage) {
    if (
      !hasExactObjectKeys(row, PID_REGISTRY_LINEAGE_KEYS) ||
      !isValidPid(row.pid) ||
      !Number.isInteger(row.parent_pid) ||
      row.parent_pid < 0 ||
      !isNonEmptyString(row.process_name) ||
      !isValidRecordedTimestamp(row.started_at)
    ) {
      return false
    }
  }
  const sealParts = [
    entry.ownership_class,
    entry.pid,
    entry.started_at,
    entry.ownership_parent_pid,
    entry.ownership_root_pid,
    entry.ownership_root_started_at,
    entry.expected_module,
    entry.expected_port,
    ...entry.ownership_lineage.map((row) =>
      `${row.pid}|${row.parent_pid}|${row.process_name}|${row.started_at}`
    )
  ]
  const expectedSeal = crypto
    .createHash('sha256')
    .update(sealParts.join('\n'))
    .digest('hex')
  return expectedSeal === entry.ownership_seal
}

const pidRegistryEntryIsStructurallyValid = (entry) => {
  const sealed = Object.prototype.hasOwnProperty.call(entry || {}, 'ownership_class')
  if (sealed) {
    if (!sealedRegistryEntryIsStructurallyValid(entry)) return false
  } else if (
    !hasExactObjectKeys(entry, PID_REGISTRY_GENERIC_ENTRY_KEYS) ||
    entry.stop_strategy !== 'managed_tree'
  ) {
    return false
  }
  return isNonEmptyString(entry.name) &&
    isNonEmptyString(entry.module) &&
    isNonEmptyString(entry.role) &&
    isValidPid(entry.pid) &&
    isNonEmptyString(entry.working_directory) &&
    typeof entry.command === 'string' &&
    isValidRecordedTimestamp(entry.started_at) &&
    Array.isArray(entry.allowed_process_names) &&
    entry.allowed_process_names.length > 0 &&
    entry.allowed_process_names.every(isNonEmptyString) &&
    typeof entry.child_process_file === 'string'
}

const readExactPidRegistrySnapshot = () => {
  if (!fs.existsSync(PID_FILE)) return null
  try {
    const raw = fs.readFileSync(PID_FILE, 'utf8')
    if (!raw || Buffer.byteLength(raw, 'utf8') > 1024 * 1024) return null
    const state = JSON.parse(raw)
    if (
      !hasExactObjectKeys(state, PID_REGISTRY_TOP_LEVEL_KEYS) ||
      state.schema_version !== 3 ||
      !isValidRecordedTimestamp(state.started_at) ||
      path.resolve(String(state.workspace_root || '')).toLowerCase() !==
        WORKSPACE_ROOT.toLowerCase() ||
      !Array.isArray(state.processes) ||
      state.processes.length < 1 ||
      !state.processes.every(pidRegistryEntryIsStructurallyValid)
    ) {
      return null
    }
    const pids = state.processes.map((entry) => entry.pid)
    if (new Set(pids).size !== pids.length) return null
    return { raw, state }
  } catch {
    return null
  }
}

const exactPidRegistrySnapshotMatches = (expected) => {
  const current = readExactPidRegistrySnapshot()
  return Boolean(current && expected && current.raw === expected.raw)
}

const deadRegistryConvergenceProbe = async (
  snapshot,
  options,
  carriedUnverifiedEntries,
  carriedStaleRecorded
) => {
  if (
    !snapshot ||
    carriedUnverifiedEntries.length > 0 ||
    carriedStaleRecorded.length > 0 ||
    !exactPidRegistrySnapshotMatches(snapshot)
  ) {
    return false
  }
  const inspection = await inspectRecordedProcesses(snapshot.state.processes)
  if (!inspection.ok || inspection.processes.size !== snapshot.state.processes.length) {
    return false
  }
  for (const entry of snapshot.state.processes) {
    const process = inspection.processes.get(entry.pid)
    if (!process || process.alive) return false
  }
  const verification = await collectStackStopVerification(options)
  return verification.pidFileExists &&
    verification.recordedProcessCount === snapshot.state.processes.length &&
    verification.aliveRecorded.length === 0 &&
    verification.staleRecorded.length === 0 &&
    verification.openPorts.length === 0 &&
    verification.portInspectionFailures.length === 0
}

const convergeDeadPidRegistryAfterStop = async (
  snapshot,
  options,
  carriedUnverifiedEntries,
  carriedStaleRecorded
) => {
  for (let probe = 0; probe < 2; probe += 1) {
    if (!await deadRegistryConvergenceProbe(
      snapshot,
      options,
      carriedUnverifiedEntries,
      carriedStaleRecorded
    )) {
      return false
    }
    if (probe === 0) {
      if (
        process.env.NODE_ENV === 'test' &&
        process.env.HOME_CONTROL_LAUNCHER_TEST_DEAD_REGISTRY_DRIFT_AFTER_PROBE === 'true'
      ) {
        fs.appendFileSync(PID_FILE, ' ')
      }
      await sleep(STOP_VERIFY_INTERVAL_MS)
    }
  }
  if (
    process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_DEAD_REGISTRY_DRIFT_BEFORE_UNLINK === 'true'
  ) {
    fs.appendFileSync(PID_FILE, ' ')
  }
  if (!exactPidRegistrySnapshotMatches(snapshot)) return false
  try {
    fs.unlinkSync(PID_FILE)
    return true
  } catch {
    return false
  }
}

const collectStackStopVerification = async (
  options,
  carriedUnverifiedEntries = [],
  carriedStaleRecorded = []
) => {
  const pidFileExists = fs.existsSync(PID_FILE)
  const pidState = readPidState()
  const recordedProcesses = Array.isArray(pidState.processes)
    ? pidState.processes
    : []
  const entriesToVerify = mergeRecordedEntries(recordedProcesses, carriedUnverifiedEntries)
  const inspection = await inspectRecordedProcesses(entriesToVerify)
  const aliveRecorded = []
  const staleRecorded = new Set(carriedStaleRecorded)
  const carriedEntries = []
  for (const entry of entriesToVerify) {
    if (!isProcessAlive(entry.pid)) {
      continue
    }
    if (!inspection.ok) {
      aliveRecorded.push(safeRecordedEntry(entry.pid, UNVERIFIED_RECORDED_CLASS))
      carriedEntries.push(entry)
      continue
    }
    const inspected = inspection.processes.get(Number(entry.pid))
    if (!inspected || !inspected.alive) {
      aliveRecorded.push(safeRecordedEntry(entry.pid, UNVERIFIED_RECORDED_CLASS))
      carriedEntries.push(entry)
      continue
    }
    if (!recordedProcessIsOwned(entry, inspected)) {
      staleRecorded.add(STALE_RECORDED_CLASS)
      continue
    }
    aliveRecorded.push(compactPidEntry(entry))
  }
  const checkedPorts = await Promise.all(
    managedStopPortTargets(options).map(async (target) => ({
      key: target.key,
      label: target.label,
      host: target.host,
      port: Number(target.port),
      tcp: await checkTcp(Number(target.port), target.host, 450)
    }))
  )
  const openPorts = checkedPorts
    .filter((target) => target.tcp && target.tcp.state === 'open')
    .map((target) => ({
      key: target.key,
      label: target.label,
      host: target.host,
      port: target.port,
      detail: target.tcp.detail || 'listen'
    }))
  const portInspectionFailures = checkedPorts
    .filter((target) => !target.tcp || target.tcp.state === 'inspection_failed')
    .map((target) => ({
      key: target.key,
      label: target.label,
      host: target.host,
      port: target.port,
      detail: 'inspection_failed'
    }))
  return {
    ok: !pidFileExists &&
      aliveRecorded.length === 0 &&
      openPorts.length === 0 &&
      portInspectionFailures.length === 0,
    checkedAt: nowIso(),
    pidFileExists,
    recordedProcessCount: recordedProcesses.length,
    aliveRecorded,
    staleRecorded: [...staleRecorded].sort(),
    carriedUnverifiedEntries: carriedEntries,
    checkedPortCount: checkedPorts.length,
    openPorts,
    portInspectionFailures
  }
}

const waitForStackStopVerification = async (
  options,
  carriedUnverifiedEntries = [],
  carriedStaleRecorded = []
) => {
  const deadline = Date.now() + STOP_VERIFY_TIMEOUT_MS
  let verification = await collectStackStopVerification(
    options,
    carriedUnverifiedEntries,
    carriedStaleRecorded
  )
  while (!verification.ok && Date.now() < deadline) {
    await sleep(STOP_VERIFY_INTERVAL_MS)
    verification = await collectStackStopVerification(
      options,
      carriedUnverifiedEntries,
      carriedStaleRecorded
    )
  }
  return {
    ...verification,
    timedOut: !verification.ok
  }
}

const describeStopVerificationFailure = (verification, scriptResult) => {
  const parts = []
  if (!scriptResult || !scriptResult.ok) {
    parts.push('shutdown script failed or timed out')
  }
  if (verification && verification.pidFileExists) {
    parts.push('PID registry still exists')
  }
  if (verification && verification.aliveRecorded && verification.aliveRecorded.length > 0) {
    parts.push(
      `recorded processes still alive: ${verification.aliveRecorded
        .map((entry) => `${entry.name}#${entry.pid}`)
        .join(', ')}`
    )
  }
  if (verification && verification.openPorts && verification.openPorts.length > 0) {
    parts.push(
      `managed ports still listening: ${verification.openPorts
        .map((entry) => `${entry.label}:${entry.port}`)
        .join(', ')}`
    )
  }
  if (
    verification &&
    verification.portInspectionFailures &&
    verification.portInspectionFailures.length > 0
  ) {
    parts.push('managed port inspection failed')
  }
  if (verification && verification.timedOut) {
    parts.push('stop verification timed out')
  }
  return parts.length > 0
    ? `Stop did not fully clear the stack: ${parts.join('; ')}.`
    : 'Stop did not fully clear the stack. Check the launcher log.'
}

const checkHttp = (targetUrl, timeoutMs = 1800) =>
  new Promise((resolve) => {
    const client = targetUrl.startsWith('https:') ? https : http
    const request = client.get(targetUrl, { timeout: timeoutMs }, (response) => {
      response.resume()
      resolve({
        ok: response.statusCode >= 200 && response.statusCode < 500,
        detail: `HTTP ${response.statusCode}`
      })
    })
    request.once('timeout', () => {
      request.destroy()
      resolve({ ok: false, detail: 'timeout' })
    })
    request.once('error', (error) => {
      resolve({ ok: false, detail: error.code || error.message })
    })
  })

// ─────────────────────────────────────────────────────────────
// 公開状態の枝: 各serviceの観測をprivacy-safeな要約へ変換する。
// 観測値はReadyのsemantic authorityではない。
// ─────────────────────────────────────────────────────────────
const skippedProbe = (detail = 'skipped') => ({ ok: false, detail })

const checkTcpIf = (enabled, port, host = '127.0.0.1', timeoutMs = 1200) =>
  enabled ? checkTcp(port, host, timeoutMs) : Promise.resolve(skippedProbe())

const checkHttpIf = (enabled, targetUrl, timeoutMs = 1800) =>
  enabled ? checkHttp(targetUrl, timeoutMs) : Promise.resolve(skippedProbe())

const fetchJsonIf = (enabled, targetUrl, timeoutMs = 1800) =>
  enabled
    ? fetchJson(targetUrl, timeoutMs)
    : Promise.resolve({
        ok: false,
        statusCode: 0,
        detail: 'skipped'
      })

const fetchJson = (targetUrl, timeoutMs = 1800) =>
  new Promise((resolve) => {
    const client = targetUrl.startsWith('https:') ? https : http
    const request = client.get(targetUrl, { timeout: timeoutMs }, (response) => {
      let body = ''
      response.setEncoding('utf8')
      response.on('data', (chunk) => {
        body += chunk
      })
      response.on('end', () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          resolve({
            ok: false,
            statusCode: response.statusCode,
            detail: `HTTP ${response.statusCode}`
          })
          return
        }
        try {
          resolve({
            ok: true,
            statusCode: response.statusCode,
            detail: `HTTP ${response.statusCode}`,
            payload: JSON.parse(body)
          })
        } catch {
          resolve({
            ok: false,
            statusCode: response.statusCode,
            detail: 'invalid JSON'
          })
        }
      })
    })
    request.once('timeout', () => {
      request.destroy()
      resolve({ ok: false, statusCode: 0, detail: 'timeout' })
    })
    request.once('error', (error) => {
      resolve({ ok: false, statusCode: 0, detail: error.code || error.message })
    })
  })

const isPlainObject = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value)

const copyFields = (source, fields) => {
  if (!isPlainObject(source)) {
    return {}
  }
  const result = {}
  for (const field of fields) {
    if (Object.prototype.hasOwnProperty.call(source, field)) {
      result[field] = source[field]
    }
  }
  return result
}

const compactRoomLightSignal = (signal) => {
  if (!isPlainObject(signal)) {
    return null
  }
  const compact = copyFields(signal, [
    'available',
    'stale',
    'state',
    'confidence_label',
    'authority',
    'source',
    'projected_by',
    'source_snapshot_id',
    'observed_at',
    'updated_at',
    'freshness',
    'answer_hint'
  ])
  const evidence = copyFields(signal.evidence, [
    'model',
    'lighting_type',
    'daylight_state',
    'electric_on_probability',
    'daylight_present_probability',
    'dark_probability',
    'confidence_label',
    'observed_at',
    'updated_at'
  ])
  if (Object.keys(evidence).length > 0) {
    compact.evidence = evidence
  }
  return compact
}

const compactSourceStatus = (source) => {
  if (!isPlainObject(source)) {
    return null
  }
  return copyFields(source, [
    'available',
    'stale',
    'status',
    'state',
    'source',
    'updated_at',
    'observed_at',
    'freshness',
    'snapshot_id'
  ])
}

const compactApplianceSignal = (signal) => {
  if (!isPlainObject(signal)) {
    return null
  }
  return copyFields(signal, [
    'state',
    'source',
    'domain',
    'expected_state',
    'action_id',
    'updated_at',
    'stale',
    'freshness'
  ])
}

const compactEnvironmentForLauncherStatus = (indicatorPayload) => {
  if (!isPlainObject(indicatorPayload) || !isPlainObject(indicatorPayload.environment)) {
    return null
  }
  const environment = indicatorPayload.environment
  const appliances = isPlainObject(environment.appliances)
    ? environment.appliances
    : {}
  const stateQueries = isPlainObject(environment.state_queries)
    ? environment.state_queries
    : {}
  const sources = isPlainObject(environment.sources) ? environment.sources : {}
  const vision = isPlainObject(environment.vision) ? environment.vision : {}
  const compact = {
    state_queries: {},
    sources: {},
    vision: {},
    appliances: {}
  }

  for (const applianceId of Object.keys(appliances).sort()) {
    const appliance = compactApplianceSignal(appliances[applianceId])
    if (appliance) {
      compact.appliances[applianceId] = appliance
    }
  }

  const roomLight = compactRoomLightSignal(stateQueries.room_light)
  if (roomLight) {
    compact.state_queries.room_light = roomLight
  }

  for (const sourceId of [
    'vision_snapshot_processor',
    'camera_hub',
    'home_assistant_bridge',
    'home_assistant'
  ]) {
    const sourceStatus = compactSourceStatus(sources[sourceId])
    if (sourceStatus) {
      compact.sources[sourceId] = sourceStatus
    }
  }

  const roomLightVision = compactRoomLightSignal(vision.room_light)
  if (roomLightVision) {
    compact.vision.room_light = roomLightVision
  }

  return compact
}

const shouldExposeEnvironmentStatus = () =>
  !ALLOW_REMOTE && isLoopbackHost(HOST)

const launcherCorsOrigin = () => {
  const fallback = 'http://127.0.0.1:3000'
  const configured = process.env.HOME_CONTROL_LAUNCHER_CORS_ORIGIN || fallback
  try {
    const parsed = new URL(configured)
    return isLoopbackHost(parsed.hostname) ? parsed.origin : fallback
  } catch {
    return fallback
  }
}

const launcherStatusCorsHeaders = () => {
  if (!shouldExposeEnvironmentStatus()) {
    return {}
  }
  return {
    'Access-Control-Allow-Origin': launcherCorsOrigin(),
    Vary: 'Origin',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
  }
}

const checkWebSocketHandshake = (port, host = '127.0.0.1', timeoutMs = 1200) =>
  new Promise((resolve) => {
    const socket = new net.Socket()
    let settled = false
    const finish = (ok, detail) => {
      if (settled) {
        return
      }
      settled = true
      socket.destroy()
      resolve({ ok, detail })
    }
    socket.setTimeout(timeoutMs)
    socket.once('connect', () => {
      const key = crypto.randomBytes(16).toString('base64')
      socket.write(
        [
          'GET / HTTP/1.1',
          `Host: ${host}:${port}`,
          'Upgrade: websocket',
          'Connection: Upgrade',
          `Sec-WebSocket-Key: ${key}`,
          'Sec-WebSocket-Version: 13',
          '',
          ''
        ].join('\r\n')
      )
    })
    socket.once('data', (chunk) => {
      const firstLine = chunk.toString('utf8').split(/\r?\n/)[0] || ''
      finish(firstLine.includes(' 101 '), firstLine.trim() || 'no response')
    })
    socket.once('timeout', () => finish(false, 'timeout'))
    socket.once('error', (error) => finish(false, error.code || error.message))
    socket.connect(port, host)
  })

// N0 parse-only drift anchor for the frozen external graph port:
// let voicevoxPort = 50021
const buildPublicReadinessProjection = (graphServices) => {
  const publicIdsByServiceId = new Map()
  const serviceIdsByPublicId = new Map()
  for (const spec of graphServices) {
    const publicId = spec.public_readiness_id
    if (publicId === null) {
      continue
    }
    const serviceId = spec.service_id
    if (typeof serviceId !== 'string' || !serviceId || typeof publicId !== 'string' || !publicId) {
      throw new Error('launcher_public_readiness_projection_invalid')
    }
    if (publicIdsByServiceId.has(serviceId) || serviceIdsByPublicId.has(publicId)) {
      throw new Error('launcher_public_readiness_projection_duplicate')
    }
    publicIdsByServiceId.set(serviceId, publicId)
    serviceIdsByPublicId.set(publicId, serviceId)
  }
  const requiredProjectionValue = (map, key) => {
    const value = map.get(key)
    if (!value) {
      throw new Error('launcher_public_readiness_projection_unmapped')
    }
    return value
  }
  return Object.freeze({
    publicIdForServiceId: (serviceId) => requiredProjectionValue(publicIdsByServiceId, serviceId),
    serviceIdForPublicId: (publicId) => requiredProjectionValue(serviceIdsByPublicId, publicId)
  })
}

const launcherPublicReadinessProjection = buildPublicReadinessProjection(
  launcherRuntime.authority.graph.services
)

const publicReadinessIdsForServiceIds = (serviceIds) => {
  const publicIds = serviceIds.map((serviceId) =>
    launcherPublicReadinessProjection.publicIdForServiceId(serviceId)
  )
  if (new Set(publicIds).size !== publicIds.length) {
    throw new Error('launcher_public_readiness_projection_duplicate')
  }
  return publicIds
}

const expectedServicesForOptions = (options) => {
  const serviceIds = selectedServiceIdsForOptions({
    graphServices: launcherRuntime.authority.graph.services,
    options
  }).filter((serviceId) =>
    launcherRuntime.authority.graph.services.find((service) => service.service_id === serviceId)
      .public_readiness_id
  )
  const selectedPublicIds = new Set(publicReadinessIdsForServiceIds(serviceIds))
  const publicIds = ORDINARY_ROUTE_CONTRACT.readiness.expected_service_ids.filter((publicId) =>
    selectedPublicIds.has(publicId)
  )
  if (publicIds.length !== selectedPublicIds.size) {
    throw new Error('launcher_public_readiness_projection_unmapped')
  }
  return publicIds
}

const serviceIsReady = (service) => {
  const state = String(service && service.state || '').toUpperCase()
  return state === 'OK' || state === 'OK_EXTERNAL'
}

const effectiveStatusOptions = () => {
  const config = readLauncherConfig()
  return normalizeOptions(config.selectedProfileId || DEFAULT_MODE_ID, config.options || {})
}

const getStatus = async () => {
  const config = readLauncherConfig()
  const selectedProfileId = config.selectedProfileId || DEFAULT_MODE_ID
  const profileConfigState = requireSupervisorProfile(selectedProfileId) || {
    ok: true,
    resultClass: 'known_profile',
    selectedProfileId
  }
  const options = effectiveStatusOptions()
  const supervisor = launcherRuntime.publicState()
  const projectedStatus = projectLauncherServiceStatus({
    supervisor,
    graphServices: launcherRuntime.authority.graph.services,
    expectedServiceIds: expectedServicesForOptions(options),
    serviceIdForPublicId: launcherPublicReadinessProjection.serviceIdForPublicId,
    profileId: selectedProfileId
  })

  return {
    ok: true,
    timestamp: nowIso(),
    workspaceRoot: WORKSPACE_ROOT,
    operation: supervisor,
    profileConfigState,
    services: projectedStatus.services,
    startupTiming: projectedStatus.startupTiming,
    diagnosticSurfaces: diagnosticSurfacesSummary(),
    environment: null,
    environmentIndicatorState: {
      exposed: false,
      payload_policy: 'owned_service_api_not_launcher_public_state',
      ok: false,
      detail: 'not_projected',
      snapshot_id: '',
      stale: true,
      age_ms: null
    },
    homeControlConfigState: compactHomeControlConfigState(
      options,
      null
    )
  }
}

const getState = async ({ includeLocalCameraSelection = true } = {}) => {
  const config = readLauncherConfig()
  const savedProfileId = config.selectedProfileId || DEFAULT_MODE_ID
  const savedProfileState = requireSupervisorProfile(savedProfileId)
  const selectedProfileId = savedProfileState ? DEFAULT_MODE_ID : savedProfileId
  const options = normalizeOptions(selectedProfileId, savedProfileState ? {} : config.options || {})
  const status = await getStatus()
  const demoSafeSettings = effectiveDemoSafeSettings()
  return {
    ok: true,
    projectRoot: PROJECT_ROOT,
    workspaceRoot: WORKSPACE_ROOT,
    stateDir: STATE_DIR,
    portMode: PORT_MODE,
    profiles: readProfiles().filter((profile) => supervisorProfileIds().includes(profile.id)).map((profile) => ({
      ...profile,
      options: includeLocalCameraSelection
        ? profile.options
        : withoutLocalCameraSelection(profile.options)
    })),
    config: {
      selectedProfileId,
      profileState: savedProfileState || {
        ok: true,
        resultClass: 'supported_supervisor_profile',
        requestedProfileClass: selectedProfileId,
        supportedProfileIds: supervisorProfileIds(),
        raw_private_publication_flags: false
      },
      configIdentity: !savedProfileState && config.schemaVersion === 'launcher_saved_config.v1'
        ? {
            profile_id: lifecycleProfileIdFor(selectedProfileId),
            effective_config_sha256: config.effectiveConfigSha256 || null,
            camera_policy: config.cameraPolicy || null
          }
        : null,
      options: includeLocalCameraSelection
        ? options
        : withoutLocalCameraSelection(options)
    },
    launcherState: publicFixedStartDiagnostic(readLauncherState()),
    operation: operationState(),
    status,
    startupTiming: status.startupTiming,
    diagnosticSurfaces: status.diagnosticSurfaces,
    demoSafeSettings,
    demoReadinessStatus: demoReadinessStatus(demoSafeSettings, status),
    endpoints: buildLauncherSurfaceCatalog(
      options,
      selectedServiceIdsForOptions({
        graphServices: launcherRuntime.authority.graph.services,
        options
      })
    )
  }
}

const getStartupTimingPayload = async () => {
  const status = await getStatus()
  return {
    ok: true,
    timestamp: status.timestamp,
    startupTiming: status.startupTiming,
    proof_ceiling: 'launcher_startup_timing_summary_only',
    raw_private_publication_flags: false
  }
}

const demoTimedActionReadiness = async () => {
  const status = await getStatus()
  const config = readLauncherConfig()
  const profileId = config.selectedProfileId || DEFAULT_MODE_ID
  const options = effectiveStatusOptions()
  const timing = status.startupTiming || {}
  const elapsed = Number(timing.elapsedMs)
  const firstResponseTargetMs = 30000
  const firstActionTargetMs = 30000
  const requiredServices = [
    'thought_core_api',
    'aituber_kit',
    'home_assistant_bridge'
  ]
  const missingServices = requiredServices.filter(
    (serviceId) => !serviceIsReady(status.services && status.services[serviceId])
  )
  const actionBridgeHost = options.HomeAssistantBridgeHost === '0.0.0.0'
    ? '127.0.0.1'
    : options.HomeAssistantBridgeHost
  return {
    schema_version: 'launcher_demo_timed_action_readiness.v0',
    profileId,
    route_class: 'demo_fast_action_first_feedback_and_first_action_readiness',
    readiness_class: missingServices.length === 0
      ? 'ready_for_reviewed_first_action_handoff'
      : 'waiting_for_required_services',
    requiredServiceIds: requiredServices,
    missingServiceIds: missingServices,
    target_ms: {
      first_response: firstResponseTargetMs,
      first_action: firstActionTargetMs
    },
    elapsedMs: Number.isFinite(elapsed) ? elapsed : null,
    remaining_ms_to_first_action_target: Number.isFinite(elapsed)
      ? firstActionTargetMs - elapsed
      : null,
    projection_visual_url: `http://127.0.0.1:${options.AituberPort}/projection-visual/`,
    action_operator_url: `http://${actionBridgeHost}:${options.HomeAssistantBridgePort}/operator`,
    reviewed_action_ids: ['aircon_cool', 'aircon_hvac_off'],
    next_operator_steps: [
      {
        step_id: 'foreground_projection_visual',
        target_surface: 'projection_visual_url',
        expected_result_class: 'projection_visual_foreground_ready',
        command_submission_authorized_by_this_summary: false
      },
      {
        step_id: 'submit_non_appliance_preface',
        target_surface: 'projection_visual_ui',
        expected_result_class: 'display_feedback_result_class',
        command_submission_authorized_by_this_summary: false
      },
      {
        step_id: 'open_action_operator',
        target_surface: 'action_operator_url',
        expected_result_class: 'operator_surface_reachable',
        command_submission_authorized_by_this_summary: false
      },
      {
        step_id: 'select_reviewed_ac_action_or_hold',
        target_surface: 'home_control_operator_route_shortcut',
        expected_action_ids: ['aircon_cool', 'aircon_hvac_off'],
        expected_result_class: 'first_action_result_class',
        command_submission_authorized_by_this_summary: false
      }
    ],
    latency_bottleneck_hints: [
      'foreground_to_preface_input_delay',
      'operator_action_id_visibility_without_catalog_load',
      'local_tts_summary_availability'
    ],
    startupTiming: timing,
    proof_ceiling: 'launcher_demo_timed_action_readiness_summary_only',
    command_submission_authorized_by_this_summary: false,
    raw_private_publication_flags: false,
    non_claims: [
      'not_runtime_success_by_itself',
      'not_first_audio_proof',
      'not_first_action_proof',
      'not_home_assistant_or_home_control_operation',
      'not_physical_device_proof'
    ]
  }
}

const getDiagnosticSurfacesPayload = () => ({
  ok: true,
  timestamp: nowIso(),
  diagnosticSurfaces: diagnosticSurfacesSummary(),
  proof_ceiling: 'source_static_diagnostic_surface_readiness_only',
  raw_private_publication_flags: false
})

const sendJson = (response, statusCode, payload, extraHeaders = {}) => {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    ...extraHeaders
  })
  response.end(JSON.stringify(payload))
}

const resolvePublicFile = (pathname) => {
  let decoded
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return null
  }
  const normalized = decoded === '/' ? '/index.html' : decoded
  const filePath = path.resolve(PUBLIC_DIR, normalized.replace(/^[/\\]+/, ''))
  const relative = path.relative(PUBLIC_DIR, filePath)
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    return null
  }
  return filePath
}

const serveStatic = (request, response, requestUrl) => {
  const filePath = resolvePublicFile(requestUrl.pathname)
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    response.writeHead(404)
    response.end('Not found')
    return
  }
  const ext = path.extname(filePath).toLowerCase()
  response.writeHead(200, {
    'Content-Type': MIME_TYPES[ext] || 'application/octet-stream',
    'Cache-Control': 'no-store'
  })
  fs.createReadStream(filePath).pipe(response)
}

const handleApi = async (request, response, requestUrl) => {
  // HTTP route表。mutation routeは必ずexclusive lifecycle入口へ流す。
  const includeLocalCameraSelection = isLoopbackAddress(getRemoteAddress(request))
  if (request.method === 'OPTIONS') {
    const headers =
      requestUrl.pathname === '/api/status' ? launcherStatusCorsHeaders() : {}
    sendJson(response, 204, {}, headers)
    return
  }
  if (
    process.env.NODE_ENV === 'test' &&
    request.method === 'POST' &&
    requestUrl.pathname === '/api/test/pid-registry-snapshot-valid'
  ) {
    sendJson(response, 200, {
      ok: true,
      valid: Boolean(readExactPidRegistrySnapshot())
    })
    return
  }
  if (
    process.env.NODE_ENV === 'test' &&
    request.method === 'POST' &&
    requestUrl.pathname === '/api/test/camera-command-boundary'
  ) {
    const body = await readBody(request)
    const suppliedTestOptions = body.useSavedOptions
      ? readLauncherConfig().options || {}
      : body.options || {}
    const testOptions = body.applyRequestCameraBoundary && !includeLocalCameraSelection
      ? withPreservedLocalCameraSelection(
          body.profileId || DEFAULT_MODE_ID,
          suppliedTestOptions
        )
      : suppliedTestOptions
    const requested = sanitizeVideoInputDeviceName(testOptions.MediapipeCameraName)
    const normalizedTestOptions = normalizeOptions(
      body.profileId || DEFAULT_MODE_ID,
      testOptions
    )
    const cameraSelection = body.resolveSelection
      ? resolveVideoInputSelectionForStart(normalizedTestOptions)
      : {
          ok: true,
          captureName: requested,
          selection_class: requested ? 'manual_selection' : 'no_selection'
        }
    const preview = previewCommand(
      body.profileId || DEFAULT_MODE_ID,
      normalizedTestOptions,
      { resolvedCameraName: cameraSelection.ok ? cameraSelection.captureName : '' }
    )
    const publicPreview = publicCommandPreview(preview)
    const publicSerialized = JSON.stringify(publicPreview)
    sendJson(response, 200, {
      ok: true,
      selection_resolution_ok: cameraSelection.ok,
      selection_class: cameraSelection.selection_class,
      selection_error: cameraSelection.ok ? null : cameraSelection.error,
      selection_resolved_exact: body.expectedResolvedCameraName
        ? cameraSelection.captureName === body.expectedResolvedCameraName
        : null,
      input_accepted: Boolean(requested),
      execution_argv_exact: cameraSelection.captureName === requested,
      review_command_redacted: true,
      public_preview_redacted:
        !Object.hasOwn(publicPreview.options || {}, 'MediapipeCameraName') &&
        (!requested || !publicSerialized.includes(requested)),
      launcher_state_command_redacted: true,
      log_command_redacted: true,
      saved_selection_exact: body.useSavedOptions
        ? cameraSelection.captureName === requested
        : null,
      request_camera_boundary_preserved: body.applyRequestCameraBoundary
        ? requested === sanitizeVideoInputDeviceName(
            readLauncherConfig().options?.MediapipeCameraName
          )
        : null
    })
    return
  }
  if (
    request.method === 'GET' &&
    requestUrl.pathname === ORDINARY_ROUTE_PUBLIC_SURFACES.contract.path
  ) {
    sendJson(response, 200, ordinaryRouteContractPayload())
    return
  }
  if (
    request.method === 'GET' &&
    requestUrl.pathname === ORDINARY_ROUTE_PUBLIC_SURFACES.state.path
  ) {
    sendJson(
      response,
      200,
      await getState({ includeLocalCameraSelection })
    )
    return
  }
  if (
    request.method === 'GET' &&
    requestUrl.pathname === ORDINARY_ROUTE_PUBLIC_SURFACES.status.path
  ) {
    sendJson(response, 200, await getStatus(), launcherStatusCorsHeaders())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/video-input-devices') {
    sendJson(
      response,
      200,
      getVideoInputDevicesPayload({ includeLocalCameraSelection })
    )
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/startup-timing') {
    sendJson(response, 200, await getStartupTimingPayload())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/diagnostic-surfaces') {
    sendJson(response, 200, getDiagnosticSurfacesPayload())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/demo-timed-action-readiness') {
    sendJson(response, 200, await demoTimedActionReadiness())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/logs') {
    sendJson(response, 200, {
      ok: true,
      diagnostic: publicFixedStartDiagnostic(readLauncherState())
    })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/preview') {
    const body = await readBody(request)
    sendJson(
      response,
      200,
      publicCommandPreview(
        previewCommand(body.profileId || DEFAULT_MODE_ID, body.options || {})
      )
    )
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/save-config') {
    const body = await readBody(request)
    const profileId = body.profileId || DEFAULT_MODE_ID
    const profileError = requireSupervisorProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const configLock = activeOperationConfigLock()
    if (configLock) {
      sendJson(response, 409, configLock)
      return
    }
    let options
    try {
      const requestedOptions = includeLocalCameraSelection
        ? body.options || {}
        : withPreservedLocalCameraSelection(profileId, body.options || {})
      options = normalizeOptions(profileId, requestedOptions)
    } catch (error) {
      if (error?.message === 'invalid_openai_broker_request_budget') {
        sendJson(response, 400, { error: 'invalid_openai_broker_request_budget' })
        return
      }
      throw error
    }
    const saved = saveConfig(profileId, options)
    const demoSafeSettings = body.demoSettings
      ? saveDemoSafeSettings(body.demoSettings)
      : effectiveDemoSafeSettings()
    sendJson(response, 200, {
      ok: true,
      profileId,
      configIdentity: saved.configIdentity,
      options: includeLocalCameraSelection
        ? options
        : withoutLocalCameraSelection(options),
      demoSafeSettings
    })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/start') {
    const body = await readBody(request)
    const profileId = body.profileId || DEFAULT_MODE_ID
    const profileError = requireSupervisorProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const savedStartConfig = resolveSavedStartConfig(profileId, body.expectedConfigSha256)
    const result = await runExclusiveStackOperation(
      'start',
      async () => startStack(profileId, savedStartConfig)
    )
    sendJson(
      response,
      result.payload?.ok === false ? 409 : result.statusCode,
      result.payload
    )
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/stop') {
    const body = await readBody(request)
    const result = await runExclusiveStackOperation(
      'stop',
      async () => stopStack(body)
    )
    sendJson(response, result.statusCode, result.payload)
    return
  }
  if (
    request.method === 'POST' &&
    requestUrl.pathname === '/api/reclaim-managed-ports'
  ) {
    const body = await readBody(request)
    const config = readLauncherConfig()
    const profileId = (body && body.profileId) || config.selectedProfileId || DEFAULT_MODE_ID
    const profileError = requireSupervisorProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const result = await runExclusiveStackOperation(
      'reclaim',
      async () => reclaimManagedPortsFromLauncher(body)
    )
    sendJson(response, result.statusCode, result.payload)
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/status-script') {
    sendJson(response, 200, {
      ...(await getStatus()),
      schema_version: 'launcher_status_projection.v1',
      result_class: 'node_supervisor_status',
      status_script_execution: false,
      raw_private_publication_flags: false
    })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/shutdown') {
    const result = await stopStack({})
    if (!result.ok) {
      sendJson(response, 409, {
        ...result,
        shutdown_scheduled: false
      })
      return
    }
    sendJson(response, 200, {
      ok: true,
      message: 'launcher_shutdown_scheduled',
      shutdown_scheduled: true,
      operation: result.operation
    })
    setTimeout(() => {
      server.close(() => process.exit(0))
      setTimeout(() => process.exit(0), 1000).unref()
    }, 50).unref()
    return
  }
  sendJson(response, 404, { ok: false, error: 'not_found' })
}

const server = http.createServer(async (request, response) => {
  try {
    const requestUrl = new URL(
      request.url,
      `http://${request.headers.host || `${HOST}:${PORT}`}`
    )
    if (rejectUntrustedRequest(request, response)) {
      return
    }
    if (request.method === 'OPTIONS') {
      const headers =
        requestUrl.pathname === ORDINARY_ROUTE_PUBLIC_SURFACES.status.path
          ? launcherStatusCorsHeaders()
          : {}
      sendJson(response, 204, {}, headers)
      return
    }
    if (requestUrl.pathname.startsWith('/api/')) {
      await handleApi(request, response, requestUrl)
      return
    }
    if (request.method === 'GET') {
      serveStatic(request, response, requestUrl)
      return
    }
    sendJson(response, 405, { ok: false, error: 'method_not_allowed' })
  } catch (error) {
    sendJson(response, 500, {
      ok: false,
      error: error.message || String(error)
    })
  }
})

let signalShutdownInFlight = false
const finalizeSignalShutdown = async () => {
  if (signalShutdownInFlight) return
  signalShutdownInFlight = true
  try {
    const result = await stopStack({})
    if (!result.ok) {
      signalShutdownInFlight = false
      process.exitCode = 1
      return
    }
    server.close(() => process.exit(0))
  } catch {
    signalShutdownInFlight = false
    process.exitCode = 1
  }
}
process.on('SIGINT', () => { void finalizeSignalShutdown() })
process.on('SIGTERM', () => { void finalizeSignalShutdown() })

const openBrowser = (targetUrl) => {
  const command =
    process.platform === 'win32'
      ? { file: 'cmd', args: ['/c', 'start', '', targetUrl] }
      : process.platform === 'darwin'
        ? { file: 'open', args: [targetUrl] }
        : { file: 'xdg-open', args: [targetUrl] }
  try {
    const child = childProcess.spawn(command.file, command.args, {
      detached: true,
      stdio: 'ignore',
      windowsHide: true
    })
    child.unref()
  } catch {
    // The URL is printed below, so failing to auto-open is non-fatal.
  }
}

server.on('error', (error) => {
  if (error && error.code === 'EADDRINUSE') {
    const url = `http://${HOST}:${PORT}`
    console.log(`Sword System Launcher is already running: ${url}`)
    console.log('Run .\\stop-home-control-launcher.bat, or restart via .\\start-home-control-launcher.bat from the workspace root.')
    if (OPEN_BROWSER) {
      openBrowser(url)
    }
    process.exitCode = 0
    return
  }
  console.error(error)
  process.exitCode = 1
})

assertLauncherRuntimeAlignment({
  profileId: ORDINARY_ROUTE_CONTRACT.profile_id,
  publicSurfaces: {
    contract: { path: ORDINARY_ROUTE_PUBLIC_SURFACES.contract.path },
    status: {
      path: ORDINARY_ROUTE_PUBLIC_SURFACES.status.path,
      field: 'startupTiming'
    },
    state: {
      path: ORDINARY_ROUTE_PUBLIC_SURFACES.state.path,
      field: 'launcherState.fixed_start_summary'
    }
  },
  expectedServiceIds: expectedServicesForOptions(
    normalizeOptions(ORDINARY_ROUTE_MODE_ID, {})
  )
})
ensureRuntimeDirs()
server.listen(PORT, HOST, () => {
  const url = `http://${HOST}:${PORT}`
  console.log(`Sword System Launcher: ${url}`)
  console.log(`Workspace root: ${WORKSPACE_ROOT}`)
  if (OPEN_BROWSER) {
    openBrowser(url)
  }
})
