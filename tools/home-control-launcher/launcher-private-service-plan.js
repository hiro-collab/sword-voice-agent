'use strict'

const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { TextDecoder } = require('node:util')

const { assertAuthority, canonicalJsonSha256, deepFreeze } = require('./launcher-supervisor-contract')

const PLAN_DIRECTORY = 'launcher-private-plan.v1'
const PLAN_FILE = 'launcher-private-service-plan.v1.json'
const PLAN_TEMP_FILE = 'launcher-private-service-plan.v1.json.tmp'
const MAX_PLAN_BYTES = 256 * 1024
const PROFILE_ID = 'thought-core-v0'
const EFFECTIVE_CONFIG_SCHEMA = 'launcher_effective_config.v1'
const CAMERA_POLICIES = new Set(['required', 'camera_excluded_by_profile'])
const SHA256 = /^[a-f0-9]{64}$/u
const SERVICE_ID = /^[a-z][a-z0-9_]{0,63}$/u
const ENVIRONMENT_NAME = /^[A-Z][A-Z0-9_]{0,127}$/u
const RESERVED_ENVIRONMENT_NAMES = new Set([
  'SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE',
  'SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF'
])
const PLAN_DOCUMENT_FIELDS = [
  'schema_version', 'graph_sha256', 'binding_sha256', 'profile_id',
  'effective_config_sha256', 'camera_policy', 'worker_file_path', 'services'
]
const PLAN_SERVICE_FIELDS = [
  'service_id', 'file_path', 'arguments', 'working_directory', 'environment',
  'remove_environment', 'clear_inherited_environment', 'listener_port'
]
const SAFE_CODE = new Set([
  'private_plan_authority_invalid',
  'private_plan_config_invalid',
  'private_plan_dependency_missing',
  'private_plan_identity_invalid',
  'private_plan_profile_invalid',
  'private_plan_root_invalid',
  'private_plan_write_failed'
])
const PROVIDER_ENVIRONMENT_NAMES = [
  'OPENAI_API_KEY',
  'OPENAI_BASE_URL',
  'OPENAI_MODEL',
  'THOUGHT_CORE_LLM_API_KEY',
  'THOUGHT_CORE_LLM_BASE_URL',
  'THOUGHT_CORE_LLM_MODEL'
]
const HOME_BRIDGE_ENVIRONMENT_NAMES = [
  'HOME_ASSISTANT_TOKEN'
]
const THOUGHT_CORE_ENVIRONMENT_NAMES = [
  'CODEX_CLI_PATH',
  'OPENAI_BASE_URL',
  'OPENAI_MODEL',
  'SWORD_THOUGHT_CORE_PERSONA',
  'THOUGHT_CORE_ACTION_LLM_ENABLED',
  'THOUGHT_CORE_CODEX_CLI_APPROVAL',
  'THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES',
  'THOUGHT_CORE_CODEX_CLI_CWD',
  'THOUGHT_CORE_CODEX_CLI_EPHEMERAL',
  'THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION',
  'THOUGHT_CORE_CODEX_CLI_MAX_CHARS',
  'THOUGHT_CORE_CODEX_CLI_MODE',
  'THOUGHT_CORE_CODEX_CLI_MODEL',
  'THOUGHT_CORE_CODEX_CLI_MODEL_REASONING_EFFORT',
  'THOUGHT_CORE_CODEX_CLI_PROFILE',
  'THOUGHT_CORE_CODEX_CLI_PROJECT_ROOT',
  'THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT',
  'THOUGHT_CORE_CODEX_CLI_SANDBOX',
  'THOUGHT_CORE_CODEX_CLI_TIMEOUT_S',
  'THOUGHT_CORE_CODEX_CLI_VERBOSITY',
  'THOUGHT_CORE_CODEX_CLI_VERSION_POLICY',
  'THOUGHT_CORE_CODEX_CLI_VERSION_TIMEOUT_S',
  'THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT',
  'THOUGHT_CORE_FORCE_NO_PROVIDER',
  'THOUGHT_CORE_HOME_HTTP_TIMEOUT_S',
  'THOUGHT_CORE_LLM_ADAPTER',
  'THOUGHT_CORE_LLM_BASE_URL',
  'THOUGHT_CORE_LLM_ENABLED',
  'THOUGHT_CORE_LLM_MAX_CHARS',
  'THOUGHT_CORE_LLM_MODEL',
  'THOUGHT_CORE_LLM_PROVIDER',
  'THOUGHT_CORE_LLM_TIMEOUT_S',
  'THOUGHT_CORE_PERSONA',
  'THOUGHT_CORE_ROOM_LIGHT_WAIT_TIMEOUT_MS',
  'THOUGHT_CORE_TOOLS_ADAPTER'
]

class LauncherPrivatePlanError extends Error {
  constructor (code) {
    super(SAFE_CODE.has(code) ? code : 'private_plan_config_invalid')
    this.name = 'LauncherPrivatePlanError'
    this.code = this.message
  }
}

