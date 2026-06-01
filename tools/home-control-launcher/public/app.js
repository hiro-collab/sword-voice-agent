const state = {
  profiles: [],
  selectedProfileId: 'thought-core-v0',
  options: {},
  busy: false,
  operation: 'idle',
  operationDetail: 'Waiting for an action.',
  operationProgress: {
    percent: 0,
    label: '0%',
    visible: false
  },
  remoteBusy: false,
  remoteOperation: null
}

const coreSwitchFields = [
  'StopExisting',
  'EnableThoughtCore',
  'EnableThoughtCoreWatch',
  'SkipAituber',
  'SkipHomeAssistantBridge',
  'SkipEnvironmentState',
  'SkipMediapipe',
  'SkipVisionSnapshotProcessor',
  'SkipTouchDesignerGui',
  'SkipVoicevoxCheck'
]

const diagnosticSwitchFields = [
  'MediapipeOpenBrowser',
  'MediapipeNoBrowser',
  'MediapipePythonGui',
  'EnableHomeControlFaultInjection'
]

const legacySwitchFields = ['SkipDify', 'SkipDifyWatch']

const portFields = [
  'AituberPort',
  'DifyPort',
  'ThoughtCorePort',
  'TouchDesignerGuiPort',
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'VisionSnapshotProcessorPort'
]

const corePortFields = [
  'AituberPort',
  'ThoughtCorePort',
  'TouchDesignerGuiPort',
  'HomeAssistantBridgePort',
  'EnvironmentStatePort',
  'MediapipePort',
  'VisionSnapshotProcessorPort'
]

const textFields = [
  'MediapipeCameraName',
  'VoicevoxUrl',
  'DifyDockerRoot',
  'HomeControlConfigPath'
]

const serviceLabels = {
  home_assistant_bridge: 'Action bridge',
  environment_state_server: 'Environment state',
  mediapipe: 'Reflex sensor',
  vision_snapshot_processor: 'Vision snapshot',
  aituber_kit: 'Expression runtime',
  touchdesigner_control_gui: 'Display runtime GUI',
  thought_core_api: 'Thought Core API',
  thought_core_watcher: 'Thought Core watcher',
  voicevox: 'VOICEVOX speech'
}
const hiddenServiceKeys = new Set(['dify'])
const serviceRoles = {
  home_assistant_bridge: 'action boundary',
  environment_state_server: 'environment',
  mediapipe: 'reflex',
  vision_snapshot_processor: 'environment input',
  aituber_kit: 'expression',
  touchdesigner_control_gui: 'display',
  thought_core_api: 'conscious API',
  thought_core_watcher: 'conscious bridge',
  voicevox: 'speech'
}

const fieldLabels = {
  StopExisting: 'Restart managed services first',
  EnableThoughtCore: 'Thought Core API',
  EnableThoughtCoreWatch: 'Thought Core watcher',
  SkipAituber: 'Disable expression UI',
  SkipHomeAssistantBridge: 'Disable action bridge',
  SkipEnvironmentState: 'Disable environment state',
  SkipMediapipe: 'Disable reflex sensor',
  SkipVisionSnapshotProcessor: 'Disable vision snapshot',
  SkipTouchDesignerGui: 'Disable display runtime GUI',
  SkipVoicevoxCheck: 'Skip VOICEVOX readiness check',
  MediapipeOpenBrowser: 'Open MediaPipe monitor',
  MediapipeNoBrowser: 'Keep MediaPipe monitor hidden',
  MediapipePythonGui: 'Use Python camera GUI',
  EnableHomeControlFaultInjection: 'Enable action bridge fault injection',
  SkipDify: 'Use external compatibility runtime / skip local start',
  SkipDifyWatch: 'Disable compatibility watcher'
}

const enableFieldsByService = {
  thought_core_api: ['EnableThoughtCore'],
  thought_core_watcher: ['EnableThoughtCoreWatch']
}

const skipFieldsByService = {
  home_assistant_bridge: ['SkipHomeAssistantBridge'],
  environment_state_server: ['SkipEnvironmentState'],
  mediapipe: ['SkipMediapipe'],
  vision_snapshot_processor: ['SkipVisionSnapshotProcessor', 'SkipMediapipe'],
  aituber_kit: ['SkipAituber'],
  touchdesigner_control_gui: ['SkipTouchDesignerGui'],
  voicevox: ['SkipVoicevoxCheck', 'SkipAituber']
}

