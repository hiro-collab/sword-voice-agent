# Home Control Launcher

Local web launcher for the Home Control Stack.

It serves a browser UI for:

- choosing launch profiles
- editing common startup options and ports
- previewing the exact PowerShell command
- starting and stopping the existing stack scripts
- keeping reference URLs visible after logs scroll

The launcher intentionally wraps the existing scripts instead of replacing them:

- `scripts/home-control-stack/start-home-control-stack.ps1`
- `scripts/home-control-stack/status-home-control-stack.ps1`
- `scripts/home-control-stack/stop-home-control-stack.ps1`

Runtime state is written under:

```text
.cache/home-control-stack/
  launcher-config.json
  launcher-state.json
  logs/launcher-stack.log
```

`launcher-stack.log` is rotated by the launcher server. The active log is
kept to 5 MB by default, with 3 backup files:

```text
logs/launcher-stack.log
logs/launcher-stack.log.1
logs/launcher-stack.log.2
logs/launcher-stack.log.3
```

The limits can be overridden with:

- `HOME_CONTROL_LAUNCHER_STACK_LOG_MAX_BYTES`
- `HOME_CONTROL_LAUNCHER_STACK_LOG_BACKUPS`

Start it from the workspace root:

```powershell
.\start-home-control-launcher.bat
```

Or from this repository:

```powershell
.\scripts\home-control-stack\start-home-control-launcher.ps1
```
