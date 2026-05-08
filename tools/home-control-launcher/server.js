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

const PUBLIC_DIR = path.join(__dirname, 'public')
const PROFILE_FILE = path.join(__dirname, 'config', 'default-profiles.json')
const OPS_SCRIPT_ROOT = path.join(PROJECT_ROOT, 'ops', 'scripts')
const SYSTEM_SCRIPT = path.join(OPS_SCRIPT_ROOT, 'system.ps1')
const STATE_DIR = resolveStackStateDir()
const LOG_DIR = path.join(STATE_DIR, 'logs')
const PID_FILE = path.join(STATE_DIR, 'pids.json')
const LAUNCHER_CONFIG_FILE = path.join(STATE_DIR, 'launcher-config.json')
const LAUNCHER_STATE_FILE = path.join(STATE_DIR, 'launcher-state.json')
const STACK_LOG_FILE = path.join(LOG_DIR, 'launcher-stack.log')
const STACK_LOG_MAX_BYTES = Number(
  process.env.HOME_CONTROL_LAUNCHER_STACK_LOG_MAX_BYTES || 5 * 1024 * 1024
)
const STACK_LOG_BACKUPS = Number(
  process.env.HOME_CONTROL_LAUNCHER_STACK_LOG_BACKUPS || 3
)

function resolveStackStateDir() {
  const configured = process.env.HOME_CONTROL_STACK_STATE_DIR || ''
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
  DifyPort: 8080,
  ThoughtCoreHost: '127.0.0.1',
  ThoughtCorePort: 18787,
  VoicevoxUrl: '',
  DifyDockerRoot: '',
  HomeControlConfigPath: '',
  MediapipeMode: 'mediamtx',
  MediapipeCameraName: 'HD Pro Webcam C920',
  MediapipeOpenBrowser: false,
  MediapipeNoBrowser: true,
  MediapipePythonGui: false,
  SkipDify: false,
  SkipVoicevoxCheck: false,
  SkipHomeAssistantBridge: false,
  SkipEnvironmentState: false,
  SkipMediapipe: false,
  SkipVisionSnapshotProcessor: false,
  SkipAituber: false,
  SkipDifyWatch: false,
  SkipTouchDesignerGui: false,
  EnableThoughtCore: false,
  EnableThoughtCoreWatch: false,
  StopExisting: true,
  EnableHomeControlFaultInjection: false
}

const OPS_PROFILE_BY_LAUNCHER_PROFILE = {
  'full-stack': 'full-local',
  'dify-external': 'full-local',
  'no-touchdesigner': 'full-local',
  'thought-core-experimental': 'thought-core-experimental',
  'aituber-only': 'aituber-only',
  'camera-debug': 'camera-debug'
}

const opsProfileFor = (profileId) =>
  OPS_PROFILE_BY_LAUNCHER_PROFILE[profileId] || profileId || 'full-local'

const NUMBER_FIELDS = new Set([
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'MediapipeBrowserMonitorPort',
  'VisionSnapshotProcessorPort',
  'AituberPort',
  'TouchDesignerGuiPort',
  'DifyPort',
  'ThoughtCorePort'
])