const operationLabels = {
  idle: 'Launcher standby',
  starting: 'Starting stack',
  started: 'Stack online',
  stopping: 'Stopping stack',
  stopped: 'Stack stopped',
  saving: 'Saving config',
  blocked: 'Action blocked',
  error: 'Action failed'
}

const $ = (id) => document.getElementById(id)

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.message || payload.error || `HTTP ${response.status}`)
    error.status = response.status
    error.payload = payload
    throw error
  }
  return payload
}

const setBusy = (busy, label = '') => {
  state.busy = busy
  document.body.dataset.busy = busy ? 'true' : 'false'
  $('save-state').textContent = busy
    ? label || 'Working'
    : state.remoteBusy
      ? 'Locked'
      : 'Ready'
  renderActionButtons()
}

const renderActionButtons = () => {
  const disabled = state.busy || state.remoteBusy
  for (const id of ['start-button', 'stop-button', 'refresh-button', 'save-config', 'stop-launcher-button']) {
    $(id).disabled = disabled
  }
  $('start-button').textContent =
    state.busy && state.operation === 'starting' ? 'Starting...' : 'Start Stack'
  $('stop-button').textContent =
    state.busy && state.operation === 'stopping' ? 'Stopping...' : 'Stop Stack'
  $('stop-launcher-button').textContent =
    state.busy && state.operation === 'stopping' ? 'Stopping...' : 'Stop Launcher'
  $('refresh-button').textContent = 'Refresh'
  $('save-config').textContent =
    state.busy && state.operation === 'saving' ? 'Saving...' : 'Save'
}

const setOperation = (operation, detail = '') => {
  state.operation = operation
  state.operationDetail = detail || operationLabels[operation] || ''
  if (!['starting', 'stopping'].includes(operation)) {
    state.operationProgress = {
      percent: operation === 'started' || operation === 'stopped' ? 100 : 0,
      label: operation === 'started' ? '100%' : '',
      visible: operation === 'started' || operation === 'stopped'
    }
  }
  document.body.dataset.operation = operation
  renderOperation()
  renderOperationReadiness()
  renderActionButtons()
}

const renderOperation = () => {
  const banner = $('operation-banner')
  const operation = state.operation || 'idle'
  banner.hidden = operation === 'idle'
  banner.className = `operation-banner ${operation}`
  $('operation-title').textContent = operationLabels[operation] || operationLabels.idle
  $('operation-detail').textContent = state.operationDetail || 'Waiting for an action.'
  const progress = state.operationProgress || {}
  const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0))
  const progressNode = $('operation-progress')
  progressNode.hidden = !progress.visible
  progressNode.setAttribute('aria-valuenow', String(Math.round(percent)))
  $('operation-progress-bar').style.width = `${percent}%`
  $('operation-progress-label').hidden = !progress.visible
  $('operation-progress-label').textContent = progress.label || `${Math.round(percent)}%`
}

const renderOperationReadiness = () => {
  const readinessStates = {
    starting: ['Starting', 'warn'],
    stopping: ['Stopping', 'warn'],
    stopped: ['Stopped', 'warn'],
    blocked: ['Locked', 'warn'],
    error: ['Action failed', 'down']
  }
  const readiness = readinessStates[state.operation]
  if (!readiness) {
    return
  }
  const readinessCard = $('readiness-card')
  readinessCard.classList.remove('ready', 'warn', 'down')
  readinessCard.classList.add(readiness[1])
  $('readiness-label').textContent = readiness[0]
  $('readiness-detail').textContent = state.operationDetail
}

const operationUiType = (operationType) => {
  if (operationType === 'start') {
    return 'starting'
  }
  if (operationType === 'stop') {
    return 'stopping'
  }
  if (operationType === 'save') {
    return 'saving'
  }
  return 'blocked'
}

const applyServerOperation = (operation) => {
  const wasRemoteBusy = state.remoteBusy
  const remoteBusy = Boolean(operation && operation.busy)
  state.remoteBusy = remoteBusy && !state.busy
  state.remoteOperation = remoteBusy ? operation : null

  if (state.remoteBusy) {
    const uiOperation = operationUiType(operation.type)
    setOperation(
      uiOperation,
      `Another ${operation.type} operation is running. Started ${formatTimestamp(operation.startedAt)}.`
    )
    $('save-state').textContent = 'Locked'
    return
  }

  if (wasRemoteBusy && !state.busy && ['starting', 'stopping', 'saving', 'blocked'].includes(state.operation)) {
    setOperation('idle')
  }
  renderActionButtons()
}

