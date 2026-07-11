const childProcess = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const http = require('http')
const https = require('https')
const net = require('net')
const path = require('path')

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

const PUBLIC_DIR = path.join(__dirname, 'public')
const PROFILE_FILE = path.join(__dirname, 'config', 'default-profiles.json')
const OPS_SCRIPT_ROOT = path.join(PROJECT_ROOT, 'ops', 'scripts')
const SYSTEM_SCRIPT = path.join(OPS_SCRIPT_ROOT, 'system.ps1')
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
const PRIMARY_PROFILE_ID = 'thought-core-v0'
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
  VoicevoxReadyTimeoutSeconds: 45,
  MediapipeReadyTimeoutSeconds: 90,
  VoicevoxUrl: '',
  HomeControlConfigPath: '',
  MediapipeMode: 'mediamtx',
  MediapipeCameraName: 'HD Pro Webcam C920',
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

const OPS_PROFILE_BY_LAUNCHER_PROFILE = {
  'demo-fast': 'demo-fast',
  'demo-fast-action': 'demo-fast-action',
  'no-touchdesigner': 'thought-core-v0',
  'thought-core-v0': 'thought-core-v0',
  'thought-core-experimental': 'thought-core-experimental',
  'aituber-only': 'aituber-only',
  'camera-debug': 'camera-debug'
}

const opsProfileFor = (profileId) =>
  OPS_PROFILE_BY_LAUNCHER_PROFILE[profileId] || profileId || PRIMARY_PROFILE_ID

const NUMBER_FIELDS = new Set([
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'MediapipeBrowserMonitorPort',
  'VisionSnapshotProcessorPort',
  'AituberPort',
  'TouchDesignerGuiPort',
  'ThoughtCorePort',
  'VoicevoxReadyTimeoutSeconds',
  'MediapipeReadyTimeoutSeconds'
])

const STRING_FIELDS = new Set([
  'HomeAssistantBridgeHost',
  'AituberHost',
  'TouchDesignerGuiHost',
  'ThoughtCoreHost',
  'VoicevoxUrl',
  'HomeControlConfigPath',
  'MediapipeMode',
  'MediapipeCameraName'
])

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
  const sanitizedContent = stripAnsiControlSequences(
    Buffer.isBuffer(content) ? content.toString('utf8') : content
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

const writeJsonFile = (filePath, value) => {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

const readProfiles = () => readJsonFile(PROFILE_FILE, [])

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

const readLauncherConfig = () =>
  readJsonFile(LAUNCHER_CONFIG_FILE, {
    selectedProfileId: PRIMARY_PROFILE_ID,
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

const getRemoteAddress = (request) => normalizeIpAddress(request.socket.remoteAddress)

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
  const base = {
    ...DEFAULT_OPTIONS,
    ...(selectedProfile ? selectedProfile.options || {} : {}),
    ...(overrides || {})
  }
  const normalized = { ...DEFAULT_OPTIONS }
  for (const [key, defaultValue] of Object.entries(DEFAULT_OPTIONS)) {
    const value = base[key]
    if (NUMBER_FIELDS.has(key)) {
      const numberValue = Number(value)
      normalized[key] = Number.isInteger(numberValue) && numberValue > 0
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
  // "headless" maps to the legacy serve_websocket.py path in the stack script.
  // Keep accepting it for saved configs and direct API calls, but do not expose it
  // as a normal launcher mode.
  if (!['gui', 'headless', 'camera-hub', 'mediamtx'].includes(normalized.MediapipeMode)) {
    normalized.MediapipeMode = DEFAULT_OPTIONS.MediapipeMode
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

const quoteArg = (arg) => {
  const value = String(arg)
  if (/^[A-Za-z0-9_./:=@-]+$/.test(value)) {
    return value
  }
  return `"${value.replace(/"/g, '\\"')}"`
}

const formatCommand = (command) => command.map(quoteArg).join(' ')

const escapeRegExp = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const scriptSupportsParameter = (scriptPath, name) => {
  try {
    const text = fs.readFileSync(scriptPath, 'utf8')
    return new RegExp(`\\$${escapeRegExp(name)}\\b`).test(text)
  } catch {
    return true
  }
}

const addParam = (args, name, value) => {
  args.push(`-${name}`)
  args.push(String(value))
}

const addSupportedParam = (scriptPath, args, name, value) => {
  if (scriptSupportsParameter(scriptPath, name)) {
    addParam(args, name, value)
  }
}

const addSupportedSwitch = (scriptPath, args, name) => {
  if (scriptSupportsParameter(scriptPath, name)) {
    args.push(`-${name}`)
  }
}

const buildSystemStartArgs = (profileId, options) => {
  const stackArgs = ['start', '-Profile', opsProfileFor(profileId)]
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'HomeAssistantBridgeHost', options.HomeAssistantBridgeHost)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'HomeAssistantBridgePort', options.HomeAssistantBridgePort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'EnvironmentStatePort', options.EnvironmentStatePort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'MediapipePort', options.MediapipePort)
  addSupportedParam(
    SYSTEM_SCRIPT,
    stackArgs,
    'MediapipeBrowserMonitorPort',
    options.MediapipeBrowserMonitorPort
  )
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'VisionSnapshotProcessorPort', options.VisionSnapshotProcessorPort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'AituberHost', options.AituberHost)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'AituberPort', options.AituberPort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'TouchDesignerGuiHost', options.TouchDesignerGuiHost)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'TouchDesignerGuiPort', options.TouchDesignerGuiPort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'ThoughtCoreHost', options.ThoughtCoreHost)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'ThoughtCorePort', options.ThoughtCorePort)
  addSupportedParam(
    SYSTEM_SCRIPT,
    stackArgs,
    'VoicevoxReadyTimeoutSeconds',
    options.VoicevoxReadyTimeoutSeconds
  )
  addSupportedParam(
    SYSTEM_SCRIPT,
    stackArgs,
    'MediapipeReadyTimeoutSeconds',
    options.MediapipeReadyTimeoutSeconds
  )
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'MediapipeMode', options.MediapipeMode)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'MediapipeCameraName', options.MediapipeCameraName)

  if (options.VoicevoxUrl) {
    addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'VoicevoxUrl', options.VoicevoxUrl)
  }
  if (options.HomeControlConfigPath) {
    addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'HomeControlConfigPath', options.HomeControlConfigPath)
  }
  for (const key of SWITCH_FIELDS) {
    if (options[key]) {
      addSupportedSwitch(SYSTEM_SCRIPT, stackArgs, key)
    }
  }
  return stackArgs
}

