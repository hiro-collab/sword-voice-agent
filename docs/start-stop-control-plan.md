# Start/Stop Control Plan

This plan belongs to the `ops` layer. It describes how to consolidate system
startup, shutdown, status, and launcher behavior without immediately moving the
current scripts.

## Current State

The active lifecycle entrypoints are:

| Surface | Current path | Role |
|---|---|---|
| Root shortcuts | `<workspace>/start-home-control-stack.bat`, `status-home-control-stack.bat`, `stop-home-control-stack.bat` | Human-friendly compatibility entrypoints. |
| Stack scripts | `scripts/home-control-stack/start-home-control-stack.ps1`, `status-home-control-stack.ps1`, `stop-home-control-stack.ps1` | Authoritative current start/status/stop implementation. |
| Launcher | `tools/home-control-launcher/` | Browser UI that wraps the stack scripts. |
| Runtime registry | `.cache/home-control-stack/pids.json` by default | Current process ownership record. |

All current paths should remain compatible until an `ops` control plane is ready.

## Target Shape

```text
ops/
  scripts/
    system.ps1
    start.ps1
    stop.ps1
    status.ps1
  manifests/
    profiles/
      minimal.json
      thought-core.json
      home-control.json
      full-local.json
    services/
      thought-core.json
      environment-state-server.json
      home-assistant-server.json
      mediapipe-sword-sign.json
      tts-service.json
      aituber-kit.json
  process-registry/
    README.md

runtime/
  pids/
    home-control-stack.json
  state/
    launcher-state.json
  logs/
    services/
```

The future `ops/scripts/system.ps1` should be a thin facade:

```powershell
.\ops\scripts\system.ps1 start  -Profile full-local
.\ops\scripts\system.ps1 status -Profile full-local
.\ops\scripts\system.ps1 stop   -Profile full-local
```

The launcher should call the same control surface instead of having separate
lifecycle logic.

## Service Manifest

Each service manifest should describe process ownership, not business behavior.

```json
{
  "service_id": "thought-core",
  "layer": "turn",
  "repo_path": "sword-voice-agent",
  "cwd": "sword-voice-agent",
  "start": {
    "command": "uv",
    "args": ["run", "sword-thought-core-server"]
  },
  "health": {
    "type": "http",
    "url": "http://127.0.0.1:18787/health"
  },
  "stop": {
    "strategy": "owned_pid"
  },
  "depends_on": ["environment-state-server", "home-assistant-server"]
}
```

Required manifest concepts:

- `service_id`: stable process identity.
- `layer`: one of `contracts/events/layer.schema.json`.
- `repo_path`: sibling repo or current repo path from workspace root.
- `start`: command, args, env overlays, working directory.
- `health`: TCP, HTTP, file, or custom probe.
- `stop`: graceful endpoint, docker compose, owned PID, or no-op.
- `depends_on`: startup order and reverse shutdown order.

## Lifecycle Rules

Start:

1. Resolve workspace root and stack state directory.
2. Load profile and service manifests.
3. Validate required sibling repos, `.env` files, local assets, and ports.
4. Stop existing owned processes only when requested.
5. Start services in dependency order.
6. Write process records with `layer`, `service_id`, PID, command, cwd, and log paths.
7. Probe health and emit status records.

Stop:

1. Read the process registry from the selected stack state directory.
2. Stop services in reverse dependency order.
3. Prefer graceful shutdown endpoints when available.
4. Stop docker compose services only when the profile owns them.
5. Kill only recorded owned PIDs as a fallback.
6. Never kill unrelated browsers, editors, or system updaters based on port alone.

Status:

1. Read process registry.
2. Probe recorded PIDs.
3. Probe configured ports and health URLs.
4. Merge module-local status files.
5. Report layer-aware status: `ops`, `adapter`, `turn`, `environment`, `action`,
   `expression`, and `reflex`.

## Migration Phases

Phase A: Keep current scripts authoritative.

- Done: add `-StackStateDir` / `HOME_CONTROL_STACK_STATE_DIR` compatibility.
- Keep root shortcuts and launcher behavior stable.
- Document `ops/` as future control plane.

Phase B: Introduce manifests in read-only mode.

- Generate a status view from manifests plus current `pids.json`.
- Do not start or stop from manifests yet.
- Compare manifest status with current status script output.

Phase C: Make `ops/scripts/system.ps1 status` authoritative.

- Launcher status calls the ops status surface.
- Existing `status-home-control-stack.ps1` becomes a compatibility wrapper.

Phase D: Move start/stop behind ops facade.

- `start-home-control-stack.ps1` and `stop-home-control-stack.ps1` call the ops
  facade or become compatibility wrappers.
- Root `.bat` shortcuts stay as human-friendly aliases.

Phase E: Optional physical layout cleanup.

- Move active lifecycle scripts under `ops/scripts/`.
- Archive retired full-stack scripts only after references are removed.
- Keep one process registry format.

## Logging

Lifecycle logs should use `layer=ops` for orchestration records and include each
service's own layer when reporting service status:

```text
[ops] starting service=thought-core layer=turn
[ops] health service=environment-state-server layer=environment status=ok
[ops] stopping service=aituber-kit layer=expression strategy=owned_pid
```

This keeps start/stop logs aligned with the system layer model.
