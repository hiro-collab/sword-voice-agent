# Ops

`ops/` is the home for process manifests, launch policy, and process registry
documentation. The active start/stop implementation is still inherited from the
Home Control Stack supervisor, and that supervisor now lives under
`ops/scripts/home-control-stack/`. `ops/scripts/system.ps1` is the profile-aware
control surface.

## Current Active Locations

| Concern | Current path |
|---|---|
| Profile-aware start/status/stop facade | `ops/scripts/system.ps1` |
| Home Control Stack supervisor implementation | `ops/scripts/home-control-stack/` |
| Root shortcut installer | `ops/scripts/home-control-stack/install-root-shortcuts.ps1` |
| Compatibility wrappers | `scripts/home-control-stack/` |
| Launcher server | `tools/home-control-launcher/` |
| Validation module setup | `scripts/setup-validation-modules.ps1` |

See `docs/start-stop-control-plan.md` for the control-plane shape. Manifests
live under `ops/manifests/`.

Use the facade from this repository root:

```powershell
.\ops\scripts\system.ps1 start  -Profile thought-core-v0 -DryRun
.\ops\scripts\system.ps1 status -Profile thought-core-v0
.\ops\scripts\system.ps1 stop   -Profile thought-core-v0 -DryRun
```

`status` prints a layer-aware manifest/PID summary, then delegates to the
current health status script unless `-ManifestOnly` is passed. `start` and
`stop` translate the selected profile into the current stack script arguments.
The facade prefers PowerShell 7 (`pwsh`) when delegating because the inherited
supervisor scripts use PowerShell 7 syntax.

## Profiles

| Profile | Intended use |
|---|---|
| `full-local` | Current Dify-based full local stack. |
| `thought-core-v0` | Thought Core API and watcher path, with Dify stack/watcher skipped. |
| `thought-core-experimental` | Deprecated compatibility alias for `thought-core-v0`. |
| `camera-debug` | Camera Hub and Vision Snapshot Processor only. |
| `aituber-only` | AITuber Kit surface only. |

When another stack is already running, use alternate ports for dry-run
verification instead of stopping user-owned processes:

```powershell
.\ops\scripts\system.ps1 start `
  -Profile thought-core-v0 `
  -DryRun `
  -SkipVoicevoxCheck `
  -HomeAssistantBridgePort 18887 `
  -EnvironmentStatePort 18890 `
  -MediapipePort 18865 `
  -MediapipeBrowserMonitorPort 18870 `
  -VisionSnapshotProcessorPort 18876 `
  -AituberPort 13000 `
  -TouchDesignerGuiPort 18889 `
  -ThoughtCorePort 18888 `
  -StackStateDir .cache\home-control-stack-system-test
```

## Migration Rule

Do not delete `scripts/home-control-stack/` until external references have had
at least one migration phase to update. Those files are compatibility wrappers;
new lifecycle work belongs under `ops/scripts/`.
