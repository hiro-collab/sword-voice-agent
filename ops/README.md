# Ops

`ops/` is the future home for process manifests, launch policy, and process
registry documentation. It is not the active script location yet.

## Current Active Locations

| Concern | Current path |
|---|---|
| Home Control Stack start/status/stop | `scripts/home-control-stack/` |
| Root shortcut installer | `scripts/home-control-stack/install-root-shortcuts.ps1` |
| Launcher server | `tools/home-control-launcher/` |
| Validation module setup | `scripts/setup-validation-modules.ps1` |

See `docs/start-stop-control-plan.md` for the proposed control-plane shape.
Initial read-only manifests live under `ops/manifests/`.

## Migration Rule

Do not move active scripts into `ops/` until the root shortcuts, launcher,
README, and tests are updated in the same change. Until then, `ops/` is a
planning and manifest area only.
