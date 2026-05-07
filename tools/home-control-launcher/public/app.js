const state = {
  profiles: [],
  selectedProfileId: 'full-stack',
  options: {},
  busy: false
}

const switchFields = [
  'StopExisting',
  'MediapipeOpenBrowser',
  'MediapipeNoBrowser',
  'MediapipePythonGui',
  'SkipDify',
  'SkipVoicevoxCheck',
  'SkipHomeAssistantBridge',
  'SkipEnvironmentState',
  'SkipMediapipe',
  'SkipVisionSnapshotProcessor',
  'SkipAituber',
  'SkipDifyWatch',
  'SkipTouchDesignerGui',
  'EnableHomeControlFaultInjection'
]

const portFields = [
  'AituberPort',
  'DifyPort',
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
    throw new Error(payload.error || `HTTP ${response.status}`)
  }
  return payload
}

const setBusy = (busy, label = '') => {
  state.busy = busy
  for (const id of ['start-button', 'stop-button', 'refresh-button']) {
    $(id).disabled = busy
  }
  $('save-state').textContent = busy ? label || 'Working' : 'Ready'
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

const renderControls = () => {
  const profileSelect = $('profile-select')
  profileSelect.innerHTML = state.profiles
    .map(
      (profile) =>
        `<option value="${profile.id}">${escapeHtml(profile.name)}</option>`
    )
    .join('')
  profileSelect.value = state.selectedProfileId
  const profile = state.profiles.find((item) => item.id === state.selectedProfileId)
  $('profile-description').textContent = profile ? profile.description : ''

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

  const switchGrid = $('switch-grid')
  switchGrid.innerHTML = switchFields
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

const renderServices = (services) => {
  const names = Object.keys(services || {})
  $('service-list').innerHTML = names
    .map((name) => {
      const service = services[name]
      return `
        <article class="service-row">
          <header>
            <span class="service-name">${escapeHtml(name)}</span>
            <span class="state-pill ${stateClass(service.state)}">${escapeHtml(service.state)}</span>
          </header>
          <div class="service-meta">
            <span>pid: ${service.pid || '-'}</span>
            <span>tcp: ${escapeHtml(service.tcp?.detail || '-')}</span>
            <span>http: ${escapeHtml(service.http?.detail || '-')}</span>
          </div>
        </article>
      `
    })
    .join('')
}

const renderEndpoints = (endpoints) => {
  const groups = new Map()
  for (const endpoint of endpoints || []) {
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
          const attrs = isUrl
            ? `href="${escapeHtml(endpoint.url)}" target="_blank" rel="noreferrer"`
            : 'href="#"'
          return `
            <a class="endpoint-link ${endpoint.enabled ? '' : 'disabled'}" ${attrs}>
              <strong>${escapeHtml(endpoint.name)}</strong>
              <span>${escapeHtml(endpoint.url)}</span>
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
  state.selectedProfileId = payload.config?.selectedProfileId || 'full-stack'
  state.options = payload.config?.options || {}
  $('workspace-root').textContent = payload.workspaceRoot
  $('status-time').textContent = payload.status?.timestamp || 'Unknown'
  $('command-preview').textContent = payload.preview?.commandLine || ''
  $('log-output').textContent = payload.logTail || 'No launcher log yet.'
  renderControls()
  renderServices(payload.status?.services || {})
  renderEndpoints(payload.endpoints || [])
}

const refreshStatusOnly = async () => {
  const payload = await api('/api/status')
  $('status-time').textContent = payload.timestamp || 'Unknown'
  renderServices(payload.services || {})
  const logs = await api('/api/logs')
  $('log-output').textContent = logs.logTail || 'No launcher log yet.'
}

const refreshLogsOnly = async () => {
  const logs = await api('/api/logs')
  $('log-output').textContent = logs.logTail || 'No launcher log yet.'
}

const startStack = async () => {
  setBusy(true, 'Starting')
  try {
    await api('/api/start', {
      method: 'POST',
      body: JSON.stringify({
        profileId: state.selectedProfileId,
        options: currentOptions()
      })
    })
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const stopStack = async () => {
  setBusy(true, 'Stopping')
  try {
    await api('/api/stop', {
      method: 'POST',
      body: JSON.stringify({ stopDify: false })
    })
    await refreshState()
  } finally {
    setBusy(false)
  }
}

const saveConfig = async () => {
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
  $('save-state').textContent = 'Error'
  $('log-output').textContent = `${error.message}\n\n${$('log-output').textContent}`
}

bindControls()
refreshState()
  .then(() => {
    window.setInterval(() => refreshLogsOnly().catch(() => {}), 5000)
  })
  .catch(showError)