const profileOptions = () => {
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  return profile ? profile.options || {} : {}
}

const currentOptions = () => ({
  ...state.options
})

const applyProfileDefaults = async () => {
  const preview = await api('/api/preview', {
    method: 'POST',
    body: JSON.stringify({
      profileId: state.selectedProfileId,
      options: profileOptions()
    })
  })
  state.options = preview.options
  $('command-preview').textContent = preview.commandLine
  renderControls()
  renderSystemSummary()
}

const setOption = (key, value) => {
  state.options[key] = value
  if (key === 'MediapipeOpenBrowser' && value) {
    state.options.MediapipeNoBrowser = false
  }
  if (key === 'MediapipeNoBrowser' && value) {
    state.options.MediapipeOpenBrowser = false
  }
  renderControls()
  refreshPreview()
}

const isLaunchServiceEnabled = (field) => {
  if (field === 'StopExisting') {
    return null
  }
  if (field.startsWith('Skip')) {
    return !state.options[field]
  }
  return Boolean(state.options[field])
}

const summarizeLaunchServices = () => {
  const serviceFields = coreSwitchFields.filter((field) => field !== 'StopExisting')
  const enabled = serviceFields.filter((field) => isLaunchServiceEnabled(field)).length
  const total = serviceFields.length
  return {
    card: `${enabled}/${total} enabled`,
    drawer: `${enabled}/${total} services`
  }
}

const summarizeDiagnostics = () => {
  const enabled = diagnosticSwitchFields.filter((field) => state.options[field]).length
  return {
    card: enabled ? `${enabled} enabled` : 'Off',
    drawer: enabled ? `${enabled} toggles on` : 'No test toggles'
  }
}

const summarizeRuntime = () => {
  const compatibilityActive = !state.options.SkipDify || !state.options.SkipDifyWatch
  return {
    card: compatibilityActive ? 'Compat on' : 'Standard',
    drawer: compatibilityActive ? 'Legacy paths active' : 'Legacy paths off'
  }
}

const summarizePorts = () => {
  const values = corePortFields.map((field) => String(state.options[field] || '').trim()).filter(Boolean)
  const duplicates = values.filter((value, index) => values.indexOf(value) !== index)
  return {
    card: duplicates.length ? 'Check conflict' : `${values.length}/${corePortFields.length} set`,
    drawer: duplicates.length ? 'Duplicate port values' : 'Core bindings'
  }
}

const renderLaunchSummary = () => {
  const services = summarizeLaunchServices()
  const diagnostics = summarizeDiagnostics()
  const runtime = summarizeRuntime()
  const ports = summarizePorts()
  $('services-summary').textContent = services.card
  $('services-drawer-summary').textContent = services.drawer
  $('diagnostics-summary').textContent = diagnostics.card
  $('diagnostics-drawer-summary').textContent = diagnostics.drawer
  $('runtime-summary').textContent = runtime.card
  $('runtime-drawer-summary').textContent = runtime.drawer
  $('ports-summary').textContent = ports.card
  $('ports-drawer-summary').textContent = ports.drawer
}

const renderControls = () => {
  const profileSelect = $('profile-select')
  const profilesByGroup = groupProfiles(visibleProfilesForSelect(state.profiles))
  profileSelect.innerHTML = profilesByGroup
    .map(([group, profiles]) => {
      const options = profiles
        .map(
          (profile) =>
            `<option value="${profile.id}">${escapeHtml(profile.name)}</option>`
        )
        .join('')
      return `<optgroup label="${escapeHtml(group)}">${options}</optgroup>`
    })
    .join('')
  profileSelect.value = state.selectedProfileId
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  $('profile-description').textContent = profile ? profile.description : ''
  $('active-profile-name').textContent = profile ? profile.name : state.selectedProfileId
  $('active-profile-detail').textContent = state.options.MediapipeMode
    ? `MediaPipe: ${state.options.MediapipeMode}`
    : 'Configuration pending'
  renderLaunchSummary()

  for (const field of portFields) {
    const input = $(field)
    input.value = state.options[field] || ''
  }
  for (const field of textFields) {
    $(field).value = state.options[field] || ''
  }

  document.querySelectorAll('#mediapipe-mode button').forEach((button) => {
    button.classList.toggle('active', button.dataset.value === state.options.MediapipeMode)
  })

  renderSwitchGroup('core-switch-grid', coreSwitchFields)
  renderSwitchGroup('diagnostic-switch-grid', diagnosticSwitchFields)
  renderSwitchGroup('legacy-switch-grid', legacySwitchFields)
}