const buildSystemStatusArgs = (profileId, options) => {
  const stackArgs = ['status', '-Profile', opsProfileFor(profileId)]
  for (const key of NUMBER_FIELDS) {
    addSupportedParam(SYSTEM_SCRIPT, stackArgs, key, options[key])
  }
  for (const key of [
    'VoicevoxUrl',
    'ThoughtCoreHost'
  ]) {
    if (options[key]) {
      addSupportedParam(SYSTEM_SCRIPT, stackArgs, key, options[key])
    }
  }
  if (options.EnableThoughtCore) {
    addSupportedSwitch(SYSTEM_SCRIPT, stackArgs, 'EnableThoughtCore')
  }
  if (options.EnableThoughtCoreWatch) {
    addSupportedSwitch(SYSTEM_SCRIPT, stackArgs, 'EnableThoughtCoreWatch')
  }
  return stackArgs
}

const buildPowerShellCommand = (scriptPath, scriptArgs = []) => [
  psExecutable(),
  '-NoLogo',
  '-NoProfile',
  '-ExecutionPolicy',
  'Bypass',
  '-File',
  scriptPath,
  '-WorkspaceRoot',
  WORKSPACE_ROOT,
  ...scriptArgs
]

const previewCommand = (profileId, optionOverrides = {}) => {
  const profileError = requireKnownProfile(profileId)
  if (profileError) {
    return profileError
  }
  const options = normalizeOptions(profileId, optionOverrides)
  const command = buildPowerShellCommand(SYSTEM_SCRIPT, buildSystemStartArgs(profileId, options))
  return {
    ok: true,
    profileId,
    opsProfile: opsProfileFor(profileId),
    options,
    demoSafeGate: effectiveDemoSafeSettings().summary,
    command,
    commandLine: formatCommand(command)
  }
}

const saveConfig = (profileId, options) => {
  writeJsonFile(LAUNCHER_CONFIG_FILE, {
    selectedProfileId: profileId,
    options,
    updatedAt: nowIso()
  })
}

let activeStackOperation = null

const operationState = () =>
  activeStackOperation
    ? { busy: true, ...activeStackOperation }
    : { busy: false, type: 'idle' }

const operationConflictPayload = (requestedType) => ({
  ok: false,
  error: 'operation_in_progress',
  message: `${activeStackOperation.type} is already running. Wait for it to finish before requesting ${requestedType}.`,
  requestedType,
  operation: operationState()
})

const runExclusiveStackOperation = async (type, action) => {
  if (activeStackOperation) {
    return {
      statusCode: 409,
      payload: operationConflictPayload(type)
    }
  }

  const operation = {
    id: crypto.randomUUID(),
    type,
    startedAt: nowIso()
  }
  activeStackOperation = operation
  appendStackLog(`[launcher] ${type} operation started id=${operation.id}\n`)

  try {
    const payload = await action()
    const finishedAt = nowIso()
    appendStackLog(`[launcher] ${type} operation finished id=${operation.id}\n`)
    return {
      statusCode: 200,
      payload: {
        ...payload,
        operation: {
          busy: false,
          ...operation,
          finishedAt
        }
      }
    }
  } finally {
    activeStackOperation = null
  }
}

