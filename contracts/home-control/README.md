# Home Control Contract

The home-control contract covers previewing and executing approved actions.
The service-facing boundary is home-control; Home Assistant itself is an
external adapter behind that boundary.

Current implementation:

- `../home-assistant-server/`

## Current HTTP Surface

```text
GET  /health
GET  /actions
POST /actions/{action_id}/preview
POST /actions/{action_id}/execute
```

The bridge exposes an allowlist of Home Assistant scripts. It owns action
safety, execution, and action tracking. Higher layers may observe, evaluate,
retry, or ask for feedback, but should not hide retries inside `execute`.

## Boundary Rule

`execute` is one request to the approved action boundary. It must not hide
higher-level orchestration retries. Depending on policy and request metadata, it
may return confirmation-required, dry-run, duplicate, accepted, or failed
results. When it actually performs a Home Assistant command, that command should
represent a single execution attempt.

Retry loops, post-action observation, success evaluation, and follow-up user
feedback belong in thought-core or another higher orchestration layer.

## Current Schemas

- `action-request.schema.json`
- `preview-result.schema.json`
- `execute-result.schema.json`

