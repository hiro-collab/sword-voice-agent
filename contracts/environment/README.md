# Environment Contract

The environment contract covers observation of world and module state.
Environment services observe and project state; they do not execute home
actions.

Current implementation:

- `../environment-state-server/`

Logical boundary:

- Service: `environment-server`
- Layer: `environment`
- Consumes source adapters such as MediaPipe/Camera Hub status, vision snapshot
  state, Home Assistant read-side state, and module-local status files.
- Exposes observation snapshots; it does not perform action orchestration.

## Current HTTP Surface

```text
GET  /environment/current
GET  /environment/current?wait_for=room_light&after=<iso>&timeout_ms=1500
POST /environment/relations
POST /feedback/state-query
GET  /feedback/state-query/recent
GET  /feedback/state-query/summary
GET  /indicators/current
GET  /health
GET  /ready
```

Thought Core-facing endpoints require bearer auth. Display-safe indicator endpoints are
loopback-only.

## Current Schemas

- `environment-current.schema.json`
- `wait-result.schema.json`
- `state-query-feedback-request.schema.json`
- `state-query-feedback-response.schema.json`

These schemas define the shared envelope and known fields while leaving detailed
module projections open-ended.
