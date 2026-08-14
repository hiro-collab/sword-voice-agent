'use strict'

/**
 * 利用者のprofile/optionsを、canonical graph上の起動対象serviceへ写す唯一の規則。
 * 実行、I/O、Ready判定は行わず、Private Plan・公開Ready・UIが同じ結果を消費する。
 */

const SERVICE_SELECTION_RULES = Object.freeze([
  Object.freeze({ service_id: 'home_assistant_bridge', profile_membership: true, option_field: 'SkipHomeAssistantBridge', selected_value: false, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'environment_state_server', profile_membership: true, option_field: 'SkipEnvironmentState', selected_value: false, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'openai_provider_broker', profile_membership: true, option_field: 'EnableThoughtCore', selected_value: true, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'thought_core_api', profile_membership: true, option_field: 'EnableThoughtCore', selected_value: true, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'mediapipe_camera_hub_stack', profile_membership: true, option_field: 'SkipMediapipe', selected_value: false, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'vision_snapshot_processor', profile_membership: true, option_field: 'SkipVisionSnapshotProcessor', selected_value: false, selection_dependencies: Object.freeze(['mediapipe_camera_hub_stack']) }),
  Object.freeze({ service_id: 'aituber_kit', profile_membership: true, option_field: 'SkipAituber', selected_value: false, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'thought_core_watcher', profile_membership: true, option_field: 'EnableThoughtCoreWatch', selected_value: true, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'touchdesigner_control_gui', profile_membership: true, option_field: 'SkipTouchDesignerGui', selected_value: false, selection_dependencies: Object.freeze([]) }),
  Object.freeze({ service_id: 'voicevox', profile_membership: false, option_field: 'SkipVoicevoxCheck', selected_value: false, selection_dependencies: Object.freeze(['aituber_kit']) })
])

const rulesByServiceId = new Map(SERVICE_SELECTION_RULES.map((rule) => [rule.service_id, rule]))

const MEMBERSHIP_OPTION_FIELDS = Object.freeze(Array.from(new Set(
  SERVICE_SELECTION_RULES
    .filter((rule) => rule.profile_membership)
    .map((rule) => rule.option_field)
)))

const optionMatches = (options, field, selectedValue) =>
  selectedValue === true ? options[field] === true : options[field] !== true

const validatedGraph = (graphServices) => {
  if (!Array.isArray(graphServices)) throw new Error('launcher_service_selection_invalid')
  const graphById = new Map()
  for (const service of graphServices) {
    const serviceId = service && service.service_id
    const rule = rulesByServiceId.get(serviceId)
    if (typeof serviceId !== 'string' || graphById.has(serviceId) || !rule ||
        (rule.profile_membership && service.ownership !== 'owned') ||
        (!rule.profile_membership && service.ownership !== 'external')) {
      throw new Error('launcher_service_selection_invalid')
    }
    graphById.set(serviceId, service)
  }
  if (graphById.size !== SERVICE_SELECTION_RULES.length) {
    throw new Error('launcher_service_selection_invalid')
  }
  return graphById
}

const membershipOptionDefaults = ({ graphServices, profileManifest }) => {
  let graphById
  try {
    graphById = validatedGraph(graphServices)
  } catch {
    throw new Error('launcher_profile_membership_invalid')
  }
  if (!profileManifest || !Array.isArray(profileManifest.services)) {
    throw new Error('launcher_profile_membership_invalid')
  }
  const members = new Set()
  for (const serviceId of profileManifest.services) {
    const service = graphById.get(serviceId)
    if (!service || service.ownership !== 'owned' || members.has(serviceId)) {
      throw new Error('launcher_profile_membership_invalid')
    }
    members.add(serviceId)
  }
  const defaults = {}
  for (const service of graphServices) {
    if (service.ownership !== 'owned') continue
    const rule = rulesByServiceId.get(service.service_id)
    const desiredValue = members.has(service.service_id) ? rule.selected_value : !rule.selected_value
    if (Object.hasOwn(defaults, rule.option_field) && defaults[rule.option_field] !== desiredValue) {
      throw new Error('launcher_profile_membership_invalid')
    }
    defaults[rule.option_field] = desiredValue
  }
  return Object.freeze(defaults)
}

