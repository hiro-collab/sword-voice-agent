# Start/Stop Control Plan

This plan belongs to the `ops` layer. It describes how system startup,
shutdown, status, and launcher behavior are consolidated.

## Current State

The active lifecycle entrypoints are:

| Surface | Current path | Role |
|---|---|---|
| Ops facade | `ops/scripts/system.ps1` | Profile-aware start/status/stop entrypoint. |
| Root shortcuts | `<cell>/start-home-control-stack.bat`, `status-home-control-stack.bat`, `stop-home-control-stack.bat` | Human-friendly compatibility entrypoints. |
| Stack scripts | `ops/scripts/home-control-stack/start-home-control-stack.ps1`, `status-home-control-stack.ps1`, `stop-home-control-stack.ps1` | Authoritative inherited supervisor implementation. |
| Launcher | `tools/home-control-launcher/` | Browser UI that calls the ops facade. |
| Runtime registry | `.cache/home-control-stack/pids.json` by default | Current process ownership record. |

All current paths remain compatible. The `ops` control surface owns the
lifecycle entrypoint, while `ops/scripts/home-control-stack/` keeps the
inherited supervisor logic in one place.

## Target Shape

```text
ops/
  scripts/
    system.ps1
    home-control-stack/
      start-home-control-stack.ps1
      status-home-control-stack.ps1
      stop-home-control-stack.ps1
      start-home-control-launcher.ps1
      stop-home-control-launcher.ps1
      install-root-shortcuts.ps1
  manifests/
    profiles/
      minimal.json
      thought-core.json
      home-control.json
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

`ops/scripts/system.ps1` is a thin facade:

```powershell
.\ops\scripts\system.ps1 start  -Profile thought-core-v0
.\ops\scripts\system.ps1 status -Profile thought-core-v0
.\ops\scripts\system.ps1 stop   -Profile thought-core-v0
```

The launcher calls the same control surface instead of having separate
lifecycle logic.

## Service Manifest

Each service manifest should describe process ownership, not business behavior.
The earlier plan to put lifecycle dependencies in each service manifest is
superseded. Current Launcher dependency order and reverse Stop ordering are
authored only in the
[`launcher-service-graph.standard.v1.json`](../ops/manifests/launcher-service-graph.standard.v1.json)
service `dependencies`; do not add `depends_on` to service manifests.

```json
{
  "service_id": "thought-core",
  "layer": "turn",
  "repo_path": "control-plane/core",
  "cwd": "control-plane/core",
  "start": {
    "command": "uv",
    "args": ["run", "python", "-m", "thought_core"],
    "python_path": "control-plane/core/services/thought-core/src"
  },
  "health": {
    "type": "http",
    "url": "http://127.0.0.1:18787/health"
  },
  "stop": {
    "strategy": "owned_pid"
  }
}
```

Required manifest concepts:

- `service_id`: stable process identity.
- `layer`: one of `contracts/events/layer.schema.json`.
- `repo_path`: sibling repo or current repo path from workspace root.
- `contracts`: optional list of public contract areas this process exposes or
  consumes.
- `adapters`: optional list of driver/external protocol edges this process uses.
- `memory`: optional M0-M6 read/write/candidate metadata used by layer-aware
  status.
- `start`: command, args, env overlays, working directory.
- `health`: TCP, HTTP, file, or custom probe.
- `stop`: graceful endpoint, docker compose, owned PID, or no-op.

## Lifecycle Rules

Start:

1. Resolve workspace root and stack state directory.
2. Load the profile, service manifests, and canonical Launcher service graph.
3. Validate required sibling repos, `.env` files, local assets, and ports.
4. Stop existing owned processes only when requested.
5. Start services in the dependency order from the canonical Launcher service graph.
6. Write process records with `layer`, `service_id`, PID, command, cwd, and log paths.
7. Probe health and emit status records.

Stop:

1. Read the process registry from the selected stack state directory.
2. Stop services in the reverse dependency order from the canonical Launcher service graph.
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
- Document `ops/` as the control plane that inherits the current supervisor.

Phase B: Introduce manifests and manifest status.

- Done: generate a status view from manifests plus current `pids.json`.
- Done: compare manifest status with current status script output.
- Current entrypoint: `ops/scripts/system.ps1 status -Profile <profile>`.

Phase C: Move start/stop behind ops facade.

- Done: `ops/scripts/system.ps1 start|stop|status -Profile <profile>` delegates
  to the inherited `ops/scripts/home-control-stack/` implementation.
- Profile membership controls `-Skip...` and `-Enable...` arguments.

Phase D: Make root shortcuts and launcher call the ops facade.

- Done: root `.bat` shortcuts stay as human-friendly aliases and are generated
  by `ops/scripts/home-control-stack/install-root-shortcuts.ps1`.
- Done: launcher start/status/stop calls `ops/scripts/system.ps1`.
- Done: root launcher shortcuts call `ops/scripts/home-control-stack/` directly;
  the superseded `scripts/home-control-stack/*.ps1` forwarding layer is removed.

Phase E: Optional manifest-native process manager.

- Replace profile-to-legacy-argument translation with service-level manifest
  start commands.
- Keep one process registry format.
- Archive retired full-stack scripts only after references are removed.

Phase F: Optional physical layout cleanup.

- Done: move active lifecycle scripts under `ops/scripts/`.
- Root `.bat` shortcuts stay as human-friendly aliases.
- Archive retired full-stack scripts only after references are removed.

## Logging

Lifecycle logs should use `layer=ops` for orchestration records and include each
service's own layer when reporting service status:

```text
[ops] starting service=thought-core layer=turn
[ops] health service=environment-state-server layer=environment status=ok
[ops] stopping service=aituber-kit layer=expression strategy=owned_pid
```

This keeps start/stop logs aligned with the system layer model.
