'use strict'

const crypto = require('crypto')

const CAMERA_SELECTION_REDACTION = '<local-camera-selection>'

const sanitizeVideoInputDeviceName = (value) => {
  const name = String(value || '').trim()
  if (
    !name ||
    name.length > 256 ||
    /[\u0000-\u001f\u007f]/.test(name) ||
    /@device_(?:pnp|cm)_/i.test(name)
  ) {
    return ''
  }
  return name
}

const sanitizeVideoInputCaptureName = (value) => {
  const name = String(value || '').trim()
  if (!name || name.length > 1024 || /[\u0000-\u001f\u007f]/.test(name)) {
    return ''
  }
  return name
}

const sanitizeVideoInputSelectionKey = (value) => {
  const key = String(value || '').trim().toLowerCase()
  return /^camera_[a-f0-9]{24}$/.test(key) ? key : ''
}

const videoInputSelectionKey = ({ name, alternativeName }) => {
  const authorityValue = alternativeName || name
  const authorityClass = alternativeName ? 'dshow-alternative' : 'dshow-name'
  return `camera_${crypto
    .createHash('sha256')
    .update(`${authorityClass}\u0000${authorityValue}`, 'utf8')
    .digest('hex')
    .slice(0, 24)}`
}

const createVideoInputDevice = (nameValue, alternativeNameValue = '') => {
  const name = sanitizeVideoInputDeviceName(nameValue)
  const alternativeName = sanitizeVideoInputCaptureName(alternativeNameValue)
  if (!name) {
    return null
  }
  return {
    name,
    alternativeName,
    captureName: alternativeName || name,
    selectionKey: videoInputSelectionKey({ name, alternativeName })
  }
}

const parseVideoInputDevices = (content) => {
  const devices = []
  let pending = null
  const flushPending = () => {
    if (pending && devices.length < 64) {
      devices.push(pending)
    }
    pending = null
  }
  for (const line of String(content || '').split(/\r?\n/)) {
    const videoMatch = line.match(/^\[[^\]]+]\s+"(.*)"\s+\(video\)\s*$/)
    if (videoMatch) {
      flushPending()
      pending = createVideoInputDevice(videoMatch[1])
      continue
    }
    const alternativeMatch = line.match(
      /^\[[^\]]+]\s+Alternative name\s+"(.*)"\s*$/
    )
    if (pending && alternativeMatch) {
      pending = createVideoInputDevice(pending.name, alternativeMatch[1])
      flushPending()
    }
  }
  flushPending()
  return devices
}

const parseVideoInputFixture = (content) => {
  try {
    const fixture = JSON.parse(content)
    if (!Array.isArray(fixture)) {
      return []
    }
    const devices = []
    const seenNameOnly = new Set()
    for (const item of fixture) {
      const device = typeof item === 'string'
        ? createVideoInputDevice(item)
        : createVideoInputDevice(item?.name, item?.alternative_name)
      if (!device) {
        continue
      }
      if (!device.alternativeName) {
        if (seenNameOnly.has(device.name)) {
          continue
        }
        seenNameOnly.add(device.name)
      }
      devices.push(device)
      if (devices.length >= 64) {
        break
      }
    }
    return devices
  } catch {
    return parseVideoInputDevices(content)
  }
}

const redactCameraSelectionArgument = (command) => {
  const redacted = [...command]
  const index = redacted.indexOf('-MediapipeCameraName')
  if (index >= 0 && index + 1 < redacted.length) {
    redacted[index + 1] = CAMERA_SELECTION_REDACTION
  }
  return redacted
}