const groupProfiles = (profiles) => {
  const groups = new Map()
  for (const profile of profiles || []) {
    const group = profile.group || 'Other'
    if (!groups.has(group)) {
      groups.set(group, [])
    }
    groups.get(group).push(profile)
  }
  return Array.from(groups.entries())
}

const visibleProfilesForSelect = (profiles) =>
  (profiles || []).filter((profile) => !profile.hidden || profile.id === state.selectedProfileId)

const renderSwitchGroup = (elementId, fields) => {
  const switchGrid = $(elementId)
  switchGrid.innerHTML = fields
    .map(
      (field) => `
        <label class="switch-row">
          <span>${labelFor(field)}</span>
          <input type="checkbox" data-switch="${field}" ${state.options[field] ? 'checked' : ''} />
        </label>
      `
    )
    .join('')

  switchGrid.querySelectorAll('[data-switch]').forEach((input) => {
    input.addEventListener('change', (event) => {
      setOption(event.target.dataset.switch, event.target.checked)
    })
  })
}

const labelFor = (value) =>
  fieldLabels[value] ||
  value
    .replace(/^Skip/, 'Skip ')
    .replace(/^Stop/, 'Stop ')
    .replace(/^Enable/, 'Enable ')
    .replace(/^Mediapipe/, 'MediaPipe ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')

const escapeHtml = (value) =>
  String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

const stateClass = (serviceState) =>
  `state-${String(serviceState || 'down').toLowerCase().replace(/_/g, '-')}`

const serviceDisplayName = (name) => serviceLabels[name] || labelFor(name)

const serviceRole = (name) => serviceRoles[name] || 'system cell'

const serviceStateShort = (serviceState) => {
  const value = String(serviceState || 'DOWN').toUpperCase()
  if (value === 'OK_EXTERNAL') return 'EXT'
  if (value === 'DEGRADED') return 'DEG'
  if (value === 'STARTING') return 'WAIT'
  return value
}

const endpointDisplayName = (name) => {
  const labels = {
    'AITuber Kit': 'Expression runtime',
    'Expression runtime': 'Operator',
    'AITuber Cube Vault': 'Expression cube vault',
    'Expression cube vault': 'Avatar vault',
    'Display control GUI/API': 'Display runtime GUI/API',
    'Display runtime GUI/API': 'Display',
    'Home Assistant bridge health': 'Action bridge health',
    'Action bridge health': 'Action',
    'MediaPipe Browser Monitor': 'Reflex browser monitor',
    'Reflex browser monitor': 'Camera Hub',
    'MediaMTX video': 'Reflex camera video',
    'Reflex camera video': 'Video',
    'MediaPipe Camera Hub WebSocket': 'Reflex Camera Hub WebSocket',
    'Reflex Camera Hub WebSocket': 'Camera WS',
    'Vision Snapshot Processor WebSocket': 'Vision snapshot WebSocket',
    'Vision snapshot WebSocket': 'Vision WS',
    'TouchDesigner UDP receiver': 'Display UDP receiver',
    'Display UDP receiver': 'TD UDP',
    'Projection Visual': 'Operator stage',
    'Passive Projection': 'Stage',
    'Thought Core API index': 'Core API',
    'Thought Core health': 'Core health',
    'Environment display state': 'Env state',
    'Environment indicators': 'Env indicators',
    'VOICEVOX': 'Speech',
    'Thought Core watcher': 'Core watch',
    'Compatibility workflow UI': 'Compatibility UI',
    'Compatibility watcher': 'Compatibility watch'
  }
  return labels[name] || name
}

const endpointTargetLabel = (endpoint, kind, canOpen) => {
  if (!endpoint.enabled) return 'skipped'
  if (!canOpen) {
    if (kind === 'websocket') return 'WebSocket reference'
    if (kind === 'background') return 'background reference'
    return 'reference'
  }
  if (kind === 'stage') return 'passive clean view'
  if (endpoint.name === 'Projection Visual') return 'operator preview'
  if (kind === 'api' || kind === 'thought') return 'local API'
  if (kind === 'camera') return 'camera feed'
  if (kind === 'display') return 'display runtime'
  if (kind === 'speech') return 'speech runtime'
  if (kind === 'compatibility') return 'compatibility'
  return 'open browser'
}