const fail = (code) => { throw new LauncherPrivatePlanError(code) }
const isPlainObject = (value) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const loopbackHost = (value) => String(value || '').trim() === '0.0.0.0' ? '127.0.0.1' : String(value || '').trim()
const requireExactKeys = (value, expected, code = 'private_plan_config_invalid') => {
  if (!isPlainObject(value) || Object.keys(value).sort().join(',') !== [...expected].sort().join(',')) fail(code)
}

const exactAbsoluteDirectory = (value, { existsSync = fs.existsSync, lstatSync = fs.lstatSync } = {}) => {
  if (typeof value !== 'string' || !path.isAbsolute(value) || value.includes('\u0000')) fail('private_plan_root_invalid')
  const resolved = path.resolve(value)
  if (!existsSync(resolved)) fail('private_plan_root_invalid')
  const stat = lstatSync(resolved)
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail('private_plan_root_invalid')
  return resolved
}

const exactAbsoluteFile = (value, { existsSync = fs.existsSync, lstatSync = fs.lstatSync } = {}) => {
  if (typeof value !== 'string' || !path.isAbsolute(value) || value.includes('\u0000')) fail('private_plan_dependency_missing')
  const resolved = path.resolve(value)
  if (!existsSync(resolved)) fail('private_plan_dependency_missing')
  const stat = lstatSync(resolved)
  if (!stat.isFile() || stat.isSymbolicLink()) fail('private_plan_dependency_missing')
  return resolved
}

const readDotEnv = (filePath, readFileSync = fs.readFileSync) => {
  let text = ''
  try { text = readFileSync(filePath, 'utf8') } catch { return {} }
  const values = {}
  for (const line of String(text).split(/\r?\n/u)) {
    const match = /^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/u.exec(line)
    if (!match || line.trimStart().startsWith('#')) continue
    let value = match[2]
    if (value.length >= 2 && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))) {
      value = value.slice(1, -1)
    }
    if (!value.includes('\u0000') && value.length <= 8192) values[match[1]] = value
  }
  return values
}

const defaultResolveExecutable = (name) => {
  const finder = process.platform === 'win32' ? 'where.exe' : 'which'
  const result = spawnSync(finder, [name], {
    encoding: 'utf8',
    windowsHide: true,
    shell: false,
    timeout: 5000
  })
  if (result.status !== 0) fail('private_plan_dependency_missing')
  const candidate = String(result.stdout || '').split(/\r?\n/u).map((row) => row.trim()).find(Boolean)
  if (!candidate || !path.isAbsolute(candidate)) fail('private_plan_dependency_missing')
  return candidate
}

const validatedExecutable = (name, resolveExecutable, io) => {
  let candidate
  try { candidate = resolveExecutable(name) } catch { fail('private_plan_dependency_missing') }
  return exactAbsoluteFile(candidate, io)
}

const boundedEnvironment = (value) => {
  if (!isPlainObject(value) || Object.keys(value).length > 64) fail('private_plan_config_invalid')
  const result = {}
  for (const [name, raw] of Object.entries(value)) {
    if (RESERVED_ENVIRONMENT_NAMES.has(name) || !ENVIRONMENT_NAME.test(name) ||
        typeof raw !== 'string' || raw.length > 8192 || raw.includes('\u0000')) {
      fail('private_plan_config_invalid')
    }
    result[name] = raw
  }
  return result
}

const inheritedRuntimeEnvironment = (environment) => {
  const result = {}
  const names = {
    COMSPEC: ['COMSPEC', 'ComSpec'],
    SYSTEMROOT: ['SYSTEMROOT', 'SystemRoot'],
    WINDIR: ['WINDIR', 'windir'],
    PATH: ['PATH', 'Path'],
    PATHEXT: ['PATHEXT', 'PathExt'],
    TEMP: ['TEMP', 'Temp'],
    TMP: ['TMP', 'Tmp'],
    USERPROFILE: ['USERPROFILE', 'UserProfile'],
    LOCALAPPDATA: ['LOCALAPPDATA', 'LocalAppData'],
    APPDATA: ['APPDATA', 'AppData']
  }
  for (const [name, candidates] of Object.entries(names)) {
    const value = candidates.map((candidate) => environment[candidate]).find((candidate) => typeof candidate === 'string')
    if (typeof value === 'string' && value.length <= 8192 && !value.includes('\u0000')) result[name] = value
  }
  Object.assign(result, {
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    NO_COLOR: '1',
    FORCE_COLOR: '0',
    TERM: 'dumb'
  })
  return boundedEnvironment(result)
}

const selectedEnvironment = (names, processEnvironment, dotEnv) => {
  const result = {}
  for (const name of names) {
    const value = processEnvironment[name] || dotEnv[name]
    if (typeof value === 'string' && value.length <= 8192 && !value.includes('\u0000')) result[name] = value
  }
  return result
}