const startStack = (profileId, optionOverrides = {}) => {
  ensureRuntimeDirs()
  const preview = previewCommand(profileId, optionOverrides)
  if (!preview.ok) {
    return preview
  }
  saveConfig(profileId, preview.options)

  appendStackLog(
    [
      '',
      `===== Sword System Launcher start ${nowIso()} =====`,
      preview.commandLine,
      ''
    ].join('\n')
  )

  let child
  try {
    child = childProcess.spawn(preview.command[0], preview.command.slice(1), {
      cwd: PROJECT_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
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
  } catch (error) {
    throw error
  }

  const acceptedAt = nowIso()
  const state = {
    startedAt: acceptedAt,
    supervisorPid: child.pid,
    commandLine: preview.commandLine,
    profileId,
    startupTiming: {
      schema_version: 'launcher_startup_timing.v0',
      profileId,
      acceptedAt,
      supervisorPid: child.pid,
      status_class: 'starting',
      expectedServiceIds: expectedServicesForOptions(preview.options),
      timelineEvents: [
        {
          event_class: 'launcher_start_accepted',
          at: acceptedAt,
          elapsedMs: 0
        }
      ],
      serviceReadiness: {},
      raw_private_publication_flags: false
    }
  }
  writeJsonFile(LAUNCHER_STATE_FILE, state)

  appendStackLog(`[launcher] spawned stack supervisor pid=${child.pid}\n`)

  child.stdout.on('data', (chunk) => {
    appendStackLog(chunk)
  })
  child.stderr.on('data', (chunk) => {
    appendStackLog(chunk)
  })

  child.once('error', (error) => {
    appendStackLog(
      `[launcher] failed to spawn stack supervisor: ${error.message}\n`
    )
    writeJsonFile(LAUNCHER_STATE_FILE, {
      ...readLauncherState(),
      failedAt: nowIso(),
      lastError: error.message
    })
  })

  child.once('exit', (code, signal) => {
    appendStackLog(
      `[launcher] stack supervisor exited code=${code} signal=${signal || '-'} at ${nowIso()}\n`
    )
    writeJsonFile(LAUNCHER_STATE_FILE, {
      ...readLauncherState(),
      exitedAt: nowIso(),
      exitCode: code,
      signal: signal || null
    })
  })
  return { ok: true, ...state }
}

const runScriptAndCollect = (scriptPath, scriptArgs = [], timeoutMs = 30000) =>
  new Promise((resolve) => {
    const command = buildPowerShellCommand(scriptPath, scriptArgs)
    const child = childProcess.spawn(command[0], command.slice(1), {
      cwd: PROJECT_ROOT,
      windowsHide: true,
      env: {
        ...process.env,
        HOME_CONTROL_WORKSPACE_ROOT: WORKSPACE_ROOT,
        HOME_CONTROL_STACK_STATE_DIR: STATE_DIR
      }
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill()
      resolve({
        ok: false,
        timedOut: true,
        commandLine: formatCommand(command),
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
        commandLine: formatCommand(command),
        stdout,
        stderr: `${stderr}${error.message}`
      })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({
        ok: code === 0,
        code,
        commandLine: formatCommand(command),
        stdout,
        stderr
      })
    })
  })

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

const listeningPortProcessOwners = async (port) => {
  const numericPort = Number(port)
  if (!Number.isInteger(numericPort) || numericPort <= 0) {
    return []
  }
  const script = `
$items = @()
$connections = @(Get-NetTCPConnection -LocalPort ${numericPort} -State Listen -ErrorAction SilentlyContinue)
foreach ($processId in ($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
  if ($processId -le 0) { continue }
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
  $parent = if ($null -ne $process) {
    Get-CimInstance Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
  } else { $null }
  $parentRuntime = if ($null -ne $parent) { Get-Process -Id $parent.ProcessId -ErrorAction SilentlyContinue } else { $null }
  $items += [pscustomobject]@{
    pid = [int]$processId
    processName = if ($null -ne $process) { [string]$process.Name } else { "" }
    commandLine = if ($null -ne $process) { [string]$process.CommandLine } else { "" }
    parentPid = if ($null -ne $process) { [int]$process.ParentProcessId } else { 0 }
    parentProcessName = if ($null -ne $parent) { [string]$parent.Name } else { "" }
    parentStartedAt = if ($null -ne $parentRuntime) { ([DateTimeOffset]$parentRuntime.StartTime).ToString('o') } else { "" }
    parentParentPid = if ($null -ne $parent) { [int]$parent.ParentProcessId } else { 0 }
    startedAt = if ($null -ne $process) {
      $started = Get-Process -Id $processId -ErrorAction SilentlyContinue
      if ($null -ne $started) { ([DateTimeOffset]$started.StartTime).ToString('o') } else { '' }
    } else { "" }
  }
}
$items | ConvertTo-Json -Compress
`
  const result = await runPowerShellInlineAndCollect(script)
  if (!result.ok) {
    return []
  }
  const owners = parseJsonArray(result.stdout)
  if (
    process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_DUPLICATE_PORT_OWNER === 'true' &&
    owners.length === 1
  ) {
    return [owners[0], { ...owners[0] }]
  }
  return owners
}

const normalizedProcessName = (value) =>
  String(value || '').toLowerCase().replace(/\.exe$/, '')

const commandLineContainsPath = (commandLine, targetPath) => {
  const text = String(commandLine || '').toLowerCase()
  const normalizedTarget = path.resolve(targetPath).toLowerCase()
  return text.includes(normalizedTarget) || text.includes(normalizedTarget.replace(/\\/g, '/'))
}

const commandLineContainsAll = (commandLine, fragments) => {
  const text = String(commandLine || '').toLowerCase()
  return fragments.every((fragment) => text.includes(String(fragment).toLowerCase()))
}

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

const MANAGED_PORT_RECLAIM_POLICIES = {
  home_assistant_bridge: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'action', 'home-assistant-server'),
    allowedProcessNames: ['uv', 'uvicorn', 'python']
  },
  environment_state_server: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'environment', 'environment-state-server'),
    allowedProcessNames: ['uv', 'python']
  },
  mediapipe_camera_hub: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'reflex', 'mediapipe-sword-sign'),
    allowedProcessNames: ['uv', 'python', 'mediamtx', 'ffmpeg']
  },
  mediapipe_browser_monitor: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'reflex', 'mediapipe-sword-sign'),
    allowedProcessNames: ['uv', 'python', 'mediamtx', 'ffmpeg']
  },
  mediapipe_rtsp: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'reflex', 'mediapipe-sword-sign'),
    allowedProcessNames: ['uv', 'python', 'mediamtx', 'ffmpeg']
  },
  mediapipe_web_media: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'reflex', 'mediapipe-sword-sign'),
    allowedProcessNames: ['uv', 'python', 'mediamtx', 'ffmpeg']
  },
  vision_snapshot_processor: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'environment', 'vision-snapshot-processor'),
    allowedProcessNames: ['uv', 'python']
  },
  aituber_kit: {
    rootPath: () => path.join(WORKSPACE_ROOT, 'organs', 'expression', 'aituber-kit'),
    allowedProcessNames: ['cmd', 'node', 'npm']
  },
  touchdesigner_control_gui: {
    rootPath: () => WORKSPACE_ROOT,
    allowedProcessNames: ['node'],
    commandFragments: ['server.js', '--workspace', '--port', '--touchdesigner-port', '--thought-core-port']
  },
  thought_core_api: {
    rootPath: () => PROJECT_ROOT,
    allowedProcessNames: ['pwsh', 'powershell', 'uv', 'python', 'node']
  }
}

const reclaimPolicyForTarget = (target) => {
  const policy = MANAGED_PORT_RECLAIM_POLICIES[target && target.key]
  if (!policy) {
    return null
  }
  return {
    ...policy,
    rootPath: policy.rootPath()
  }
}

const SEALED_LISTENER_CLASS_BY_TARGET = {
  home_assistant_bridge: 'home_control_bridge_descendant_listener.v0',
  vision_snapshot_processor: 'vsp_descendant_listener.v0',
  thought_core_api: 'thought_core_descendant_listener.v0'
}
const sealedValidationCounts = new Map()

