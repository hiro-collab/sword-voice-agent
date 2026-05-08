# 0003 Thought Core Service Src Layout

Status: accepted

## Context

The current thought-core implementation is the canonical `turn` layer service
inside the `sword-voice-agent` repository. Moving it directly to the workspace
root would create Git-management and launcher risks because the workspace root
is not yet a meta repository.

## Decision

Keep the canonical service root at:

```text
sword-control-plane/services/thought-core/
```

Move package code under:

```text
sword-control-plane/services/thought-core/src/thought_core/
```

The external API contract does not change. `POST /turn`, `POST /turn/stream`,
`GET /health`, event schemas, and port defaults remain the same.

## Consequences

- `scripts/start-thought-core.ps1` sets `PYTHONPATH` to
  `services/thought-core/src`.
- Repo-level tests insert `services/thought-core/src` for `thought_core`
  imports.
- Future split-repo work can lift `services/thought-core/` as a more standard
  service root.
- Do not create a second implementation under workspace-root
  `services/thought-core`.
