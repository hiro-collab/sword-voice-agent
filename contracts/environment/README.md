# Environment Contract

The environment contract covers observation of world and module state.
Environment services observe and project state; they do not execute home
actions.

Current implementation:

- `../environment-state-server/`

## Current HTTP Surface

```text
GET  /environment/current
GET  /environment/current?wait_for=room_light&after=<iso>&timeout_ms=1500
GET  /environment/relations
POST /feedback/state-query
GET  /feedback/state-query/recent
GET  /feedback/state-query/summary
GET  /indicators/current
GET  /health
GET  /ready
```

Dify-facing endpoints require bearer auth. Display-safe indicator endpoints are
loopback-only.

## Current Schemas

- `environment-current.schema.json`
- `wait-result.schema.json`
- `state-query-feedback-request.schema.json`
- `state-query-feedback-response.schema.json`

These schemas define the shared envelope and known fields while leaving detailed
module projections open-ended.

