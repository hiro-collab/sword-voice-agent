# Ops Manifests

These manifests describe the named profiles and service identities used by the
`ops` control plane. The current supervisor implementation is still inherited
from `scripts/home-control-stack/`, while `ops/scripts/system.ps1` translates a
profile into the matching start/status/stop arguments.

## Directories

| Path | Purpose |
|---|---|
| `services/` | Stable service records, current script owner, layer, health, stop strategy, dependencies. |
| `profiles/` | Named service sets accepted by `ops/scripts/system.ps1 -Profile`. |

The `service_id` values intentionally match current PID registry names where
possible, so `system.ps1 status` can compare manifests against
`.cache/home-control-stack/pids.json`.