const requireCanonicalOptions = (options, authority) => {
  if (!isPlainObject(options)) fail('private_plan_config_invalid')
  const fixedPorts = {
    HomeAssistantBridgePort: 'home_assistant_bridge',
    EnvironmentStatePort: 'environment_state_server',
    MediapipePort: 'mediapipe_camera_hub_stack',
    VisionSnapshotProcessorPort: 'vision_snapshot_processor',
    AituberPort: 'aituber_kit',
    TouchDesignerGuiPort: 'touchdesigner_control_gui',
    ThoughtCorePort: 'thought_core_api',
    OpenAIBrokerPort: 'openai_provider_broker'
  }
  for (const [optionName, serviceId] of Object.entries(fixedPorts)) {
    const spec = authority.graph.services.find((service) => service.service_id === serviceId)
    if (!spec || Number(options[optionName]) !== Number(spec.port.loopback_port)) fail('private_plan_config_invalid')
  }
  const voicevoxSpec = authority.graph.services.find((service) => service.service_id === 'voicevox')
  const camera = authority.graph.services.find((service) => service.service_id === 'mediapipe_camera_hub_stack')
  if (
    !voicevoxSpec ||
    !camera ||
    Number(options.VoicevoxReadyTimeoutSeconds) * 1000 !== Number(voicevoxSpec.ready_deadline_ms) ||
    Number(options.MediapipeReadyTimeoutSeconds) * 1000 !== Number(camera.ready_deadline_ms)
  ) {
    fail('private_plan_config_invalid')
  }
  if (
    options.SkipHomeAssistantBridge ||
    options.SkipEnvironmentState ||
    options.SkipAituber ||
    options.SkipTouchDesignerGui ||
    !options.EnableThoughtCore ||
    !options.EnableThoughtCoreWatch ||
    options.SkipVoicevoxCheck ||
    options.ThoughtCoreLlmProvider !== 'sword-openai-broker' ||
    options.MediapipeMode !== 'mediamtx'
  ) {
    fail('private_plan_profile_invalid')
  }
  for (const name of [
    'HomeAssistantBridgeHost',
    'AituberHost',
    'TouchDesignerGuiHost',
    'ThoughtCoreHost'
  ]) {
    if (!['127.0.0.1', '0.0.0.0', 'localhost'].includes(String(options[name] || '').toLowerCase())) {
      fail('private_plan_config_invalid')
    }
  }
  const rawVoicevox = String(options.VoicevoxUrl || 'http://127.0.0.1:50021')
  let voicevox
  try { voicevox = new URL(rawVoicevox) } catch { fail('private_plan_config_invalid') }
  if (
    voicevox.protocol !== 'http:' ||
    !['127.0.0.1', 'localhost'].includes(voicevox.hostname.toLowerCase()) ||
    Number(voicevox.port || 80) !== 50021
  ) {
    fail('private_plan_config_invalid')
  }
}

const ownedPlan = ({ serviceId, filePath, args, cwd, environment, listenerPort, removeEnvironment = [] }) => ({
  service_id: serviceId,
  file_path: filePath,
  arguments: args.map(String),
  working_directory: cwd,
  environment: boundedEnvironment(environment),
  remove_environment: [...removeEnvironment],
  clear_inherited_environment: true,
  listener_port: listenerPort
})

const deriveEffectiveConfigIdentity = ({ profileId, options, authority }) => {
  try { assertAuthority(authority) } catch { fail('private_plan_authority_invalid') }
  if (profileId !== PROFILE_ID || authority.graph.profile_id !== PROFILE_ID) fail('private_plan_profile_invalid')
  requireCanonicalOptions(options, authority)
  if (options.SkipMediapipe === true && options.SkipVisionSnapshotProcessor !== true) {
    fail('private_plan_config_invalid')
  }
  const cameraPolicy = options.SkipMediapipe === true
    ? 'camera_excluded_by_profile'
    : 'required'
  const identityOptions = { ...options }
  if (identityOptions.MediapipeCameraSelectionKey !== '') {
    identityOptions.MediapipeCameraName = ''
  }
  const configDocument = {
    schema_version: EFFECTIVE_CONFIG_SCHEMA,
    profile_id: profileId,
    options: identityOptions
  }
  return deepFreeze({
    profile_id: profileId,
    effective_config_sha256: canonicalJsonSha256(configDocument),
    camera_policy: cameraPolicy
  })
}

const requireEffectiveConfigIdentity = (candidate, derived) => {
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate) ||
      Object.keys(candidate).sort().join(',') !== 'camera_policy,effective_config_sha256,profile_id' ||
      candidate.profile_id !== derived.profile_id ||
      candidate.effective_config_sha256 !== derived.effective_config_sha256 ||
      !CAMERA_POLICIES.has(candidate.camera_policy) ||
      candidate.camera_policy !== derived.camera_policy) {
    fail('private_plan_config_invalid')
  }
}