const serviceIsIncluded = (name) => {
  const enableFields = enableFieldsByService[name] || []
  if (enableFields.length > 0) {
    return enableFields.some((field) => state.options[field])
  }
  const skipFields = skipFieldsByService[name] || []
  return !skipFields.some((field) => state.options[field])
}

const serviceStateGroup = (serviceState) => {
  const value = String(serviceState || 'DOWN').toUpperCase()
  if (value === 'OK' || value === 'OK_EXTERNAL') {
    return 'ok'
  }
  if (value === 'DEGRADED' || value === 'STARTING') {
    return 'warn'
  }
  return 'down'
}

const formatTimestamp = (value) => {
  if (!value) {
    return 'Awaiting status'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(date)
}

const summarizeServices = (services = {}) => {
  const entries = Object.entries(services).filter(
    ([name]) => !hiddenServiceKeys.has(name) && serviceIsIncluded(name)
  )
  const summary = {
    total: entries.length,
    online: 0,
    warn: 0,
    down: 0
  }
  for (const [, service] of entries) {
    const group = serviceStateGroup(service.state)
    if (group === 'ok') {
      summary.online += 1
    } else if (group === 'warn') {
      summary.warn += 1
    } else {
      summary.down += 1
    }
  }
  return summary
}

const setOperationProgressFromSummary = (summary, mode) => {
  if (!summary || summary.total <= 0) {
    state.operationProgress = {
      percent: 8,
      label: mode === 'stopping' ? 'Stopping...' : 'Checking...',
      visible: true
    }
    renderOperation()
    return
  }
  const count = mode === 'stopping'
    ? Math.max(0, summary.total - summary.online)
    : summary.online
  const remaining = mode === 'stopping'
    ? summary.online
    : Math.max(0, summary.total - summary.online)
  const percent = Math.round((count / summary.total) * 100)
  const noun = remaining === 1 ? 'service' : 'services'
  state.operationProgress = {
    percent,
    label:
      remaining === 0
        ? `${percent}%`
        : `${percent}% · ${remaining} ${noun} remaining`,
    visible: true
  }
  renderOperation()
}

const renderSystemSummary = (services = null, timestamp = '') => {
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  $('active-profile-name').textContent = profile ? profile.name : state.selectedProfileId
  $('active-profile-detail').textContent = state.options.MediapipeMode
    ? `MediaPipe: ${state.options.MediapipeMode}`
    : 'Configuration pending'

  if (!services) {
    return
  }

  const summary = summarizeServices(services)
  if (state.operation === 'starting') {
    setOperationProgressFromSummary(summary, 'starting')
    const detail =
      summary.total > 0
        ? `${summary.online}/${summary.total} expected services online. Watching startup progress.`
        : 'Start command accepted. Waiting for service status.'
    if (summary.total > 0 && summary.online === summary.total) {
      setOperation('started', `All expected services are online. Updated ${formatTimestamp(timestamp)}.`)
    } else {
      setOperation('starting', detail)
    }
  } else if (state.operation === 'stopping') {
    setOperationProgressFromSummary(summary, 'stopping')
  }

  const attention = summary.warn + summary.down
  const readinessCard = $('readiness-card')
  readinessCard.classList.remove('ready', 'warn', 'down')

  let readinessLabel = 'Ready'
  let readinessClass = 'ready'
  if (state.operation === 'starting') {
    readinessLabel = 'Starting'
    readinessClass = 'warn'
  } else if (state.operation === 'stopping') {
    readinessLabel = 'Stopping'
    readinessClass = 'warn'
  } else if (state.operation === 'stopped') {
    readinessLabel = 'Stopped'
    readinessClass = 'warn'
  } else if (summary.total === 0) {
    readinessLabel = 'Manual'
    readinessClass = 'warn'
  } else if (summary.down > 0) {
    readinessLabel = 'Check stack'
    readinessClass = 'down'
  } else if (summary.warn > 0) {
    readinessLabel = 'Warming up'
    readinessClass = 'warn'
  }
  readinessCard.classList.add(readinessClass)
  $('readiness-label').textContent = readinessLabel
  $('readiness-detail').textContent =
    state.operation === 'starting' ||
    state.operation === 'stopping' ||
    state.operation === 'stopped'
      ? state.operationDetail
      : `${summary.online}/${summary.total} expected services online`
  $('online-count').textContent = `${summary.online}/${summary.total}`
  $('online-detail').textContent = `Updated ${formatTimestamp(timestamp)}`
  $('attention-count').textContent = String(attention)
  $('attention-detail').textContent =
    attention === 0 ? 'All expected services nominal' : `${summary.warn} warming, ${summary.down} down`
}

const renderServices = (services) => {
  const names = Object.keys(services || {}).filter((name) => !hiddenServiceKeys.has(name))
  $('service-list').innerHTML = names
    .reduce(
      (markup, name) => {
        const service = services[name]
        const included = serviceIsIncluded(name)
        const rowClass = included ? '' : ' service-skipped'
        return `${markup}
          <div
            class="service-row${rowClass}"
            data-state-group="${serviceStateGroup(service.state)}"
            role="row"
          >
            <span class="service-status" role="cell">
              <span class="service-led" aria-hidden="true"></span>
              <span
                class="state-pill ${stateClass(service.state)}"
                title="${escapeHtml(service.state)}"
              >
                ${escapeHtml(serviceStateShort(service.state))}
              </span>
            </span>
            <span class="service-title" role="cell">
              <span class="service-name">${escapeHtml(serviceDisplayName(name))}</span>
              <span class="service-key">
                ${escapeHtml(serviceRole(name))}${included ? '' : ' / profile off'}
              </span>
            </span>
            <span class="service-metric" role="cell">${service.pid || '-'}</span>
            <span class="service-metric" role="cell" title="${escapeHtml(service.tcp?.detail || '-')}">
              ${escapeHtml(service.tcp?.detail || '-')}
            </span>
            <span class="service-metric" role="cell" title="${escapeHtml(service.http?.detail || '-')}">
              ${escapeHtml(service.http?.detail || '-')}
            </span>
          </div>
        `
      },
      `
        <div class="service-rack" role="table" aria-label="Runtime organ status">
          <div class="service-rack-header" role="row">
            <span role="columnheader">State</span>
            <span role="columnheader">Organ</span>
            <span role="columnheader">PID</span>
            <span role="columnheader">TCP</span>
            <span role="columnheader">HTTP</span>
          </div>
      `
    ) + '</div>'
}

const renderEndpoints = (endpoints) => {
  const groups = new Map()
  for (const endpoint of endpoints || []) {
    if (endpointKind(endpoint) === 'compatibility' && !endpoint.enabled) {
      continue
    }
    if (!groups.has(endpoint.group)) {
      groups.set(endpoint.group, [])
    }
    groups.get(endpoint.group).push(endpoint)
  }
  $('endpoint-list').innerHTML = Array.from(groups.entries())
    .map(([group, items]) => {
      const links = items
        .map((endpoint) => {
          const isUrl = /^https?:|^file:/.test(endpoint.url)
          const canOpen = isUrl && endpoint.enabled
          const attrs = canOpen
            ? `href="${escapeHtml(endpoint.url)}" target="_blank" rel="noreferrer"`
            : 'href="#" aria-disabled="true" tabindex="-1"'
          const status = endpoint.enabled ? (canOpen ? 'open' : 'reference') : 'skipped'
          const className = endpoint.enabled ? (canOpen ? '' : 'reference-only') : 'disabled'
          const kind = endpointKind(endpoint)
          const title = endpoint.url
            ? `${endpointDisplayName(endpoint.name)}: ${endpoint.url}`
            : endpointDisplayName(endpoint.name)
          return `
            <a class="endpoint-link ${className}" data-kind="${kind}" title="${escapeHtml(title)}" ${attrs}>
              <span class="endpoint-icon" aria-hidden="true">${endpointIcon(kind)}</span>
              <span class="endpoint-copy">
                <strong>${escapeHtml(endpointDisplayName(endpoint.name))}</strong>
                <span>${escapeHtml(endpointTargetLabel(endpoint, kind, canOpen))}</span>
              </span>
              <em class="endpoint-status">${status}</em>
            </a>
          `
        })
        .join('')
      return `
        <section class="endpoint-group">
          <h3>${escapeHtml(group)}</h3>
          <div class="endpoint-links">${links}</div>
        </section>
      `
    })
    .join('')
}

const endpointKind = (endpoint) => {
  const name = String(endpoint.name || '').toLowerCase()
  const url = String(endpoint.url || '').toLowerCase()
  if (name.includes('dify') || name.includes('compatibility')) return 'compatibility'
  if (url.startsWith('ws:') || name.includes('websocket')) return 'websocket'
  if (name.includes('passive projection') || url.includes('mode=passive')) return 'stage'
  if (name.includes('thought-core')) return 'thought'
  if (name.includes('aituber') || name.includes('projection')) return 'ui'
  if (name.includes('display') || name.includes('td control') || name.includes('touchdesigner')) return 'display'
  if (name.includes('voicevox')) return 'speech'
  if (name.includes('mediapipe') || name.includes('mediamtx') || name.includes('camera')) return 'camera'
  if (name.includes('health') || name.includes('environment') || url.includes('/api/')) return 'api'
  return endpoint.group === 'Background links' ? 'background' : 'link'
}

const endpointIcon = (kind) => {
  const icons = {
    ui: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="12" rx="2"></rect><path d="M8 21h8"></path><path d="M12 17v4"></path></svg>',
    api: '<svg viewBox="0 0 24 24"><path d="M7 8l-4 4 4 4"></path><path d="M17 8l4 4-4 4"></path><path d="M14 4l-4 16"></path></svg>',
    websocket: '<svg viewBox="0 0 24 24"><path d="M5 12a7 7 0 0 1 14 0"></path><path d="M8 12a4 4 0 0 1 8 0"></path><path d="M12 12h.01"></path><path d="M12 16v4"></path></svg>',
    thought: '<svg viewBox="0 0 24 24"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M8 14a6 6 0 1 1 8 0c-.8.6-1 1.3-1 2H9c0-.7-.2-1.4-1-2z"></path></svg>',
    stage: '<svg viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="14" rx="2"></rect><path d="M8 9h8"></path><path d="M8 13h5"></path></svg>',
    display: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"></rect><path d="M8 20h8"></path><path d="M12 16v4"></path><path d="M7 8h10"></path></svg>',
    speech: '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H3v6h3l5 4z"></path><path d="M15 9a4 4 0 0 1 0 6"></path><path d="M18 6a8 8 0 0 1 0 12"></path></svg>',
    camera: '<svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"></path><circle cx="12" cy="13" r="3"></circle></svg>',
    legacy: '<svg viewBox="0 0 24 24"><path d="M4 7h16"></path><path d="M7 7v13"></path><path d="M17 7v13"></path><path d="M9 4h6l2 3H7z"></path><path d="M10 11h4"></path></svg>',
    compatibility: '<svg viewBox="0 0 24 24"><path d="M4 7h16"></path><path d="M7 7v13"></path><path d="M17 7v13"></path><path d="M9 4h6l2 3H7z"></path><path d="M10 11h4"></path></svg>',
    background: '<svg viewBox="0 0 24 24"><path d="M4 6h16v12H4z"></path><path d="M8 10h8"></path><path d="M8 14h5"></path></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"></path><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"></path></svg>'
  }
  return icons[kind] || icons.link
}

const refreshPreview = async () => {
  try {
    const preview = await api('/api/preview', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions()
      })
    })
    state.options = preview.options
    $('command-preview').textContent = preview.commandLine
  } catch (error) {
    $('command-preview').textContent = error.message
  }
}

