'use strict'

const MEMBERSHIP_OPTION_FIELDS = Object.freeze([
  'SkipHomeAssistantBridge',
  'SkipEnvironmentState',
  'SkipMediapipe',
  'SkipVisionSnapshotProcessor',
  'SkipAituber',
  'SkipTouchDesignerGui',
  'EnableThoughtCore',
  'EnableThoughtCoreWatch'
])

const ownedServiceEnabled = (serviceId, options) => {
  switch (serviceId) {
    case 'home_assistant_bridge': return !options.SkipHomeAssistantBridge
    case 'environment_state_server': return !options.SkipEnvironmentState
    case 'openai_provider_broker': return options.EnableThoughtCore === true
    case 'thought_core_api': return options.EnableThoughtCore === true
    case 'mediapipe_camera_hub_stack': return !options.SkipMediapipe
    case 'vision_snapshot_processor':
      return !options.SkipVisionSnapshotProcessor && !options.SkipMediapipe
    case 'aituber_kit': return !options.SkipAituber
    case 'thought_core_watcher': return options.EnableThoughtCoreWatch === true
    case 'touchdesigner_control_gui': return !options.SkipTouchDesignerGui
    default: return false
  }
}

const membershipOptionDefaults = ({ graphServices, profileManifest }) => {
  if (!Array.isArray(graphServices) || !profileManifest || !Array.isArray(profileManifest.services)) {
    throw new Error('launcher_profile_membership_invalid')
  }
  const graphIds = new Set(graphServices.map((service) => service.service_id))
  const members = new Set()
  for (const serviceId of profileManifest.services) {
    if (typeof serviceId !== 'string' || !graphIds.has(serviceId) || members.has(serviceId)) {
      throw new Error('launcher_profile_membership_invalid')
    }
    members.add(serviceId)
  }
  return Object.freeze({
    SkipHomeAssistantBridge: !members.has('home_assistant_bridge'),
    SkipEnvironmentState: !members.has('environment_state_server'),
    SkipMediapipe: !members.has('mediapipe_camera_hub_stack'),
    SkipVisionSnapshotProcessor: !members.has('vision_snapshot_processor'),
    SkipAituber: !members.has('aituber_kit'),
    SkipTouchDesignerGui: !members.has('touchdesigner_control_gui'),
    EnableThoughtCore: members.has('thought_core_api'),
    EnableThoughtCoreWatch: members.has('thought_core_watcher')
  })
}

const selectedServiceIdsForOptions = ({ graphServices, options }) => {
  if (!Array.isArray(graphServices) || !options || typeof options !== 'object') {
    throw new Error('launcher_service_selection_invalid')
  }
  const selected = []
  for (const service of graphServices) {
    const serviceId = service && service.service_id
    if (service && service.ownership === 'external') {
      if (serviceId === 'voicevox' && !options.SkipVoicevoxCheck && !options.SkipAituber) {
        selected.push(serviceId)
      }
      continue
    }
    if (ownedServiceEnabled(serviceId, options)) selected.push(serviceId)
  }
  if (new Set(selected).size !== selected.length) {
    throw new Error('launcher_service_selection_invalid')
  }
  const selectedSet = new Set(selected)
  if (graphServices.some((service) =>
    service && service.requirement === 'required' && !selectedSet.has(service.service_id)
  )) {
    throw new Error('launcher_required_service_missing')
  }
  return Object.freeze(selected)
}

const validateProfileRecords = ({
  graphProfileId,
  graphServices,
  defaultProfiles,
  profileManifests
}) => {
  if (typeof graphProfileId !== 'string' || !Array.isArray(graphServices) ||
      !Array.isArray(defaultProfiles) || !Array.isArray(profileManifests)) {
    throw new Error('launcher_profile_projection_invalid')
  }
  const graphById = new Map(graphServices.map((service) => [service.service_id, service]))
  if (graphById.size !== graphServices.length) throw new Error('launcher_profile_projection_invalid')
  const manifestsById = new Map()
  for (const manifest of profileManifests) {
    const profileId = manifest && manifest.profile_id
    if (typeof profileId !== 'string' || manifestsById.has(profileId)) {
      throw new Error('launcher_profile_projection_invalid')
    }
    manifestsById.set(profileId, manifest)
  }
  const records = []
  const seenProfileIds = new Set()
  for (const profile of defaultProfiles) {
    const profileId = profile && profile.id
    if (typeof profileId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/u.test(profileId) ||
        seenProfileIds.has(profileId)) {
      throw new Error('launcher_profile_projection_invalid')
    }
    seenProfileIds.add(profileId)
    const manifest = manifestsById.get(profileId)
    if (!manifest || !manifest.lifecycle_profile_id) continue
    if (manifest.lifecycle_profile_id !== graphProfileId ||
        profile.options && MEMBERSHIP_OPTION_FIELDS.some((field) => Object.hasOwn(profile.options, field))) {
      throw new Error('launcher_profile_projection_invalid')
    }
    const memberIds = manifest.services
    if (!Array.isArray(memberIds) || new Set(memberIds).size !== memberIds.length ||
        memberIds.some((serviceId) => !graphById.has(serviceId) || graphById.get(serviceId).ownership !== 'owned')) {
      throw new Error('launcher_profile_projection_invalid')
    }
    const membershipOptions = membershipOptionDefaults({ graphServices, profileManifest: manifest })
    const projectedIds = selectedServiceIdsForOptions({
      graphServices,
      options: { ...membershipOptions, SkipVoicevoxCheck: true }
    })
    if (projectedIds.length !== memberIds.length ||
        projectedIds.some((serviceId) => !memberIds.includes(serviceId))) {
      throw new Error('launcher_profile_projection_invalid')
    }
    records.push(Object.freeze({ profile, manifest }))
  }
  if (records.length === 0) throw new Error('launcher_profile_projection_invalid')
  return Object.freeze(records)
}

module.exports = {
  MEMBERSHIP_OPTION_FIELDS,
  membershipOptionDefaults,
  selectedServiceIdsForOptions,
  validateProfileRecords
}