const compilePrivateServicePlan = ({
  repositoryRoot,
  workspaceRoot,
  privateRuntimeRoot,
  profileId,
  options,
  configIdentity,
  authority,
  processEnvironment = process.env,
  resolveExecutable = defaultResolveExecutable,
  nonceFactory = () => crypto.randomBytes(16).toString('hex'),
  io = {}
}) => {
  try { assertAuthority(authority) } catch { fail('private_plan_authority_invalid') }
  const effectiveIo = {
    existsSync: io.existsSync || fs.existsSync,
    lstatSync: io.lstatSync || fs.lstatSync
  }
  const readFileSync = io.readFileSync || fs.readFileSync
  const repo = exactAbsoluteDirectory(repositoryRoot, effectiveIo)
  const workspace = exactAbsoluteDirectory(workspaceRoot, effectiveIo)
  if (typeof privateRuntimeRoot !== 'string' || !path.isAbsolute(privateRuntimeRoot) || privateRuntimeRoot.includes('\u0000')) {
    fail('private_plan_root_invalid')
  }
  if (profileId !== PROFILE_ID || authority.graph.profile_id !== PROFILE_ID) fail('private_plan_profile_invalid')
  requireCanonicalOptions(options, authority)
  const derivedConfigIdentity = deriveEffectiveConfigIdentity({ profileId, options, authority })
  requireEffectiveConfigIdentity(configIdentity, derivedConfigIdentity)

  const roots = {
    home: path.join(workspace, 'organs', 'action', 'home-assistant-server'),
    environment: path.join(workspace, 'organs', 'environment', 'environment-state-server'),
    mediapipe: path.join(workspace, 'organs', 'reflex', 'mediapipe-sword-sign'),
    vision: path.join(workspace, 'organs', 'environment', 'vision-snapshot-processor'),
    aituber: path.join(workspace, 'organs', 'expression', 'aituber-kit'),
    display: path.join(workspace, 'organs', 'display', 'touchdesigner-ai-controller'),
    speech: path.join(workspace, 'organs', 'speech-input', 'ai-talk-core')
  }
  for (const [name, root] of Object.entries(roots)) {
    if ((name === 'vision' && (options.SkipVisionSnapshotProcessor || options.SkipMediapipe)) ||
        (name === 'mediapipe' && options.SkipMediapipe)) continue
    roots[name] = exactAbsoluteDirectory(root, effectiveIo)
  }

  const configCandidate = String(options.HomeControlConfigPath || path.join(roots.home, 'config', 'home-control.yaml'))
  const configPath = exactAbsoluteFile(
    path.isAbsolute(configCandidate) ? configCandidate : path.join(workspace, configCandidate),
    effectiveIo
  )
  const localLiveConfig = path.join(workspace, 'local', 'env', 'home-control.live.yaml')
  const configText = String(readFileSync(configPath, 'utf8'))
  if (
    effectiveIo.existsSync(localLiveConfig) &&
    path.resolve(configPath).toLowerCase() !== path.resolve(localLiveConfig).toLowerCase() &&
    /script\.demo_light_(?:on|off)/u.test(configText)
  ) {
    fail('private_plan_config_invalid')
  }

  const uv = validatedExecutable('uv', resolveExecutable, effectiveIo)
  const node = validatedExecutable('node', resolveExecutable, effectiveIo)
  const powershell = validatedExecutable('pwsh', resolveExecutable, effectiveIo)
  const nextEntrypoint = exactAbsoluteFile(
    path.join(roots.aituber, 'node_modules', 'next', 'dist', 'bin', 'next'),
    effectiveIo
  )
  const baseline = inheritedRuntimeEnvironment(processEnvironment)
  const homeEnvPath = exactAbsoluteFile(path.join(roots.home, '.env'), effectiveIo)
  const homeDotEnv = readDotEnv(homeEnvPath, readFileSync)
  const thoughtDotEnv = readDotEnv(path.join(repo, '.env'), readFileSync)
  const homeToken = processEnvironment.HOME_CONTROL_API_TOKEN || homeDotEnv.HOME_CONTROL_API_TOKEN || ''
  const environmentToken = processEnvironment.ENVIRONMENT_API_TOKEN || homeDotEnv.ENVIRONMENT_API_TOKEN || homeToken
  const homeBridgeSecrets = selectedEnvironment(HOME_BRIDGE_ENVIRONMENT_NAMES, processEnvironment, homeDotEnv)
  if (homeToken.length < 16 || environmentToken.length < 16 ||
      typeof homeBridgeSecrets.HOME_ASSISTANT_TOKEN !== 'string' || homeBridgeSecrets.HOME_ASSISTANT_TOKEN.length < 16) {
    fail('private_plan_config_invalid')
  }

  const homeHost = loopbackHost(options.HomeAssistantBridgeHost)
  const aituberHost = loopbackHost(options.AituberHost)
  const displayHost = loopbackHost(options.TouchDesignerGuiHost)
  const thoughtHost = loopbackHost(options.ThoughtCoreHost)
  const thoughtBase = `http://${thoughtHost}:${options.ThoughtCorePort}`
  const feedbackEnabled = '1'
  const thoughtEnvironment = {
    ...baseline,
    ...selectedEnvironment(THOUGHT_CORE_ENVIRONMENT_NAMES, processEnvironment, thoughtDotEnv),
    THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: feedbackEnabled,
    THOUGHT_CORE_LLM_ENABLED: '1',
    THOUGHT_CORE_LLM_PROVIDER: 'sword-openai-broker',
    THOUGHT_CORE_LLM_BASE_URL: `http://127.0.0.1:${options.OpenAIBrokerPort}/v1`,
    THOUGHT_CORE_LLM_MODEL: 'gpt-4o-mini',
    THOUGHT_CORE_LLM_TIMEOUT_S: '12',
    THOUGHT_CORE_ACTION_LLM_ENABLED: '0',
    THOUGHT_CORE_TOOLS_ADAPTER: 'home_control',
    HOME_CONTROL_BRIDGE_URL: `http://${homeHost}:${options.HomeAssistantBridgePort}`,
    HOME_ASSISTANT_BRIDGE_URL: `http://${homeHost}:${options.HomeAssistantBridgePort}`,
    HOME_CONTROL_API_TOKEN: homeToken,
    ENVIRONMENT_STATE_URL: `http://127.0.0.1:${options.EnvironmentStatePort}/environment/current`,
    ENVIRONMENT_API_TOKEN: environmentToken,
    SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST: path.join(privateRuntimeRoot, 'thought-core-api', 'controller.json'),
    SWORD_THOUGHT_CORE_LAUNCH_NONCE: String(nonceFactory())
  }
  const homeEnvironment = {
    ...baseline,
    ...homeBridgeSecrets,
    HOME_CONTROL_CONFIG: configPath,
    HOME_CONTROL_API_TOKEN: homeToken,
    ...(options.EnableHomeControlFaultInjection ? { HOME_CONTROL_FAULT_MODE: '1' } : {})
  }
  const environmentStateEnvironment = {
    ...baseline,
    HOME_CONTROL_API_TOKEN: homeToken,
    ENVIRONMENT_API_TOKEN: environmentToken
  }
  const plans = [
    ownedPlan({
      serviceId: 'home_assistant_bridge',
      filePath: uv,
      args: ['run', 'python', '-m', 'uvicorn', 'home_control_bridge.main:app', '--host', options.HomeAssistantBridgeHost, '--port', options.HomeAssistantBridgePort],
      cwd: roots.home,
      environment: homeEnvironment,
      listenerPort: options.HomeAssistantBridgePort
    }),
    ownedPlan({
      serviceId: 'environment_state_server',
      filePath: uv,
      args: [
        'run', 'python', '-m', 'environment_state_server.main',
        '--host', '127.0.0.1', '--port', options.EnvironmentStatePort,
        '--ha-events-path', path.join(roots.home, '.cache', 'home_control', 'events.jsonl'),
        '--state-query-feedback-path', path.join(privateRuntimeRoot, 'feedback', 'state-query.jsonl'),
        '--camera-hub-url', `ws://127.0.0.1:${options.MediapipePort}`,
        '--home-assistant-health-url', `http://${homeHost}:${options.HomeAssistantBridgePort}/operator`,
        '--aituber-url', `http://${aituberHost}:${options.AituberPort}`,
        '--voicevox-health-url', 'http://127.0.0.1:50021/version',
        '--profile-id', derivedConfigIdentity.profile_id,
        '--effective-config-sha256', derivedConfigIdentity.effective_config_sha256,
        '--camera-policy', derivedConfigIdentity.camera_policy,
        ...((options.SkipMediapipe) ? ['--disable-camera-hub'] : []),
        ...((!options.SkipVisionSnapshotProcessor && !options.SkipMediapipe) ? ['--vision-topic-url', `ws://127.0.0.1:${options.VisionSnapshotProcessorPort}`] : [])
      ],
      cwd: roots.environment,
      environment: environmentStateEnvironment,
      listenerPort: options.EnvironmentStatePort
    }),
    ownedPlan({
      serviceId: 'openai_provider_broker',
      filePath: uv,
      args: ['run', 'python', '-m', 'sword_voice_agent.apps.openai_broker', '--port', options.OpenAIBrokerPort],
      cwd: repo,
      environment: baseline,
      removeEnvironment: PROVIDER_ENVIRONMENT_NAMES,
      listenerPort: options.OpenAIBrokerPort
    }),
    ownedPlan({
      serviceId: 'thought_core_api',
      filePath: powershell,
      args: [
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        exactAbsoluteFile(path.join(repo, 'scripts', 'start-thought-core.ps1'), effectiveIo),
        '-HostName', options.ThoughtCoreHost, '-Port', options.ThoughtCorePort,
        '-StatusDir', path.join(privateRuntimeRoot, 'thought-core-api'), '-SkipEnvImport'
      ],
      cwd: repo,
      environment: thoughtEnvironment,
      removeEnvironment: PROVIDER_ENVIRONMENT_NAMES,
      listenerPort: options.ThoughtCorePort
    }),
    ownedPlan({
      serviceId: 'aituber_kit',
      filePath: node,
      args: [
        nextEntrypoint, 'dev', '--hostname', options.AituberHost,
        '--port', options.AituberPort
      ],
      cwd: roots.aituber,
      environment: {
        ...baseline,
        THOUGHT_CORE_BASE_URL: thoughtBase,
        THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_URL: `${thoughtBase}/feedback/closed-loop`,
        THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: feedbackEnabled,
        NEXT_PUBLIC_THOUGHT_CORE_BASE_URL: thoughtBase,
        NEXT_PUBLIC_THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: feedbackEnabled,
        NEXT_PUBLIC_THOUGHT_CORE_SESSION_ID: 'aituber-kit',
        NEXT_PUBLIC_SYSTEM_CELL_AI_SERVICE: 'thought-core',
        NEXT_PUBLIC_SELECT_AI_SERVICE: 'thought-core',
        NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE: processEnvironment.NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE || 'thought-core',
        NEXT_PUBLIC_DISPLAY_RUNTIME_STATUS_URL: `http://${displayHost}:${options.TouchDesignerGuiPort}/api/status`,
        NEXT_PUBLIC_TD_CONTROL_GUI_STATUS_URL: `http://${displayHost}:${options.TouchDesignerGuiPort}/api/status`,
        NEXT_PUBLIC_ENVIRONMENT_INDICATORS_URL: `http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`,
        NEXT_PUBLIC_REFLEX_GESTURE_WS_URL: `ws://127.0.0.1:${options.MediapipePort}`,
        NEXT_PUBLIC_GESTURE_VOICE_WS_URL: `ws://127.0.0.1:${options.MediapipePort}`,
        NEXT_PUBLIC_GESTURE_VOICE_BRIDGE_ENABLED: options.SkipMediapipe ? 'false' : 'true'
      },
      listenerPort: options.AituberPort
    }),
    ownedPlan({
      serviceId: 'thought_core_watcher',
      filePath: powershell,
      args: [
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        exactAbsoluteFile(path.join(repo, 'scripts', 'start-thought-core-watch.ps1'), effectiveIo),
        '-EnvPath', path.join(repo, '.env'),
        '-AiTalkCoreRoot', roots.speech,
        '-ThoughtCoreBaseUrl', thoughtBase,
        '-StatusDir', path.join(privateRuntimeRoot, 'thought-core-watcher'),
        '-ClosedLoopFeedbackV1Mode', 'enabled',
        '-AituberPort', options.AituberPort,
        '-AituberMessageUrl', `http://${aituberHost}:${options.AituberPort}/api/messages/?clientId=thought-core&type=direct_send`
      ],
      cwd: repo,
      environment: { ...baseline, THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED: feedbackEnabled },
      listenerPort: 0
    }),
    ownedPlan({
      serviceId: 'touchdesigner_control_gui',
      filePath: node,
      args: [
        'server.js', '--workspace', workspace, '--port', options.TouchDesignerGuiPort,
        '--host', options.TouchDesignerGuiHost,
        '--home-assistant-bridge-host', homeHost,
        '--home-assistant-bridge-port', options.HomeAssistantBridgePort,
        '--environment-state-host', '127.0.0.1',
        '--environment-state-port', options.EnvironmentStatePort,
        '--aituber-host', aituberHost, '--aituber-port', options.AituberPort,
        '--aituber-url', `http://${aituberHost}:${options.AituberPort}/projection-visual/?mode=passive&hud=0`,
        '--touchdesigner-host', '127.0.0.1', '--touchdesigner-port', 9001,
        '--thought-core-host', thoughtHost, '--thought-core-port', options.ThoughtCorePort
      ],
      cwd: exactAbsoluteDirectory(path.join(roots.display, 'tools'), effectiveIo),
      environment: { ...baseline, THOUGHT_CORE_TOOLS_ADAPTER: 'home_control' },
      listenerPort: options.TouchDesignerGuiPort
    })
  ]

  if (!options.SkipMediapipe) {
    plans.push(ownedPlan({
      serviceId: 'mediapipe_camera_hub_stack',
      filePath: uv,
      args: [
        'run', 'python', 'scripts/camera_hub_stack.py',
        '--camera-name', options.MediapipeCameraName,
        '--width', options.MediapipeCameraWidth,
        '--height', options.MediapipeCameraHeight,
        '--fps', options.MediapipeCameraFps,
        '--ffmpeg-video-source', 'dshow',
        '--ffmpeg-input-codec', options.MediapipeCameraInputCodec,
        '--hub-port', options.MediapipePort,
        '--viewer-port', options.MediapipeBrowserMonitorPort,
        '--no-browser'
      ],
      cwd: roots.mediapipe,
      environment: baseline,
      listenerPort: options.MediapipePort
    }))
  }
  if (!options.SkipVisionSnapshotProcessor && !options.SkipMediapipe) {
    exactAbsoluteFile(path.join(roots.vision, 'src', 'vision_snapshot_processor', 'main.py'), effectiveIo)
    plans.push(ownedPlan({
      serviceId: 'vision_snapshot_processor',
      filePath: uv,
      args: [
        'run', 'python', '-m', 'vision_snapshot_processor.main',
        '--host', '127.0.0.1', '--port', options.VisionSnapshotProcessorPort,
        '--camera-source', 'rtsp://127.0.0.1:8554/cam0',
        '--frame-id', 'cam0', '--processor', 'room_light'
      ],
      cwd: roots.vision,
      environment: baseline,
      listenerPort: options.VisionSnapshotProcessorPort
    }))
  }

  const planIds = new Set(plans.map((plan) => plan.service_id))
  if (planIds.size !== plans.length) fail('private_plan_config_invalid')
  for (const spec of authority.graph.services) {
    if (spec.ownership === 'external') {
      if (planIds.has(spec.service_id)) fail('private_plan_config_invalid')
      continue
    }
    if (spec.requirement === 'required' && !planIds.has(spec.service_id)) fail('private_plan_config_invalid')
    if (planIds.has(spec.service_id)) {
      const plan = plans.find((candidate) => candidate.service_id === spec.service_id)
      if (Number(plan.listener_port) !== Number(spec.port.loopback_port || 0)) fail('private_plan_identity_invalid')
    }
  }
  const document = {
    schema_version: 'launcher_private_service_plans.v1',
    graph_sha256: authority.identities.graphSha256,
    binding_sha256: authority.identities.bindingSha256,
    profile_id: derivedConfigIdentity.profile_id,
    effective_config_sha256: derivedConfigIdentity.effective_config_sha256,
    camera_policy: derivedConfigIdentity.camera_policy,
    worker_file_path: powershell,
    services: plans
  }
  const serialized = JSON.stringify(document)
  if (Buffer.byteLength(serialized, 'utf8') > MAX_PLAN_BYTES) fail('private_plan_config_invalid')
  return deepFreeze({
    document,
    powershell_path: powershell,
    included_service_ids: [...planIds].sort()
  })
}