const projectLauncherServiceSelection = ({ graphServices, options }) => {
  const graphById = validatedGraph(graphServices)
  if (!options || typeof options !== 'object') {
    throw new Error('launcher_service_selection_invalid')
  }
  const selectedServiceIds = []
  const selectedByServiceId = new Map()
  const rows = []
  for (const service of graphServices) {
    const rule = rulesByServiceId.get(service.service_id)
    if (rule.selection_dependencies.some((serviceId) => !selectedByServiceId.has(serviceId))) {
      throw new Error('launcher_service_selection_invalid')
    }
    const selected = optionMatches(options, rule.option_field, rule.selected_value) &&
      rule.selection_dependencies.every((serviceId) => selectedByServiceId.get(serviceId) === true)
    selectedByServiceId.set(service.service_id, selected)
    if (selected) selectedServiceIds.push(service.service_id)

    if (service.public_readiness_id) {
      rows.push(Object.freeze({
        service_id: service.service_id,
        public_readiness_id: service.public_readiness_id,
        selected,
        direct_fields: Object.freeze([Object.freeze({ field: rule.option_field, selected_value: rule.selected_value })]),
        dependency_fields: Object.freeze(rule.selection_dependencies.map((dependencyId) => {
          const dependencyRule = rulesByServiceId.get(dependencyId)
          const dependencyService = graphById.get(dependencyId)
          if (!dependencyRule || !dependencyService || !dependencyService.public_readiness_id) {
            throw new Error('launcher_service_selection_invalid')
          }
          return Object.freeze({
            field: dependencyRule.option_field,
            selected_value: dependencyRule.selected_value,
            public_readiness_id: dependencyService.public_readiness_id
          })
        }))
      }))
    }
  }

  const selectedSet = new Set(selectedServiceIds)
  if (selectedSet.size !== selectedServiceIds.length) throw new Error('launcher_service_selection_invalid')
  const requiredMissingServiceIds = graphServices
    .filter((service) => service.requirement === 'required' && !selectedSet.has(service.service_id))
    .map((service) => service.service_id)

  return Object.freeze({
    selectedServiceIds: Object.freeze(selectedServiceIds),
    requiredMissingServiceIds: Object.freeze(requiredMissingServiceIds),
    publicProjection: Object.freeze({
      schema_version: 'launcher_service_selection.v0',
      rows: Object.freeze(rows),
      required_missing_count: requiredMissingServiceIds.length,
      raw_private_publication_flags: false
    })
  })
}

const resolveLauncherServiceSelection = (input) => {
  const projection = projectLauncherServiceSelection(input)
  if (projection.requiredMissingServiceIds.length > 0) {
    throw new Error('launcher_required_service_missing')
  }
  return projection
}

const validateProfileRecords = ({ graphProfileId, graphServices, defaultProfiles, profileManifests }) => {
  if (typeof graphProfileId !== 'string' || !Array.isArray(graphServices) ||
      !Array.isArray(defaultProfiles) || !Array.isArray(profileManifests)) {
    throw new Error('launcher_profile_projection_invalid')
  }
  let graphById
  try {
    graphById = validatedGraph(graphServices)
  } catch {
    throw new Error('launcher_profile_projection_invalid')
  }
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
    if (typeof profileId !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/u.test(profileId) || seenProfileIds.has(profileId)) {
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
    let membershipOptions
    let projectedIds
    try {
      membershipOptions = membershipOptionDefaults({ graphServices, profileManifest: manifest })
      projectedIds = resolveLauncherServiceSelection({
        graphServices,
        options: { ...membershipOptions, SkipVoicevoxCheck: true }
      }).selectedServiceIds
    } catch {
      throw new Error('launcher_profile_projection_invalid')
    }
    if (projectedIds.length !== memberIds.length || projectedIds.some((serviceId) => !memberIds.includes(serviceId))) {
      throw new Error('launcher_profile_projection_invalid')
    }
    records.push(Object.freeze({ profile, manifest }))
  }
  if (records.length === 0) throw new Error('launcher_profile_projection_invalid')
  return Object.freeze(records)
}

module.exports = {
  MEMBERSHIP_OPTION_FIELDS,
  SERVICE_SELECTION_RULES,
  membershipOptionDefaults,
  projectLauncherServiceSelection,
  resolveLauncherServiceSelection,
  validateProfileRecords
}
