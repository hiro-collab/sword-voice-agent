# System Cell And Control Plane

The outer directory is a **system cell**: the local deployment unit for one AI body.
It may also be called a deployment cell. The inner repository is the
**control plane**.

## Responsibilities

| Area | Responsibility |
|---|---|
| system cell root | Start/stop shortcuts, organ repo placement, runtime/cache/local folders |
| `control-plane/core/` | docs, contracts, policies, ops manifests, tests, shared kernel parts |
| `organs/` | Large independently managed modules: voice, reflex, environment, action, expression |
| `external/` | Third-party SDKs and redistribution-sensitive assets only |
| `.cache/` | Current compatibility runtime path |
| `runtime/` | Future canonical runtime logs/state/pids/diagnostics |
| `local/` | Cell-local config, memory, secrets |

## Three Layers

```text
cell.yaml
  placement ledger for this machine

ops/manifests
  launch definitions and profile membership

docs/deployment-cell.md
  design source of truth for the system-cell concept
```

`stack.lock.json` is intentionally not hand-written yet. It should be generated
later by ops tooling from repo commits, dirty state, profile, ports, and checked
health results.

## Final Layout

```text
sword-agent-os/
  README.md
  CELL.md
  cell.yaml

  start-home-control-stack.bat
  status-home-control-stack.bat
  stop-home-control-stack.bat

  .cache/
  runtime/
  local/
  external/
  archives/

  control-plane/core/
  organs/
    speech-input/ai-talk-core/
    reflex/mediapipe-sword-sign/
    environment/environment-state-server/
    environment/vision-snapshot-processor/
    action/home-assistant-server/
    expression/aituber-kit/
    expression/tts-service/
    expression/avatar-service/
    display/touchdesigner-ai-controller/
    diagnostics/system-house-renderer/
```