const resolvePlanPaths = (privateRuntimeRoot) => {
  if (typeof privateRuntimeRoot !== 'string' || !path.isAbsolute(privateRuntimeRoot) || privateRuntimeRoot.includes('\u0000')) {
    fail('private_plan_root_invalid')
  }
  const root = path.join(path.resolve(privateRuntimeRoot), PLAN_DIRECTORY)
  return {
    root,
    planPath: path.join(root, PLAN_FILE),
    temporaryPath: path.join(root, PLAN_TEMP_FILE)
  }
}

const boundedStringArray = (value, { maximumItems = 128, maximumLength = 4096, pattern = null } = {}) => {
  if (!Array.isArray(value) || value.length > maximumItems) fail('private_plan_config_invalid')
  const result = value.map((item) => {
    if (typeof item !== 'string' || item.length > maximumLength || item.includes('\u0000') || (pattern && !pattern.test(item))) {
      fail('private_plan_config_invalid')
    }
    return item
  })
  if (new Set(result).size !== result.length && pattern === ENVIRONMENT_NAME) fail('private_plan_config_invalid')
  return result
}

const validatePersistedPlanDocument = ({ document, configIdentity, authority, io }) => {
  try { assertAuthority(authority) } catch { fail('private_plan_authority_invalid') }
  requireExactKeys(document, PLAN_DOCUMENT_FIELDS)
  requireExactKeys(configIdentity, ['profile_id', 'effective_config_sha256', 'camera_policy'], 'private_plan_identity_invalid')
  if (document.schema_version !== 'launcher_private_service_plans.v1' ||
      document.graph_sha256 !== authority.identities.graphSha256 ||
      document.binding_sha256 !== authority.identities.bindingSha256 ||
      document.profile_id !== configIdentity.profile_id || document.profile_id !== authority.graph.profile_id ||
      document.effective_config_sha256 !== configIdentity.effective_config_sha256 ||
      !SHA256.test(document.effective_config_sha256) ||
      document.camera_policy !== configIdentity.camera_policy || !CAMERA_POLICIES.has(document.camera_policy)) {
    fail('private_plan_identity_invalid')
  }
  const workerFilePath = exactAbsoluteFile(document.worker_file_path, io)
  if (!['pwsh', 'powershell'].includes(path.basename(workerFilePath, path.extname(workerFilePath)).toLowerCase())) {
    fail('private_plan_identity_invalid')
  }
  if (!Array.isArray(document.services) || document.services.length > authority.graph.services.length) {
    fail('private_plan_config_invalid')
  }
  const planIds = new Set()
  for (const plan of document.services) {
    requireExactKeys(plan, PLAN_SERVICE_FIELDS)
    if (typeof plan.service_id !== 'string' || !SERVICE_ID.test(plan.service_id) || planIds.has(plan.service_id)) {
      fail('private_plan_config_invalid')
    }
    const spec = authority.graph.services.find((service) => service.service_id === plan.service_id)
    if (!spec || spec.ownership !== 'owned') fail('private_plan_config_invalid')
    exactAbsoluteFile(plan.file_path, io)
    exactAbsoluteDirectory(plan.working_directory, io)
    boundedStringArray(plan.arguments)
    boundedEnvironment(plan.environment)
    const removed = boundedStringArray(plan.remove_environment, {
      maximumItems: 64,
      maximumLength: 128,
      pattern: ENVIRONMENT_NAME
    })
    if (removed.some((name) => RESERVED_ENVIRONMENT_NAMES.has(name)) ||
        plan.clear_inherited_environment !== true || !Number.isSafeInteger(plan.listener_port) ||
        plan.listener_port !== Number(spec.port.loopback_port || 0)) {
      fail('private_plan_config_invalid')
    }
    planIds.add(plan.service_id)
  }
  for (const spec of authority.graph.services) {
    if (spec.ownership === 'external' && planIds.has(spec.service_id)) fail('private_plan_config_invalid')
    if (spec.ownership === 'owned' && spec.requirement === 'required' && !planIds.has(spec.service_id)) {
      fail('private_plan_config_invalid')
    }
  }
  if (document.camera_policy === 'camera_excluded_by_profile' &&
      (planIds.has('mediapipe_camera_hub_stack') || planIds.has('vision_snapshot_processor'))) {
    fail('private_plan_identity_invalid')
  }
  if (document.camera_policy === 'required' && !planIds.has('mediapipe_camera_hub_stack')) {
    fail('private_plan_identity_invalid')
  }
  return [...planIds].sort()
}

