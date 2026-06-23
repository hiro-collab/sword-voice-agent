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
For timed local demonstrations, `Fast visible demo` starts the minimal
Projection Visual plus no-provider Thought Core path.
For timed demonstrations that must reach one bounded appliance handoff,
`Fast action demo` keeps that minimal local display/audio path but also starts
the Home Assistant bridge while still skipping environment state, camera,
vision, and TouchDesigner services.

The fast demo profiles lower the VOICEVOX readiness wait budget with
`VoicevoxReadyTimeoutSeconds`. The Launcher keeps the default at 45 seconds for
normal profiles, while `Fast visible demo` and `Fast action demo` use an
8-second wait so missing speech readiness does not consume the entire
first-response timing budget.

The Launcher exposes source/static diagnostic readiness and startup timing
summaries for later reviewed measurement routes:

- `GET /api/startup-timing` returns `launcher_startup_timing.v0` with expected
  service IDs, first-ready elapsed milliseconds, waiting elapsed milliseconds,
  timeline events, and the current critical-path service ID.
- `GET /api/diagnostic-surfaces` returns the no-live diagnostic surface map for
  audio awareness, Self Mirror temporal motion, Projection Visual display/TTS,
  and OS display/window prompt summaries.
- `GET /api/demo-timed-action-readiness` returns the current `demo-fast-action`
  first-feedback/first-action readiness summary, including required local
  service IDs, target milliseconds, remaining milliseconds to the first-action
  target, Projection Visual URL, and Action bridge operator URL.

For read-only timing collection during a reviewed runtime route, run:

```powershell
node .\tools\home-control-launcher\scripts\collect-demo-timing.mjs --timeout-ms 30000
```

The collector polls only the Launcher summary endpoints above. It does not
start Chrome, start services, send UI input, submit Home Control preview/dry-run
or execute requests, or capture microphone/system/browser audio or screen
content.

When the Home Assistant bridge is enabled, Quick Links includes the local
`/operator` console as `Action bridge operator`. This is a visible/selectable
operator-surface shortcut only; opening the link is not command authority and
does not submit preview, dry-run, execute, or confirm-execute requests.

These endpoints publish class/count/timing summaries only. They do not perform
microphone, system-audio, browser-audio, screen, camera, or Home Control
capture/operation, and they do not publish raw screenshots, video, audio,
transcripts, browser storage, Home Assistant payloads, tokens, or private
paths.

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

Tracked defaults may include all-appliance command-stimulus route metadata such
as `action_ids`, proof ceiling, and configured wait estimates. The Launcher
shows that metadata for planning only. Enabling a row does not call Home
Assistant, submit a Home Control action, publish raw evidence, or upgrade proof
claims; a later reviewed runtime route still owns command submission, timing
measurement, feedback wording, and cleanup.

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