const redactCameraSelectionInCommandText = (value) =>
  String(value || '').replace(
    /((?:-MediapipeCameraName|--camera-name)(?:\s+|=))(?:"(?:\\.|[^"])*"|'(?:''|[^'])*'|[^\s]+)/gi,
    `$1${CAMERA_SELECTION_REDACTION}`
  ).replace(
    /@device_(?:pnp|cm)_[^\s"'<>]+/gi,
    CAMERA_SELECTION_REDACTION
  )

const withoutLocalCameraSelection = (options) => {
  const {
    MediapipeCameraName,
    MediapipeCameraSelectionKey,
    ...publicOptions
  } = options || {}
  return publicOptions
}

const createLauncherCameraAdapter = ({
  fileSystem,
  processRunner,
  platform,
  environment,
  resolveExecutable,
  readLauncherConfig
}) => {
  const enumerateVideoInputDevices = () => {
    if (platform !== 'win32') {
      return {
        result_class: 'video_input_enumeration_unsupported',
        devices: []
      }
    }

    const testVideoInputFixture =
      environment.NODE_ENV === 'test' &&
      environment.HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS_FILE
        ? (() => {
            try {
              return fileSystem.readFileSync(
                environment.HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS_FILE,
                'utf8'
              )
            } catch {
              return '[]'
            }
          })()
        : environment.HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS
    if (environment.NODE_ENV === 'test' && testVideoInputFixture) {
      const devices = parseVideoInputFixture(testVideoInputFixture)
      return {
        result_class: devices.length > 0 ? 'video_inputs_enumerated' : 'video_inputs_none',
        devices
      }
    }

    try {
      const ffmpeg = resolveExecutable(environment.HOME_CONTROL_FFMPEG || 'ffmpeg')
      const result = processRunner.spawnSync(
        ffmpeg,
        ['-hide_banner', '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy'],
        {
          encoding: 'utf8',
          windowsHide: true,
          timeout: 5000,
          maxBuffer: 256 * 1024
        }
      )
      if (result.error) {
        return {
          result_class: 'video_input_enumeration_unavailable',
          devices: []
        }
      }
      const devices = parseVideoInputDevices(`${result.stderr || ''}\n${result.stdout || ''}`)
      return {
        result_class: devices.length > 0 ? 'video_inputs_enumerated' : 'video_inputs_none',
        devices
      }
    } catch {
      return {
        result_class: 'video_input_enumeration_unavailable',
        devices: []
      }
    }
  }

  const getVideoInputDevicesPayload = ({ includeLocalCameraSelection = true } = {}) => {
    if (!includeLocalCameraSelection) {
      return {
        ok: true,
        result_class: 'local_video_input_enumeration_redacted',
        count: 0,
        devices: [],
        selection_class: 'local_selection_private',
        selected_match: false,
        device_start_count: 0,
        capture_count: 0
      }
    }
    const enumeration = enumerateVideoInputDevices()
    const config = readLauncherConfig()
    const selectedName = sanitizeVideoInputDeviceName(config.options?.MediapipeCameraName)
    const selectedKey = sanitizeVideoInputSelectionKey(
      config.options?.MediapipeCameraSelectionKey
    )
    const selectedKeyMatches = selectedKey
      ? enumeration.devices.filter((device) => device.selectionKey === selectedKey)
      : []
    const selectedNameMatches = !selectedKey && selectedName
      ? enumeration.devices.filter((device) => device.name === selectedName)
      : []
    const selectedMatch = selectedKey
      ? selectedKeyMatches.length === 1
      : selectedNameMatches.length === 1
    const selectionClass = selectedKey
      ? selectedKeyMatches.length === 1
        ? 'selected_available'
        : selectedKeyMatches.length > 1
          ? 'selected_ambiguous'
          : 'selected_unresolvable'
      : selectedName
        ? selectedNameMatches.length > 1
          ? 'selected_ambiguous'
          : selectedNameMatches.length === 1
            ? 'selected_available'
            : 'manual_selection'
        : 'no_selection'
    return {
      ok: true,
      result_class: enumeration.result_class,
      count: enumeration.devices.length,
      devices: enumeration.devices.map((device, index, allDevices) => ({
        value: device.selectionKey,
        label: allDevices.filter((candidate) => candidate.name === device.name).length > 1
          ? `${device.name} (${index + 1})`
          : device.name
      })),
      selection_class: selectionClass,
      selected_match: selectedMatch,
      device_start_count: 0,
      capture_count: 0
    }
  }

  const resolveVideoInputSelectionForStart = (options) => {
    if (options?.SkipMediapipe) {
      return { ok: true, captureName: '', selection_class: 'camera_disabled' }
    }
    const selectedName = sanitizeVideoInputDeviceName(options?.MediapipeCameraName)
    const selectedKey = sanitizeVideoInputSelectionKey(
      options?.MediapipeCameraSelectionKey
    )
    const enumeration = enumerateVideoInputDevices()
    if (selectedKey) {
      const matches = enumeration.devices.filter(
        (device) => device.selectionKey === selectedKey
      )
      if (matches.length !== 1) {
        return {
          ok: false,
          error: matches.length > 1
            ? 'selected_camera_ambiguous'
            : 'selected_camera_unresolvable',
          selection_class: matches.length > 1
            ? 'selected_ambiguous'
            : 'selected_unresolvable',
          device_start_count: 0,
          capture_count: 0
        }
      }
      return {
        ok: true,
        captureName: matches[0].captureName,
        selection_class: 'selected_available'
      }
    }
    if (!selectedName) {
      return { ok: true, captureName: '', selection_class: 'no_selection' }
    }
    const nameMatches = enumeration.devices.filter(
      (device) => device.name === selectedName
    )
    if (nameMatches.length > 1) {
      return {
        ok: false,
        error: 'selected_camera_ambiguous',
        selection_class: 'selected_ambiguous',
        device_start_count: 0,
        capture_count: 0
      }
    }
    return {
      ok: true,
      captureName: nameMatches.length === 1
        ? nameMatches[0].captureName
        : selectedName,
      selection_class: nameMatches.length === 1
        ? 'selected_available'
        : 'manual_selection'
    }
  }

  return Object.freeze({
    getVideoInputDevicesPayload,
    resolveVideoInputSelectionForStart
  })
}

module.exports = Object.freeze({
  createLauncherCameraAdapter,
  redactCameraSelectionArgument,
  redactCameraSelectionInCommandText,
  sanitizeVideoInputCaptureName,
  sanitizeVideoInputDeviceName,
  sanitizeVideoInputSelectionKey,
  withoutLocalCameraSelection
})