const readPrivateServicePlan = ({
  privateRuntimeRoot,
  configIdentity,
  authority,
  io = {}
}) => {
  const paths = resolvePlanPaths(privateRuntimeRoot)
  const effectiveIo = {
    existsSync: io.existsSync || fs.existsSync,
    lstatSync: io.lstatSync || fs.lstatSync
  }
  const readFileSync = io.readFileSync || fs.readFileSync
  try {
    exactAbsoluteDirectory(paths.root, effectiveIo)
    exactAbsoluteFile(paths.planPath, effectiveIo)
    const bytes = readFileSync(paths.planPath)
    if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.length > MAX_PLAN_BYTES ||
        (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf)) {
      fail('private_plan_config_invalid')
    }
    let text
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes) } catch { fail('private_plan_config_invalid') }
    let document
    try { document = JSON.parse(text) } catch { fail('private_plan_config_invalid') }
    const includedServiceIds = validatePersistedPlanDocument({
      document,
      configIdentity,
      authority,
      io: effectiveIo
    })
    return deepFreeze({
      document,
      plan_path: paths.planPath,
      powershell_path: document.worker_file_path,
      included_service_ids: includedServiceIds
    })
  } catch (error) {
    if (error instanceof LauncherPrivatePlanError) throw error
    fail('private_plan_config_invalid')
  }
}

