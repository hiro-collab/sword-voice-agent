# Migration Plan

The first migration goal is reviewable structure, not physical movement. The
existing launch flow and module paths should keep working throughout these
phases.

## Phase 1: Name The System

Scope:

- Add architecture docs.
- Add component map.
- Add contract README files and current schemas.
- Document runtime and ops intent.

Do not:

- Move code.
- Rename modules.
- Change default launch behavior.
- Move `.cache` files.

Exit criteria:

- A new contributor can map current paths to logical roles.
- Current integration docs are linked from the top-level README.
- `thought-core` canonical path duplication is documented.

## Phase 2: Extract Contracts From Existing Code

Scope:

- Turn existing thought-core request/event shapes into JSON Schema.
- Treat `thought-core.event.v0` as the current event schema version.
- Add reflex schemas for raw gesture state, voice-gate status, and Camera Hub
  diagnostics.
- Document environment and home-control APIs from existing module docs.
- Add contract tests that validate representative fixtures.

Priority files:

```text
contracts/events/event.schema.json
contracts/reflex/gesture-state.schema.json
contracts/turn/turn-request.schema.json
contracts/turn/turn-response-events.schema.json
contracts/environment/environment-current.schema.json
contracts/home-control/execute-result.schema.json
```

Exit criteria:

- Existing thought-core contract tests can be compared with schemas.
- New events either match the documented schema or intentionally bump a schema
  version.

## Phase 3: Runtime Compatibility Plan

Scope:

- Keep `.cache/home-control-stack` as the default compatibility path.
- Define future `runtime/` layout.
- Add script-level variables for state/log roots before changing defaults.

Do not:

- Move live PID files while services are running.
- Delete generated logs.
- Break launcher expectations.

Exit criteria:

- Start/status/stop scripts can use a configurable stack state directory without
  changing default behavior.
- Generated files are classified as logs, state, pids, cache, or diagnostics.

## Phase 4: Thought-Core Path Consolidation

Scope:

- Keep the current implementation at
  `<cell>/sword-control-plane/services/thought-core` until a split is chosen.
- Keep package code under `src/thought_core` inside that service root.
- Do not create a second implementation under `<cell>/services/thought-core`.
- If a split repo is needed later, update tests, launcher scripts, and docs in
  one migration.
- Keep a short compatibility shim only if external scripts already reference the
  old path.

Exit criteria:

- There is only one implementation path.
- `POST /turn`, `POST /turn/stream`, and `/health` remain compatible.
- The thought-core watcher still works with ai-talk-core handoff.

## Phase 5: Service Alias Cleanup

Scope:

- Decide whether current module names remain physical names or become aliases.
- Only rename modules when launch scripts, docs, tests, and local env examples
  are updated in the same migration.

Candidate aliases:

```text
environment-state-server -> environment-server v0
home-assistant-server    -> home-control-server v0
mediapipe-sword-sign     -> reflex-core input v0
tts-service              -> expression-core speech v0
aituber-kit              -> expression app v0
```

The alias is logical first. Keep the physical sibling repository name until the
service boundary, adapter boundary, ops manifest, and tests are updated in the
same migration.

Exit criteria:

- Names in docs, launcher UI, scripts, and tests mean the same thing.
- No service becomes a catch-all during the rename.
