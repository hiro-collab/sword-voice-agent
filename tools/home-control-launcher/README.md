# Sword System Launcher

Local web launcher for the Sword Agent OS system cell.

It serves a browser UI for:

- choosing launch profiles
- editing common startup options and ports
- previewing the exact PowerShell command
- starting and stopping through the ops lifecycle facade
- keeping reference URLs visible after logs scroll

The primary profile is `System Cell (Thought Core)`. Normal operation should
use the Thought Core profile.

The launcher calls the ops facade, which then delegates to the inherited
supervisor implementation:

- `ops/scripts/system.ps1`
- `ops/scripts/home-control-stack/start-home-control-stack.ps1`
- `ops/scripts/home-control-stack/status-home-control-stack.ps1`
- `ops/scripts/home-control-stack/stop-home-control-stack.ps1`

Runtime state is written under `.cache/home-control-stack/` by default:

```text
.cache/home-control-stack/
  launcher-config.json
  launcher-state.json
  demo-safe-settings.json
  logs/launcher-stack.log
```

`demo-safe-settings.json` stores local operator choices for the Launcher Demo
settings drawer. It is local state, not tracked source; fresh clones use the
tracked defaults from `manifests/demo-safe-settings/defaults.json` and start
with demo-safe candidates disabled.

To test an alternate compatible state directory, set
`HOME_CONTROL_STACK_STATE_DIR` before starting the launcher or pass
`-StackStateDir` to the ops lifecycle command. Relative paths are resolved from
the workspace root. The launcher passes the resolved state directory to
start/status/stop child processes so they read the same `pids.json`.

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

If another launcher is already running on the same port, the start shortcut
stops that launcher first and then starts a fresh launcher in the current
terminal. After that, `Ctrl+C` in that terminal stops the launcher server.
This does not stop the system cell services.

Stop only the launcher server from the workspace root:

```powershell
.\stop-home-control-launcher.bat
```

Stop the stack itself separately:

```powershell
.\stop-home-control-stack.bat
```

Or from this repository:

```powershell
.\ops\scripts\home-control-stack\start-home-control-launcher.ps1
```

Use `-ReuseExisting` when you only want to open or reuse the already-running
launcher instead of moving it into the current terminal.
