# Ops Manifests

These manifests are read-only planning data for the future `ops` control plane.
The current authoritative lifecycle implementation remains
`scripts/home-control-stack/`.

## Directories

| Path | Purpose |
|---|---|
| `services/` | Stable service records, current script owner, layer, health, stop strategy, dependencies. |
| `profiles/` | Named service sets that should become start/status/stop profiles. |

The `service_id` values intentionally match current PID registry names where
possible, so migration can compare manifests against `.cache/home-control-stack/pids.json`.