const STRING_FIELDS = new Set([
  'HomeAssistantBridgeHost',
  'AituberHost',
  'TouchDesignerGuiHost',
  'ThoughtCoreHost',
  'VoicevoxUrl',
  'DifyDockerRoot',
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

const readLauncherConfig = () =>
  readJsonFile(LAUNCHER_CONFIG_FILE, {
    selectedProfileId: 'full-stack',
    options: {}
  })

const readLauncherState = () => readJsonFile(LAUNCHER_STATE_FILE, {})

const readPidState = () => readJsonFile(PID_FILE, { processes: [] })

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
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'DifyPort', options.DifyPort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'ThoughtCoreHost', options.ThoughtCoreHost)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'ThoughtCorePort', options.ThoughtCorePort)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'MediapipeMode', options.MediapipeMode)
  addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'MediapipeCameraName', options.MediapipeCameraName)

  if (options.VoicevoxUrl) {
    addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'VoicevoxUrl', options.VoicevoxUrl)
  }
  if (options.DifyDockerRoot) {
    addSupportedParam(SYSTEM_SCRIPT, stackArgs, 'DifyDockerRoot', options.DifyDockerRoot)
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
  const options = normalizeOptions(profileId, optionOverrides)
  const command = buildPowerShellCommand(SYSTEM_SCRIPT, buildSystemStartArgs(profileId, options))
  return {
    ok: true,
    profileId,
    opsProfile: opsProfileFor(profileId),
    options,
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
  saveConfig(profileId, preview.options)

  appendStackLog(
    [
      '',
      `===== Home Control Launcher start ${nowIso()} =====`,
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
  const profileId = (body && body.profileId) || config.selectedProfileId || 'full-stack'
  const scriptArgs = ['stop', '-Profile', opsProfileFor(profileId), '-Force']
  if (body && body.stopDify) {
    scriptArgs.push('-StopDify')
  }
  const result = await runScriptAndCollect(SYSTEM_SCRIPT, scriptArgs, 45000)
  writeJsonFile(LAUNCHER_STATE_FILE, {
    ...readLauncherState(),
    stoppedAt: nowIso(),
    lastStop: result
  })
  return result
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
  return normalizeOptions(config.selectedProfileId || 'full-stack', config.options || {})
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
      name: 'AITuber Kit',
      url: `http://127.0.0.1:${options.AituberPort}`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Projection Visual',
      url: `http://127.0.0.1:${options.AituberPort}/projection-visual`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'AITuber Cube Vault',
      url: `http://127.0.0.1:${options.AituberPort}/cube-vault-background?fov=60&scale=1`,
      enabled: !options.SkipAituber
    },
    {
      group: 'Open in browser',
      name: 'Dify',
      url: `http://127.0.0.1:${options.DifyPort}`,
      enabled: !options.SkipDify
    },
    {
      group: 'Open in browser',
      name: 'thought-core API index',
      url: thoughtCoreUrl,
      enabled: options.EnableThoughtCore
    },
    {
      group: 'Open in browser',
      name: 'TD Control GUI/API',
      url: `http://127.0.0.1:${options.TouchDesignerGuiPort}`,
      enabled: !options.SkipTouchDesignerGui
    },
    {
      group: 'Local APIs and feeds',
      name: 'Home Assistant bridge health',
      url: `http://127.0.0.1:${options.HomeAssistantBridgePort}/health`,
      enabled: !options.SkipHomeAssistantBridge
    },
    {
      group: 'Local APIs and feeds',
      name: 'Environment current state',
      url: `http://127.0.0.1:${options.EnvironmentStatePort}/environment/current`,
      enabled: !options.SkipEnvironmentState
    },
    {
      group: 'Local APIs and feeds',
      name: 'Environment indicators',
      url: `http://127.0.0.1:${options.EnvironmentStatePort}/indicators/current`,
      enabled: !options.SkipEnvironmentState
    },
    {
      group: 'Local APIs and feeds',
      name: 'MediaPipe Browser Monitor',
      url: browserMonitorUrl,
      enabled: !options.SkipMediapipe && options.MediapipeMode === 'mediamtx'
    },
    {
      group: 'Local APIs and feeds',
      name: 'MediaMTX video',
      url: 'http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true',
      enabled: !options.SkipMediapipe && options.MediapipeMode === 'mediamtx'
    },
    {
      group: 'Local APIs and feeds',
      name: 'MediaPipe Camera Hub WebSocket',
      url: `ws://127.0.0.1:${options.MediapipePort}`,
      enabled: !options.SkipMediapipe
    },
    {
      group: 'Local APIs and feeds',
      name: 'Vision Snapshot Processor WebSocket',
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
      name: 'thought-core health',
      url: `${thoughtCoreUrl}/health`,
      enabled: options.EnableThoughtCore
    },
    {
      group: 'Background links',
      name: 'Dify watcher',
      url: 'no browser URL',
      enabled: !options.SkipDifyWatch
    },
    {
      group: 'Background links',
      name: 'thought-core watcher',
      url: 'no browser URL',
      enabled: options.EnableThoughtCoreWatch
    },
    {
      group: 'Background links',
      name: 'TouchDesigner UDP receiver',
      url: '127.0.0.1:9001',
      enabled: true
    }
  ]
}

const getStatus = async () => {
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
  let voicevoxPort = 50021
  try {
    voicevoxPort = Number(new URL(voicevoxUrl).port || 50021)
  } catch {
    voicevoxPort = 50021
  }

  const [
    homeTcp,
    homeHttp,
    environmentTcp,
    environmentHttp,
    mediapipeTcp,
    visionTcp,
    aituberTcp,
    aituberHttp,
    tdTcp,
    tdHttp,
    difyTcp,
    difyHttp,
    thoughtCoreTcp,
    thoughtCoreHttp,
    voicevoxTcp,
    voicevoxHttp
  ] = await Promise.all([
    checkTcp(options.HomeAssistantBridgePort),
    checkHttp(`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`, 2500),
    checkTcp(options.EnvironmentStatePort),
    checkHttp(`http://127.0.0.1:${options.EnvironmentStatePort}/health`),
    checkWebSocketHandshake(options.MediapipePort),
    checkWebSocketHandshake(options.VisionSnapshotProcessorPort),
    checkTcp(options.AituberPort),
    checkHttp(`http://127.0.0.1:${options.AituberPort}`),
    checkTcp(options.TouchDesignerGuiPort),
    checkHttp(`http://127.0.0.1:${options.TouchDesignerGuiPort}`),
    checkTcp(options.DifyPort),
    checkHttp(`http://127.0.0.1:${options.DifyPort}`),
    checkTcp(options.ThoughtCorePort, thoughtCoreHost),
    checkHttp(`${thoughtCoreUrl}/health`),
    checkTcp(voicevoxPort),
    checkHttp(`${voicevoxUrl}/version`)
  ])

  return {
    ok: true,
    timestamp: nowIso(),
    workspaceRoot: WORKSPACE_ROOT,
    operation: operationState(),
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
        tcp: mediapipeTcp
      }),
      vision_snapshot_processor: serviceState({
        entry: pids.vision_snapshot_processor,
        tcp: visionTcp
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
      dify: serviceState({
        entry: null,
        tcp: difyTcp,
        http: difyHttp,
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
  const selectedProfileId = config.selectedProfileId || 'full-stack'
  const options = normalizeOptions(selectedProfileId, config.options || {})
  const preview = previewCommand(selectedProfileId, options)
  return {
    ok: true,
    projectRoot: PROJECT_ROOT,
    workspaceRoot: WORKSPACE_ROOT,
    stateDir: STATE_DIR,
    profiles: readProfiles(),
    config: {
      selectedProfileId,
      options
    },
    launcherState: readLauncherState(),
    operation: operationState(),
    status: await getStatus(),
    endpoints: getEndpoints(options),
    preview,
    logTail: readTextTail(STACK_LOG_FILE)
  }
}

const sendJson = (response, statusCode, payload) => {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store'
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
  if (request.method === 'GET' && requestUrl.pathname === '/api/state') {
    sendJson(response, 200, await getState())
    return
  }
  if (request.method === 'GET' && requestUrl.pathname === '/api/status') {
    sendJson(response, 200, await getStatus())
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
      previewCommand(body.profileId || 'full-stack', body.options || {})
    )
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/save-config') {
    const body = await readBody(request)
    const profileId = body.profileId || 'full-stack'
    const options = normalizeOptions(profileId, body.options || {})
    saveConfig(profileId, options)
    sendJson(response, 200, { ok: true, profileId, options })
    return
  }
  if (request.method === 'POST' && requestUrl.pathname === '/api/start') {
    const body = await readBody(request)
    const result = await runExclusiveStackOperation(
      'start',
      async () => startStack(body.profileId || 'full-stack', body.options || {})
    )
    sendJson(response, result.statusCode, result.payload)
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
  if (request.method === 'POST' && requestUrl.pathname === '/api/status-script') {
    const config = readLauncherConfig()
    const profileId = config.selectedProfileId || 'full-stack'
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
      sendJson(response, 204, {})
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
    console.log(`Home Control Launcher is already running: ${url}`)
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
  console.log(`Home Control Launcher: ${url}`)
  console.log(`Workspace root: ${WORKSPACE_ROOT}`)
  if (OPEN_BROWSER) {
    openBrowser(url)
  }
})