const refreshState = async () => {
  const payload = await api('/api/state')
  state.profiles = payload.profiles || []
  state.selectedProfileId = payload.config?.selectedProfileId || 'thought-core-v0'
  state.options = payload.config?.options || {}
  $('workspace-root').textContent = payload.portMode
    ? `${payload.workspaceRoot} · ${payload.portMode}`
    : payload.workspaceRoot
  $('status-time').textContent = payload.status?.timestamp || 'Unknown'
  $('command-preview').textContent = payload.preview?.commandLine || ''
  $('log-output').textContent = payload.logTail || 'No launcher log yet.'
  renderControls()
  applyServerOperation(payload.operation || payload.status?.operation)
  renderSystemSummary(payload.status?.services || {}, payload.status?.timestamp)
  renderServices(payload.status?.services || {})
  renderEndpoints(payload.endpoints || [])
}

const refreshStatusOnly = async () => {
  const payload = await api('/api/status')
  $('status-time').textContent = payload.timestamp || 'Unknown'
  applyServerOperation(payload.operation)
  renderSystemSummary(payload.services || {}, payload.timestamp)
  renderServices(payload.services || {})
  const logs = await api('/api/logs')
  $('log-output').textContent = logs.logTail || 'No launcher log yet.'
}

const refreshLogsOnly = async () => {
  const logs = await api('/api/logs')
  $('log-output').textContent = logs.logTail || 'No launcher log yet.'
}

