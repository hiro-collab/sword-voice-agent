# Memory Contract

The memory contract covers M4 semantic and episodic memory. It does not cover
raw signals, module state, working memory, event journals, config, policy, or
secrets.

Current status:

- `memory-core` is a future service boundary.
- Current `thought-core` may emit memory candidates later, but should not
  commit long-term memory directly.
- Policies in `policies/access/` define which services may read scopes or write
  candidates.

## Current Schemas

- `memory-item.schema.json`
- `retrieve-request.schema.json`
- `retrieve-result.schema.json`
- `write-candidate-request.schema.json`
- `write-candidate-result.schema.json`
- `commit-request.schema.json`
- `commit-result.schema.json`

## Boundary Rules

- `retrieve` returns selected, scoped M4 items only. It is not a raw journal
  reader.
- `write-candidate` proposes a memory item with source traceability.
- `commit` is reserved for `memory-core` or explicit implementation work after
  policy and optional user confirmation.
- Secrets and config are never memory items.
