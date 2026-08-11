'use strict'

/**
 * Convert one fresh supervisor snapshot into the service-facing public status.
 *
 * This module deliberately owns no I/O, polling, cache, lifecycle decision, or
 * readiness observation. The supervisor/reducer remain the only authority for
 * service state; this projection only translates their current public snapshot.
 */
const projectLauncherServiceStatus = ({
  supervisor,
  graphServices,
  expectedServiceIds,
  serviceIdForPublicId,
  profileId
}) => {
  const serviceStates = new Map(
    (supervisor.services || []).map((service) => [service.service_id, service.state])
  )
  const enabledIds = new Set(expectedServiceIds)
  const services = {}
  for (const spec of graphServices) {
    const key = spec.public_readiness_id || spec.service_id
    const supervisorState = serviceStates.get(spec.service_id) || 'pending'
    const ready = supervisorState === 'ready' || supervisorState === 'external_ready'
    const enabled = enabledIds.has(spec.public_readiness_id) || spec.requirement === 'external'
    services[key] = {
      state: supervisorState === 'external_ready'
        ? 'OK_EXTERNAL'
        : supervisorState === 'ready'
          ? 'OK'
          : supervisorState === 'optional_absent'
            ? 'SKIPPED'
            : ['starting', 'stop_requested'].includes(supervisorState)
              ? 'STARTING'
              : ['failed', 'residue', 'unknown'].includes(supervisorState)
                ? 'ERROR'
                : 'DOWN',
      enabled,
      supervisor_state: supervisorState,
      readiness_authority: 'node_supervisor',
      processAlive: ready && spec.ownership === 'owned',
      pid: null,
      tcp: { ok: ready, detail: ready ? 'supervisor_ready' : 'supervisor_not_ready' },
      http: { ok: ready, detail: ready ? 'supervisor_ready' : 'supervisor_not_ready' },
      raw_private_publication_flags: false
    }
  }
  const readyServiceIds = [...enabledIds].filter((serviceId) => {
    const graphId = serviceIdForPublicId(serviceId)
    return ['ready', 'external_ready', 'optional_absent'].includes(serviceStates.get(graphId))
  })
  return {
    services,
    startupTiming: {
      schema_version: 'launcher_startup_timing.v1',
      profileId,
      status_class: supervisor.phase,
      expectedServiceIds: [...enabledIds],
      readyServiceIds,
      operational: supervisor.phase === 'ready',
      readiness_authority: 'node_supervisor',
      raw_private_publication_flags: false
    }
  }
}

module.exports = {
  projectLauncherServiceStatus
}