const startStack = async () => {
  setOperation('starting', 'Start command is being sent. Waiting for the supervisor to spawn.')
  setBusy(true, 'Starting')
  try {
    await api('/api/start', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions()
      })
    })
    setOperation('starting', 'Start command accepted. Watching services come online.')
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const stopStack = async () => {
  setOperation('stopping', 'Stop command is running. Waiting for the shutdown script.')
  setBusy(true, 'Stopping')
  try {
    await api('/api/stop', {
      method: 'POST',
      body: JSON.stringify({ stopDify: false })
    })
    setOperation('stopped', 'Stop command completed. Service cards are refreshed below.')
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const stopLauncher = async () => {
  const confirmed = window.confirm(
    'Stop Sword System Launcher? System cell services are not stopped by this button.'
  )
  if (!confirmed) {
    return
  }
  setOperation('stopping', 'Launcher server is shutting down. System cell services are unchanged.')
  setBusy(true, 'Stopping')
  try {
    await api('/api/shutdown', { method: 'POST' })
  } catch (error) {
    if (!String(error.message || '').includes('Failed to fetch')) {
      throw error
    }
  }
  setOperation('stopped', 'Launcher stopped. Close this tab or start it again from the terminal.')
  document.querySelectorAll('button, input, select').forEach((element) => {
    element.disabled = true
  })
}

const saveConfig = async () => {
  setOperation('saving', 'Writing launcher configuration.')
  setBusy(true, 'Saving')
  try {
    await api('/api/save-config', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions()
      })
    })
    $('save-state').textContent = 'Saved'
    window.setTimeout(() => {
      $('save-state').textContent = 'Ready'
    }, 1200)
  } finally {
    setBusy(false)
    setOperation('idle')
  }
}

