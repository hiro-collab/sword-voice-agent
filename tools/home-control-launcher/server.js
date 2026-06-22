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
  'ThoughtCorePort'
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

const appendStackLog = (content) => {
  ensureRuntimeDirs()
  const incomingBytes = Buffer.isBuffer(content)
    ? content.length
    : Buffer.byteLength(String(content), 'utf8')
  rotateStackLogIfNeeded(incomingBytes)

  if (Buffer.isBuffer(content)) {
    fs.appendFileSync(STACK_LOG_FILE, content)
  } else {
    fs.appendFileSync(STACK_LOG_FILE, content, 'utf8')
  }
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
        HOME_CONTROL_STACK_STATE_DIR: STATE_DIR
      }
    })
  } catch (error) {
    throw error
  }

  const state = {
    startedAt: nowIso(),
    supervisorPid: child.pid,
    commandLine: preview.commandLine,
    profileId
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

const stopStack = async (body) => {
  const config = readLauncherConfig()
  const profileId = (body && body.profileId) || config.selectedProfileId || PRIMARY_PROFILE_ID
  const profileError = requireKnownProfile(profileId)
  if (profileError) {
    return profileError
  }
  const options = normalizeOptions(profileId, config.options || {})
  const scriptArgs = ['stop', '-Profile', opsProfileFor(profileId), '-Force']
  const beforeStopVerification = await collectStackStopVerification(options)
  const result = await runScriptAndCollect(SYSTEM_SCRIPT, scriptArgs, 45000)
  const stopVerification = await waitForStackStopVerification(options)
  const ok = Boolean(result.ok && stopVerification.ok)
  const payload = {
    ...result,
    ok,
    beforeStopVerification,
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

const collectStackStopVerification = async (options) => {
  const pidFileExists = fs.existsSync(PID_FILE)
  const pidState = readPidState()
  const recordedProcesses = Array.isArray(pidState.processes)
    ? pidState.processes
    : []
  const aliveRecorded = recordedProcesses
    .filter((entry) => isProcessAlive(entry.pid))
    .map(compactPidEntry)
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
    checkedPortCount: checkedPorts.length,
    openPorts
  }
}

const waitForStackStopVerification = async (options) => {
  const deadline = Date.now() + STOP_VERIFY_TIMEOUT_MS
  let verification = await collectStackStopVerification(options)
  while (!verification.ok && Date.now() < deadline) {
    await sleep(STOP_VERIFY_INTERVAL_MS)
    verification = await collectStackStopVerification(options)
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
    'effective_state',
    'confidence_label',
    'effective_confidence_label',
    'authority',
    'effective_authority',
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

const compactActionReadiness = (action) => {
  if (!isPlainObject(action)) {
    return null
  }
  const compact = copyFields(action, [
    'action_id',
    'label',
    'appliance_id',
    'target_label',
    'verb',
    'expected_state',
    'control_type',
    'state_authority',
    'verification_mode',
    'state_tracking',
    'proof_ceiling',
    'live_test_candidate',
    'live_test_readiness',
    'live_test_blockers',
    'restore_action_id',
    'stop_action_id',
    'terminal_action',
    'safety_requirements',
    'available',
    'noop',
    'reason'
  ])
  const recheck = copyFields(action.recheck_visibility, [
    'status',
    'evidence_class',
    'physical_state_source',
    'proof_ceiling',
    'live_test_readiness',
    'live_test_blockers'
  ])
  if (Object.keys(recheck).length > 0) {
    compact.recheck_visibility = recheck
  }
  return compact
}

const compactActionReadinessSummary = (summary) => {
  if (!isPlainObject(summary)) {
    return null
  }
  return copyFields(summary, [
    'schema_version',
    'by_readiness',
    'proof_ceilings',
    'live_test_candidate_ids',
    'blocked_live_test_candidate_ids',
    'test_now_count',
    'blocked_candidate_count'
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
  const actions = Array.isArray(environment.actions)
    ? environment.actions
    : []
  const stateQueries = isPlainObject(environment.state_queries)
    ? environment.state_queries
    : {}
  const sources = isPlainObject(environment.sources) ? environment.sources : {}
  const vision = isPlainObject(environment.vision) ? environment.vision : {}
  const actionReadiness = compactActionReadinessSummary(environment.action_readiness)
  const compact = {
    state_queries: {},
    sources: {},
    vision: {},
    appliances: {},
    actions: [],
    action_readiness: actionReadiness || {
      schema_version: 'home_control_action_readiness.v0',
      by_readiness: {},
      proof_ceilings: {},
      live_test_candidate_ids: [],
      blocked_live_test_candidate_ids: [],
      test_now_count: 0,
      blocked_candidate_count: 0
    }
  }

  for (const applianceId of Object.keys(appliances).sort()) {
    const appliance = compactApplianceSignal(appliances[applianceId])
    if (appliance) {
      compact.appliances[applianceId] = appliance
    }
  }

  compact.actions = actions
    .map(compactActionReadiness)
    .filter(Boolean)

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

  const [
    homeTcp,
    homeHttp,
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
    checkTcp(options.HomeAssistantBridgePort),
    checkHttp(`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`, 2500),
    checkHttp(`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`, 2500).then((result) =>
      result.ok
        ? fetchJson(`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`, 2500)
        : Promise.resolve({
            ok: false,
            statusCode: 0,
            detail: result.detail || 'home-control bridge health unavailable'
          })
    ),
    checkTcp(options.EnvironmentStatePort),
    checkHttp(`http://127.0.0.1:${options.EnvironmentStatePort}/health`),
    checkTcp(options.AituberPort),
    checkHttp(`http://127.0.0.1:${options.AituberPort}`),
    checkTcp(options.TouchDesignerGuiPort),
    checkHttp(`http://127.0.0.1:${options.TouchDesignerGuiPort}`),
    checkTcp(options.ThoughtCorePort, thoughtCoreHost),
    checkHttp(`${thoughtCoreUrl}/health`),
    checkTcp(voicevoxPort),
    checkHttp(`${voicevoxUrl}/version`),
    exposeEnvironmentStatus
      ? fetchJson(`http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`)
      : Promise.resolve({
          ok: false,
          statusCode: 0,
          detail: 'environment status hidden for remote launcher'
        })
  ])

  return {
    ok: true,
    timestamp: nowIso(),
    workspaceRoot: WORKSPACE_ROOT,
    operation: operationState(),
    profileConfigState,
    services: {
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
    },
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
    return buffer.toString('utf8')
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
    demoSafeSettings,
    demoReadinessStatus: demoReadinessStatus(demoSafeSettings, status),
    endpoints: getEndpoints(options),
    preview,
    logTail: readTextTail(STACK_LOG_FILE)
  }
}

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