const validateSealedListenerEntry = async (entry, requiredOwnershipClass) => {
  const validationKey = `${requiredOwnershipClass}:${Number(entry && entry.pid) || 0}`
  const validationCount = sealedValidationCounts.get(validationKey) || 0
  sealedValidationCounts.set(validationKey, validationCount + 1)
  if (
    process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_FAIL_SEALED_REVALIDATION === 'true' &&
    validationCount > 0
  ) {
    return { valid: false, reason: 'inspection_failed' }
  }
  const encodedEntry = Buffer.from(JSON.stringify(entry || {}), 'utf8').toString('base64')
  const commonScript = COMMON_SCRIPT.replace(/'/g, "''")
  const requiredClass = String(requiredOwnershipClass || '').replace(/'/g, "''")
  const simulateFailure = process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_SEALED_INSPECTION_FAILURE === 'true'
  const result = await runPowerShellInlineAndCollect(`
. '${commonScript}'
$entryJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encodedEntry}'))
$entry = $entryJson | ConvertFrom-Json
$result = Test-SwordSealedDescendantListenerEntry -Entry $entry -RequiredOwnershipClass '${requiredClass}' -SimulateInspectionFailure:$${simulateFailure ? 'true' : 'false'}
$result | ConvertTo-Json -Compress
`)
  if (!result.ok) return { valid: false, reason: 'inspection_failed' }
  try {
    const parsed = JSON.parse(String(result.stdout || '').trim())
    return {
      valid: parsed.Valid === true,
      reason: String(parsed.Reason || 'inspection_failed'),
      listenerPid: Number(parsed.ListenerPid) || 0
    }
  } catch {
    return { valid: false, reason: 'inspection_failed' }
  }
}

const recordedSealedListenerOwner = async (owner, target) => {
  const requiredClass = SEALED_LISTENER_CLASS_BY_TARGET[target && target.key]
  if (!requiredClass) return false
  const pidState = readPidState()
  const entries = Array.isArray(pidState.processes) ? pidState.processes : []
  const candidates = entries.filter((entry) =>
    entry && entry.ownership_class === requiredClass &&
    Number(entry.pid) === Number(owner && owner.pid) &&
    Number(entry.expected_port) === Number(target && target.port)
  )
  if (candidates.length !== 1) return false
  const validation = await validateSealedListenerEntry(candidates[0], requiredClass)
  return validation.valid && validation.listenerPid === Number(owner && owner.pid)
}

const isReclaimableManagedPortOwner = async (owner, target) => {
  const processName = normalizedProcessName(owner && owner.processName)
  if (!processName || EXTERNAL_PROCESS_DENY_LIST.has(processName)) {
    return false
  }
  const policy = reclaimPolicyForTarget(target)
  if (!policy) {
    return false
  }
  const allowed = new Set((policy.allowedProcessNames || []).map(normalizedProcessName))
  if (allowed.size > 0 && !allowed.has(processName)) {
    return false
  }
  if (SEALED_LISTENER_CLASS_BY_TARGET[target && target.key]) {
    return recordedSealedListenerOwner(owner, target)
  }
  const commandLine = String(owner && owner.commandLine || '')
  return (
    commandLineContainsPath(commandLine, policy.rootPath) &&
    commandLineContainsAll(commandLine, policy.commandFragments || [])
  )
}

const stopProcessById = async (pid) => {
  const numericPid = Number(pid)
  if (!Number.isInteger(numericPid) || numericPid <= 0 || numericPid === process.pid) {
    return { ok: false, skipped: true }
  }
  return runPowerShellInlineAndCollect(
    `Stop-Process -Id ${numericPid} -Force -ErrorAction Stop`,
    6000
  )
}

const reclaimManagedPortResidue = async (options) => {
  const targets = managedStopPortTargets(options)
  const enabled = process.env.HOME_CONTROL_LAUNCHER_MANAGED_PORT_RECLAIM !== 'false'
  const summary = {
    enabled,
    scope: 'managed_stack_ports',
    attempted: 0,
    reclaimed: [],
    skippedOwners: 0,
    targets: []
  }
  if (!enabled) {
    return summary
  }
  for (const target of targets) {
    const owners = await listeningPortProcessOwners(target.port)
    const reclaimable = []
    if (owners.length === 1 && await isReclaimableManagedPortOwner(owners[0], target)) {
      reclaimable.push(owners[0])
    }
    const targetSummary = {
      key: target.key,
      label: target.label,
      port: Number(target.port),
      ownerCount: owners.length,
      attempted: 0,
      reclaimed: [],
      skippedOwners: Math.max(0, owners.length - reclaimable.length)
    }
    summary.skippedOwners += targetSummary.skippedOwners
    for (const owner of reclaimable) {
      const currentOwners = await listeningPortProcessOwners(target.port)
      const revalidatedOwner = currentOwners.length === 1 ? currentOwners[0] : null
      if (
        !revalidatedOwner ||
        Number(revalidatedOwner.pid) !== Number(owner.pid) ||
        !await isReclaimableManagedPortOwner(revalidatedOwner, target)
      ) {
        summary.skippedOwners += 1
        targetSummary.skippedOwners += 1
        continue
      }
      summary.attempted += 1
      targetSummary.attempted += 1
      appendStackLog(
        `[launcher] reclaiming route-owned managed port residue key=${target.key} port=${target.port} pid=${owner.pid}\n`
      )
      const result = await stopProcessById(owner.pid)
      if (result.ok) {
        const reclaimed = {
          key: target.key,
          label: target.label,
          port: Number(target.port),
          pid: Number(owner.pid),
          processName: String(owner.processName || ''),
          reason: 'route_owned_managed_port_residue'
        }
        targetSummary.reclaimed.push(reclaimed)
        summary.reclaimed.push(reclaimed)
      }
    }
    if (owners.length > 0 || targetSummary.reclaimed.length > 0) {
      summary.targets.push(targetSummary)
    }
  }
  if (summary.reclaimed.length > 0) {
    await sleep(1000)
  }
  return summary
}

const stopStack = async (body) => {
  const config = readLauncherConfig()
  const profileId = (body && body.profileId) || config.selectedProfileId || PRIMARY_PROFILE_ID
  const profileError = requireKnownProfile(profileId)
  if (profileError) {
    return profileError
  }
  const options = normalizeOptions(profileId, config.options || {})
  const scriptArgs = ['stop', '-Profile', opsProfileFor(profileId), '-Force']
  const beforeStopCollection = await collectStackStopVerification(options)
  const { carriedUnverifiedEntries, ...beforeStopVerification } = beforeStopCollection
  const collectedResult = await runScriptAndCollect(SYSTEM_SCRIPT, scriptArgs, 45000)
  const result = process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_FORCE_STOP_SCRIPT_NONZERO === 'true'
    ? { ...collectedResult, ok: false, code: 1, timedOut: false }
    : collectedResult
  const managedPortReclaim = await reclaimManagedPortResidue(options)
  const stopCollection = await waitForStackStopVerification(
    options,
    carriedUnverifiedEntries,
    beforeStopVerification.staleRecorded
  )
  const { carriedUnverifiedEntries: ignoredCarriedEntries, ...stopVerification } = stopCollection
  const ok = Boolean(stopVerification.ok)
  const payload = {
    ...result,
    ok,
    beforeStopVerification,
    managedPortReclaim,
    stopVerification,
    message: ok
      ? 'Stop verified: all managed stack processes and ports are clear.'
      : describeStopVerificationFailure(stopVerification, result)
  }
  writeJsonFile(LAUNCHER_STATE_FILE, {
    ...readLauncherState(),
    stoppedAt: nowIso(),
    lastStop: payload
  })
  return payload
}

const reclaimManagedPortsFromLauncher = async (body) => {
  const config = readLauncherConfig()
  const profileId = (body && body.profileId) || config.selectedProfileId || PRIMARY_PROFILE_ID
  const profileError = requireKnownProfile(profileId)
  if (profileError) {
    return profileError
  }
  const options = normalizeOptions(profileId, config.options || {})
  const beforeStopVerification = await collectStackStopVerification(options)
  const managedPortReclaim = await reclaimManagedPortResidue(options)
  const stopVerification = await collectStackStopVerification(options)
  const payload = {
    ok: true,
    profileId,
    beforeStopVerification,
    managedPortReclaim,
    stopVerification,
    message: managedPortReclaim.reclaimed.length > 0
      ? 'Recovered route-owned managed port residue.'
      : 'No route-owned managed port residue was recoverable.'
  }
  writeJsonFile(LAUNCHER_STATE_FILE, {
    ...readLauncherState(),
    lastManagedPortReclaim: {
      ...payload,
      checkedAt: nowIso()
    }
  })
  return payload
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
    socket.once('connect', () => finish(true, 'listen'))
    socket.once('timeout', () => finish(false, 'timeout'))
    socket.once('error', (error) => finish(false, error.code || error.message))
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

const inspectRecordedProcesses = async (recordedProcesses) => {
  if (
    process.env.NODE_ENV === 'test' &&
    process.env.HOME_CONTROL_LAUNCHER_TEST_INSPECTION_FAILURE === 'true'
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
    .filter((target) => target.tcp && target.tcp.ok)
    .map((target) => ({
      key: target.key,
      label: target.label,
      host: target.host,
      port: target.port,
      detail: target.tcp.detail || 'listen'
    }))
  return {
    ok: !pidFileExists && aliveRecorded.length === 0 && openPorts.length === 0,
    checkedAt: nowIso(),
    pidFileExists,
    recordedProcessCount: recordedProcesses.length,
    aliveRecorded,
    staleRecorded: [...staleRecorded].sort(),
    carriedUnverifiedEntries: carriedEntries,
    checkedPortCount: checkedPorts.length,
    openPorts
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

const serviceState = ({ entry, tcp, http, requireHttp = false, processOnly = false }) => {
  const processAlive = entry ? isProcessAlive(entry.pid) : false
  const tcpOk = Boolean(tcp && tcp.ok)
  const httpOk = Boolean(http && http.ok)
  let state = 'DOWN'
  if (processOnly && processAlive) {
    state = 'OK'
  } else if (processAlive && (tcpOk || httpOk)) {
    state = requireHttp && !httpOk ? 'DEGRADED' : 'OK'
  } else if (httpOk || tcpOk) {
    state = entry ? 'DEGRADED' : 'OK_EXTERNAL'
  } else if (processAlive) {
    state = 'STARTING'
  }
  return {
    state,
    processAlive,
    pid: entry ? entry.pid : null,
    command: entry ? entry.command || '' : '',
    workingDirectory: entry ? entry.working_directory || '' : '',
    tcp: tcp || { ok: false, detail: '-' },
    http: http || { ok: false, detail: '-' },
    startedAt: entry ? entry.started_at || null : null
  }
}

const expectedServicesForOptions = (options) => {
  const services = []
  if (!options.SkipHomeAssistantBridge) services.push('home_assistant_bridge')
  if (!options.SkipEnvironmentState) services.push('environment_state_server')
  if (!options.SkipMediapipe) services.push('mediapipe')
  if (!options.SkipVisionSnapshotProcessor && !options.SkipMediapipe) {
    services.push('vision_snapshot_processor')
  }
  if (!options.SkipAituber) services.push('aituber_kit')
  if (!options.SkipTouchDesignerGui) services.push('touchdesigner_control_gui')
  if (options.EnableThoughtCore) services.push('thought_core_api')
  if (options.EnableThoughtCoreWatch) services.push('thought_core_watcher')
  if (!options.SkipVoicevoxCheck && !options.SkipAituber) services.push('voicevox')
  return services
}

const startupReadyTimeoutMsForService = (serviceId, options) => {
  if (serviceId === 'mediapipe') {
    return Number(options.MediapipeReadyTimeoutSeconds || 0) * 1000
  }
  if (serviceId === 'voicevox') {
    return Number(options.VoicevoxReadyTimeoutSeconds || 0) * 1000
  }
  return null
}

const serviceIsReady = (service) => {
  const state = String(service && service.state || '').toUpperCase()
  return state === 'OK' || state === 'OK_EXTERNAL'
}

const dateMs = (value) => {
  const ms = Date.parse(value || '')
  return Number.isFinite(ms) ? ms : null
}

const elapsedMs = (start, end = Date.now()) => {
  const startMs = dateMs(start)
  if (startMs === null) {
    return null
  }
  return Math.max(0, end - startMs)
}

const startupTimingEvents = ({ acceptedAt, lastCheckedAt, serviceReadiness }) => {
  const events = []
  if (acceptedAt) {
    events.push({
      event_class: 'launcher_start_accepted',
      at: acceptedAt,
      elapsedMs: 0
    })
  }
  for (const [serviceId, item] of Object.entries(serviceReadiness || {})) {
    if (item.firstReadyAt) {
      events.push({
        event_class: 'service_first_ready',
        serviceId,
        at: item.firstReadyAt,
        elapsedMs: item.firstReadyElapsedMs
      })
    } else {
      events.push({
        event_class: 'service_waiting',
        serviceId,
        at: lastCheckedAt,
        elapsedMs: item.waitingElapsedMs
      })
    }
  }
  return events.sort((left, right) => {
    const leftElapsed = Number.isFinite(Number(left.elapsedMs)) ? Number(left.elapsedMs) : Number.MAX_SAFE_INTEGER
    const rightElapsed = Number.isFinite(Number(right.elapsedMs)) ? Number(right.elapsedMs) : Number.MAX_SAFE_INTEGER
    if (leftElapsed !== rightElapsed) {
      return leftElapsed - rightElapsed
    }
    return String(left.serviceId || '').localeCompare(String(right.serviceId || ''))
  })
}

const updateStartupTimingSummary = ({ profileId, options, services }) => {
  const launcherState = readLauncherState()
  const startedAt = launcherState.startedAt || launcherState.startupTiming?.acceptedAt || null
  const now = Date.now()
  const nowText = nowIso()
  const expectedServiceIds = expectedServicesForOptions(options)
  const previous = launcherState.startupTiming && typeof launcherState.startupTiming === 'object'
    ? launcherState.startupTiming
    : {}
  const previousReadiness = previous.serviceReadiness || {}
  const serviceReadiness = {}
  const waitingServiceIds = []
  const readyServiceIds = []

  for (const serviceId of expectedServiceIds) {
    const service = services[serviceId] || {}
    const prior = previousReadiness[serviceId] || {}
    const ready = serviceIsReady(service)
    const firstSeenAt = prior.firstSeenAt || startedAt || nowText
    const firstReadyAt = ready
      ? prior.firstReadyAt || nowText
      : prior.firstReadyAt || null
    const waitingElapsed = ready
      ? null
      : elapsedMs(firstSeenAt, now)
    const firstReadyElapsed = firstReadyAt && startedAt
      ? elapsedMs(startedAt, dateMs(firstReadyAt))
      : null
    const readyTimeoutMs = startupReadyTimeoutMsForService(serviceId, options)
    if (ready) {
      readyServiceIds.push(serviceId)
    } else {
      waitingServiceIds.push(serviceId)
    }
    serviceReadiness[serviceId] = {
      state: service.state || 'DOWN',
      firstSeenAt,
      firstReadyAt,
      firstReadyElapsedMs: firstReadyElapsed,
      waitingElapsedMs: waitingElapsed,
      readyTimeoutMs,
      readyTimeoutClass: Number.isFinite(readyTimeoutMs) && readyTimeoutMs > 0
        ? 'explicit_service_ready_timeout'
        : 'no_explicit_service_ready_timeout',
      pid_present_class: service.pid ? 'pid_present' : 'pid_missing',
      tcp_detail_class: compactProfileId(service.tcp && service.tcp.detail || 'unknown'),
      http_detail_class: compactProfileId(service.http && service.http.detail || 'unknown')
    }
  }

  const criticalPathServiceId = waitingServiceIds
    .slice()
    .sort((a, b) => {
      const left = serviceReadiness[b].waitingElapsedMs || 0
      const right = serviceReadiness[a].waitingElapsedMs || 0
      return left - right
    })[0] || expectedServiceIds
    .slice()
    .sort((a, b) => {
      const left = serviceReadiness[b].firstReadyElapsedMs || 0
      const right = serviceReadiness[a].firstReadyElapsedMs || 0
      return left - right
    })[0] || ''

  const summary = {
    schema_version: 'launcher_startup_timing.v0',
    profileId,
    acceptedAt: startedAt,
    lastCheckedAt: nowText,
    elapsedMs: elapsedMs(startedAt, now),
    status_class: expectedServiceIds.length === 0
      ? 'startup_no_expected_services'
      : waitingServiceIds.length === 0
        ? 'startup_expected_services_ready'
        : 'startup_waiting_for_expected_services',
    expectedServiceIds,
    readyServiceIds,
    waitingServiceIds,
    criticalPathServiceId,
    criticalPathStateClass: criticalPathServiceId
      ? serviceReadiness[criticalPathServiceId].state
      : 'not_applicable',
    serviceReadiness,
    timelineEvents: startupTimingEvents({
      acceptedAt: startedAt,
      lastCheckedAt: nowText,
      serviceReadiness
    }),
    proof_ceiling: 'launcher_startup_timing_summary_only',
    raw_private_publication_flags: false,
    non_claims: [
      'not_runtime_success_by_itself',
      'not_first_audio_proof',
      'not_first_action_proof',
      'not_home_assistant_action',
      'not_browser_visible_avatar_motion',
      'not_user_heard_audio'
    ]
  }

  if (startedAt) {
    writeJsonFile(LAUNCHER_STATE_FILE, {
      ...launcherState,
      startupTiming: summary
    })
  }
  return summary
}

const pidMap = () => {
  const state = readPidState()
  const map = {}
  for (const entry of state.processes || []) {
    map[entry.name] = entry
  }
  return map
}

const effectiveStatusOptions = () => {
  const config = readLauncherConfig()
  return normalizeOptions(config.selectedProfileId || PRIMARY_PROFILE_ID, config.options || {})
}

const getVoicevoxUrl = (options) =>
  (options.VoicevoxUrl || 'http://127.0.0.1:50021').replace(
    /^http:\/\/localhost(?=:|\/|$)/,
    'http://127.0.0.1'
  )

const getEndpoints = (options) => {
  const voicevoxUrl = getVoicevoxUrl(options).replace(/\/$/, '')
  const thoughtCoreHost =
    options.ThoughtCoreHost === '0.0.0.0' ? '127.0.0.1' : options.ThoughtCoreHost
  const thoughtCoreUrl = `http://${thoughtCoreHost}:${options.ThoughtCorePort}`
  const mediaUrl = encodeURIComponent(
    'http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true'
  )
  const wsUrl = encodeURIComponent(`ws://127.0.0.1:${options.MediapipePort}`)
  const browserMonitorUrl = `http://127.0.0.1:${options.MediapipeBrowserMonitorPort}/browser_camera_hub_viewer.html?mediaUrl=${mediaUrl}&wsUrl=${wsUrl}&target=sword_sign`
  return [
    {
      group: 'Open in browser',
      name: 'Expression runtime',
      url: `http://127.0.0.1:${options.AituberPort}`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Projection Visual',
      url: `http://127.0.0.1:${options.AituberPort}/projection-visual/`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Passive Projection',
      url: `http://127.0.0.1:${options.AituberPort}/projection-visual/?mode=passive&hud=0`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Expression cube vault',
      url: `http://127.0.0.1:${options.AituberPort}/cube-vault-background?fov=60&scale=1`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Thought Core API index',
      url: thoughtCoreUrl,
      enabled: options.EnableThoughtCore
    },
    {
      group: 'Open in browser',
      name: 'Display runtime GUI/API',
      url: `http://127.0.0.1:${options.TouchDesignerGuiPort}`,
      enabled: !options.SkipTouchDesignerGui
    },
    {
      group: 'Open in browser',
      name: 'Action bridge operator',
      url: `http://127.0.0.1:${options.HomeAssistantBridgePort}/operator`,
      enabled: !options.SkipHomeAssistantBridge
    },
    {
      group: 'Local APIs and feeds',
      name: 'Action bridge health',
      url: `http://127.0.0.1:${options.HomeAssistantBridgePort}/health`,
      enabled: !options.SkipHomeAssistantBridge
    },
    {
      group: 'Local APIs and feeds',
      name: 'Environment display state',
      url: `http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`,
      enabled: !options.SkipEnvironmentState
    },
    {
      group: 'Local APIs and feeds',
      name: 'Reflex browser monitor',
      url: browserMonitorUrl,
      enabled: !options.SkipMediapipe && options.MediapipeMode === 'mediamtx'
    },
    {
      group: 'Local APIs and feeds',
      name: 'Reflex camera video',
      url: 'http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true',
      enabled: !options.SkipMediapipe && options.MediapipeMode === 'mediamtx'
    },
    {
      group: 'Local APIs and feeds',
      name: 'Reflex Camera Hub WebSocket',
      url: `ws://127.0.0.1:${options.MediapipePort}`,
      enabled: !options.SkipMediapipe
    },
    {
      group: 'Local APIs and feeds',
      name: 'Vision snapshot WebSocket',
      url: `ws://127.0.0.1:${options.VisionSnapshotProcessorPort}`,
      enabled:
        !options.SkipVisionSnapshotProcessor &&
        !options.SkipMediapipe &&
        options.MediapipeMode === 'mediamtx'
    },
    {
      group: 'Local APIs and feeds',
      name: 'VOICEVOX',
      url: voicevoxUrl,
      enabled: !options.SkipVoicevoxCheck && !options.SkipAituber
    },
    {
      group: 'Local APIs and feeds',
      name: 'Thought Core health',
      url: `${thoughtCoreUrl}/health`,
      enabled: options.EnableThoughtCore
    },
    {
      group: 'Background links',
      name: 'Thought Core watcher',
      url: 'no browser URL',
      enabled: options.EnableThoughtCoreWatch
    },
    {
      group: 'Background links',
      name: 'Display UDP receiver',
      url: '127.0.0.1:9001',
      enabled: true
    }
  ]
}

const getStatus = async () => {
  const config = readLauncherConfig()
  const selectedProfileId = config.selectedProfileId || PRIMARY_PROFILE_ID
  const profileConfigState = requireKnownProfile(selectedProfileId) || {
    ok: true,
    resultClass: 'known_profile',
    selectedProfileId
  }
  const options = effectiveStatusOptions()
  const pids = pidMap()
  const mediapipeEntry =
    pids.mediapipe_camera_hub_stack ||
    pids.mediapipe_camera_hub ||
    pids.mediapipe_ws ||
    pids.mediapipe_camera_hub_gui
  const voicevoxUrl = getVoicevoxUrl(options).replace(/\/$/, '')
  const thoughtCoreHost =
    options.ThoughtCoreHost === '0.0.0.0' ? '127.0.0.1' : options.ThoughtCoreHost
  const thoughtCoreUrl = `http://${thoughtCoreHost}:${options.ThoughtCorePort}`
  const exposeEnvironmentStatus = shouldExposeEnvironmentStatus()
  let voicevoxPort = 50021
  try {
    voicevoxPort = Number(new URL(voicevoxUrl).port || 50021)
  } catch {
    voicevoxPort = 50021
  }
  const homeAssistantBridgeEnabled = !options.SkipHomeAssistantBridge
  const environmentStateEnabled = !options.SkipEnvironmentState
  const aituberEnabled = !options.SkipAituber
  const touchDesignerEnabled = !options.SkipTouchDesignerGui
  const thoughtCoreEnabled = Boolean(options.EnableThoughtCore)
  const voicevoxEnabled = !options.SkipVoicevoxCheck && aituberEnabled
  const environmentIndicatorsEnabled =
    exposeEnvironmentStatus && environmentStateEnabled

  const [
    homeTcp,
    homeHealth,
    environmentTcp,
    environmentHttp,
    aituberTcp,
    aituberHttp,
    tdTcp,
    tdHttp,
    thoughtCoreTcp,
    thoughtCoreHttp,
    voicevoxTcp,
    voicevoxHttp,
    environmentIndicators
  ] = await Promise.all([
    checkTcpIf(homeAssistantBridgeEnabled, options.HomeAssistantBridgePort),
    checkHttpIf(
      homeAssistantBridgeEnabled,
      `http://127.0.0.1:${options.HomeAssistantBridgePort}/operator`
    ),
    checkTcpIf(environmentStateEnabled, options.EnvironmentStatePort),
    checkHttpIf(environmentStateEnabled, `http://127.0.0.1:${options.EnvironmentStatePort}/health`),
    checkTcpIf(aituberEnabled, options.AituberPort),
    checkHttpIf(aituberEnabled, `http://127.0.0.1:${options.AituberPort}`),
    checkTcpIf(touchDesignerEnabled, options.TouchDesignerGuiPort),
    checkHttpIf(touchDesignerEnabled, `http://127.0.0.1:${options.TouchDesignerGuiPort}`),
    checkTcpIf(thoughtCoreEnabled, options.ThoughtCorePort, thoughtCoreHost),
    checkHttpIf(thoughtCoreEnabled, `${thoughtCoreUrl}/health`),
    checkTcpIf(voicevoxEnabled, voicevoxPort),
    checkHttpIf(voicevoxEnabled, `${voicevoxUrl}/version`),
    fetchJsonIf(
      environmentIndicatorsEnabled,
      `http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`
    )
  ])
  const homeHttp = homeHealth && homeHealth.statusCode
    ? {
        ok: homeHealth.statusCode >= 200 && homeHealth.statusCode < 500,
        detail: homeHealth.detail || `HTTP ${homeHealth.statusCode}`
      }
    : {
        ok: Boolean(homeHealth && homeHealth.ok),
        detail: homeHealth && homeHealth.detail || 'home-control bridge health unavailable'
      }
  const services = {
    home_assistant_bridge: serviceState({
      entry: pids.home_assistant_bridge,
      tcp: homeTcp,
      http: homeHttp,
      requireHttp: true
    }),
    environment_state_server: serviceState({
      entry: pids.environment_state_server,
      tcp: environmentTcp,
      http: environmentHttp,
      requireHttp: true
    }),
    mediapipe: serviceState({
      entry: mediapipeEntry,
      processOnly: true
    }),
    vision_snapshot_processor: serviceState({
      entry: pids.vision_snapshot_processor,
      processOnly: true
    }),
    aituber_kit: serviceState({
      entry: pids.aituber_kit,
      tcp: aituberTcp,
      http: aituberHttp,
      requireHttp: true
    }),
    touchdesigner_control_gui: serviceState({
      entry: pids.touchdesigner_control_gui,
      tcp: tdTcp,
      http: tdHttp,
      requireHttp: true
    }),
    thought_core_api: serviceState({
      entry: pids.thought_core_api,
      tcp: thoughtCoreTcp,
      http: thoughtCoreHttp,
      requireHttp: true
    }),
    thought_core_watcher: serviceState({
      entry: pids.thought_core_watcher,
      processOnly: true
    }),
    voicevox: serviceState({
      entry: null,
      tcp: voicevoxTcp,
      http: voicevoxHttp,
      requireHttp: true
    })
  }
  const startupTiming = updateStartupTimingSummary({
    profileId: selectedProfileId,
    options,
    services
  })

  return {
    ok: true,
    timestamp: nowIso(),
    workspaceRoot: WORKSPACE_ROOT,
    operation: operationState(),
    profileConfigState,
    services,
    startupTiming,
    diagnosticSurfaces: diagnosticSurfacesSummary(),
    environment:
      exposeEnvironmentStatus && environmentIndicators.ok && environmentIndicators.payload
        ? compactEnvironmentForLauncherStatus(environmentIndicators.payload)
        : null,
    environmentIndicatorState: {
      exposed: exposeEnvironmentStatus,
      payload_policy: exposeEnvironmentStatus
        ? 'compact_whitelist'
        : 'hidden_remote_launcher',
      ok: Boolean(environmentIndicators.ok),
      detail: environmentIndicators.detail || '-',
      snapshot_id:
        environmentIndicators.ok && environmentIndicators.payload
          ? environmentIndicators.payload.snapshot_id || ''
          : '',
      stale:
        environmentIndicators.ok && environmentIndicators.payload
          ? Boolean(environmentIndicators.payload.stale)
          : true,
      age_ms:
        environmentIndicators.ok && environmentIndicators.payload
          ? environmentIndicators.payload.age_ms ?? null
          : null
    },
    homeControlConfigState: compactHomeControlConfigState(
      options,
      homeHealth.ok ? homeHealth.payload : null
    )
  }
}

const readTextTail = (filePath, maxBytes = 128 * 1024) => {
  try {
    const stat = fs.statSync(filePath)
    const size = Math.min(stat.size, maxBytes)
    const fd = fs.openSync(filePath, 'r')
    const buffer = Buffer.alloc(size)
    fs.readSync(fd, buffer, 0, size, stat.size - size)
    fs.closeSync(fd)
    return stripAnsiControlSequences(buffer.toString('utf8'))
  } catch {
    return ''
  }
}

const getState = async () => {
  const config = readLauncherConfig()
  const selectedProfileId = config.selectedProfileId || PRIMARY_PROFILE_ID
  const options = normalizeOptions(selectedProfileId, config.options || {})
  const preview = previewCommand(selectedProfileId, options)
  const status = await getStatus()
  const demoSafeSettings = effectiveDemoSafeSettings()
  return {
    ok: true,
    projectRoot: PROJECT_ROOT,
    workspaceRoot: WORKSPACE_ROOT,
    stateDir: STATE_DIR,
    portMode: PORT_MODE,
    profiles: readProfiles(),
    config: {
      selectedProfileId,
      options
    },
    launcherState: readLauncherState(),
    operation: operationState(),
    status,
    startupTiming: status.startupTiming,
    diagnosticSurfaces: status.diagnosticSurfaces,
    demoSafeSettings,
    demoReadinessStatus: demoReadinessStatus(demoSafeSettings, status),
    endpoints: getEndpoints(options),
    preview,
    logTail: readTextTail(STACK_LOG_FILE)
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
  const profileId = config.selectedProfileId || PRIMARY_PROFILE_ID
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
  if (request.method === 'OPTIONS') {
    const headers =
      requestUrl.pathname === '/api/status' ? launcherStatusCorsHeaders() : {}
    sendJson(response, 204, {}, headers)
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/state') {
    sendJson(response, 200, await getState())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/status') {
    sendJson(response, 200, await getStatus(), launcherStatusCorsHeaders())
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
    sendJson(response, 200, { ok: true, logTail: readTextTail(STACK_LOG_FILE) })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/preview') {
    const body = await readBody(request)
    sendJson(
      response,
      200,
      previewCommand(body.profileId || PRIMARY_PROFILE_ID, body.options || {})
    )
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/save-config') {
    const body = await readBody(request)
    const profileId = body.profileId || PRIMARY_PROFILE_ID
    const profileError = requireKnownProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const options = normalizeOptions(profileId, body.options || {})
    saveConfig(profileId, options)
    const demoSafeSettings = body.demoSettings
      ? saveDemoSafeSettings(body.demoSettings)
      : effectiveDemoSafeSettings()
    sendJson(response, 200, { ok: true, profileId, options, demoSafeSettings })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/start') {
    const body = await readBody(request)
    const profileId = body.profileId || PRIMARY_PROFILE_ID
    const profileError = requireKnownProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const result = await runExclusiveStackOperation(
      'start',
      async () => startStack(profileId, body.options || {})
    )
    sendJson(response, result.statusCode, result.payload)
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/stop') {
    const body = await readBody(request)
    const config = readLauncherConfig()
    const profileId = (body && body.profileId) || config.selectedProfileId || PRIMARY_PROFILE_ID
    const profileError = requireKnownProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
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
    const profileId = (body && body.profileId) || config.selectedProfileId || PRIMARY_PROFILE_ID
    const profileError = requireKnownProfile(profileId)
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
    const config = readLauncherConfig()
    const profileId = config.selectedProfileId || PRIMARY_PROFILE_ID
    const profileError = requireKnownProfile(profileId)
    if (profileError) {
      sendJson(response, 400, profileError)
      return
    }
    const options = normalizeOptions(profileId, config.options || {})
    sendJson(
      response,
      200,
      await runScriptAndCollect(
        SYSTEM_SCRIPT,
        buildSystemStatusArgs(profileId, options),
        30000
      )
    )
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/shutdown') {
    sendJson(response, 200, {
      ok: true,
      message: 'launcher_shutdown_scheduled'
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
        requestUrl.pathname === '/api/status' ? launcherStatusCorsHeaders() : {}
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

ensureRuntimeDirs()
server.listen(PORT, HOST, () => {
  const url = `http://${HOST}:${PORT}`
  console.log(`Sword System Launcher: ${url}`)
  console.log(`Workspace root: ${WORKSPACE_ROOT}`)
  if (OPEN_BROWSER) {
    openBrowser(url)
  }
})
