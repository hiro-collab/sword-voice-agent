# Access Control Contract

Access-control contracts describe capability authorization and audit records.
They are policy-first skeletons for the current local system; there is no
standalone authorization server yet.

Current policy files:

- `policies/access/capabilities.json`
- `policies/access/services.json`
- `policies/access/memory-scopes.json`
- `policies/access/action-approval.json`

## Current Schemas

- `authorization-request.schema.json`
- `authorization-decision.schema.json`
- `audit-event.schema.json`

## Boundary Rules

- Authorization is capability-based: what a service may do is more important
  than which process asks.
- M6 secrets are not memory. Policies may allow an adapter or ops layer to use a
  secret, but memory and thought services must not read secret values.
- `memory.write.confirmed` belongs to `memory-core` policy, not to
  `thought-core` or `deep-core`.