const writePrivateServicePlan = (compiled, privateRuntimeRoot) => {
  if (!compiled || !isPlainObject(compiled.document)) fail('private_plan_config_invalid')
  const paths = resolvePlanPaths(privateRuntimeRoot)
  try {
    fs.mkdirSync(paths.root, { recursive: true, mode: 0o700 })
    const rootStat = fs.lstatSync(paths.root)
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail('private_plan_root_invalid')
    for (const target of [paths.planPath, paths.temporaryPath]) {
      if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) fail('private_plan_root_invalid')
    }
    const payload = `${JSON.stringify(compiled.document)}\n`
    if (Buffer.byteLength(payload, 'utf8') > MAX_PLAN_BYTES) fail('private_plan_config_invalid')
    fs.writeFileSync(paths.temporaryPath, payload, { encoding: 'utf8', mode: 0o600, flag: 'wx' })
    fs.renameSync(paths.temporaryPath, paths.planPath)
    try { fs.chmodSync(paths.planPath, 0o600) } catch {}
    return paths.planPath
  } catch (error) {
    try { if (fs.existsSync(paths.temporaryPath)) fs.unlinkSync(paths.temporaryPath) } catch {}
    if (error instanceof LauncherPrivatePlanError) throw error
    fail('private_plan_write_failed')
  }
}

const removePrivateServicePlan = (privateRuntimeRoot) => {
  const paths = resolvePlanPaths(privateRuntimeRoot)
  try {
    for (const target of [paths.temporaryPath, paths.planPath]) {
      if (fs.existsSync(target) && !fs.lstatSync(target).isSymbolicLink()) fs.unlinkSync(target)
    }
  } catch {
    fail('private_plan_write_failed')
  }
}

module.exports = {
  LauncherPrivatePlanError,
  MAX_PLAN_BYTES,
  PLAN_DIRECTORY,
  PLAN_FILE,
  PROFILE_ID,
  compilePrivateServicePlan,
  deriveEffectiveConfigIdentity,
  readPrivateServicePlan,
  removePrivateServicePlan,
  resolvePlanPaths,
  writePrivateServicePlan
}