const bindControls = () => {
  $('profile-select').addEventListener('change', (event) => {
    state.selectedProfileId = event.target.value
    applyProfileDefaults().catch(showError)
  })
  for (const field of portFields) {
    $(field).addEventListener('change', (event) => {
      setOption(field, Number(event.target.value))
    })
  }
  for (const field of textFields) {
    $(field).addEventListener('change', (event) => {
      setOption(field, event.target.value)
    })
  }
  document.querySelectorAll('#mediapipe-mode button').forEach((button) => {
    button.addEventListener('click', () => {
      setOption('MediapipeMode', button.dataset.value)
    })
  })
  $('refresh-button').addEventListener('click', () => refreshState().catch(showError))
  $('save-config').addEventListener('click', () => saveConfig().catch(showError))
  $('start-button').addEventListener('click', () => startStack().catch(showError))
  $('stop-button').addEventListener('click', () => stopStack().catch(showError))
  $('stop-launcher-button').addEventListener('click', () => stopLauncher().catch(showError))
  $('copy-command').addEventListener('click', async () => {
    await navigator.clipboard.writeText($('command-preview').textContent)
    $('save-state').textContent = 'Copied'
    window.setTimeout(() => {
      $('save-state').textContent = 'Ready'
    }, 1200)
  })
  $('copy-log').addEventListener('click', async () => {
    await navigator.clipboard.writeText($('log-output').textContent)
    $('save-state').textContent = 'Log copied'
    window.setTimeout(() => {
      $('save-state').textContent = 'Ready'
    }, 1200)
  })
}

const showError = (error) => {
  setBusy(false)
  if (error.payload?.error === 'operation_in_progress') {
    const operation = error.payload.operation
    state.remoteBusy = Boolean(operation && operation.busy)
    state.remoteOperation = state.remoteBusy ? operation : null
    setOperation('blocked', error.payload.message || 'Another operation is already running.')
    $('save-state').textContent = state.remoteBusy ? 'Locked' : 'Ready'
    $('log-output').textContent = `${error.message}\n\n${$('log-output').textContent}`
    return
  }
  setOperation('error', error.message || 'Check the launcher log for details.')
  $('save-state').textContent = 'Error'
  $('log-output').textContent = `${error.message}\n\n${$('log-output').textContent}`
}

bindControls()
refreshState()
  .then(() => {
    renderOperation()
    renderActionButtons()
    window.setInterval(() => refreshStatusOnly().catch(() => {}), 5000)
  })
  .catch(showError)
