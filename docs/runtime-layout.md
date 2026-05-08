# Runtime Layout

Generated files should eventually live under `runtime/`, grouped by operational
meaning. The current active stack still defaults to `.cache/home-control-stack`.
That path remains the compatibility path until launch scripts migrate their
default.

## Target Layout

```text
runtime/
  logs/
    events/
    services/
  state/
    feedback/
  pids/
  cache/
    screenshots/
    surfaces/
  diagnostics/

local/
  memory/
  config/
  secrets/

policies/
  access/
```

## Current Compatibility Mapping

| Current path | Target category | Future path |
|---|---|---|
| `.cache/home-control-stack/dify-chat-events.jsonl` | event log | `runtime/logs/events/dify-chat-events.jsonl` |
| `.cache/home-control-stack/thought-core-chat-events.jsonl` | event log | `runtime/logs/events/thought-core-chat-events.jsonl` |
| `.cache/home-control-stack/logs/` | service logs | `runtime/logs/services/` |
| `.cache/home-control-stack/launcher-state.json` | launcher state | `runtime/state/launcher-state.json` |
| `.cache/home-control-stack/mediapipe-status.json` | module state projection | `runtime/state/mediapipe-status.json` |
| `.cache/home-control-stack/feedback/` | feedback state | `runtime/state/feedback/` |
| `.cache/home-control-stack/pids.json` | process registry state | `runtime/pids/home-control-stack.json` |
| `.cache/diagnostics/` | diagnostics | `runtime/diagnostics/` |
| `.cache/*.png` | UI screenshots/cache | `runtime/cache/screenshots/` |

## Local And Policy Layout

| Target path | Category | Source control rule |
|---|---|---|
| `local/memory/` | M4 memory candidates, facts, episodes, summaries | Local data ignored; README/examples only. |
| `local/config/` | M5 user/device/service configuration | Local data ignored unless intentionally promoted as example config. |
| `local/secrets/` | M6 local-only secret material | Prefer `.env` or OS secret store; do not commit secret values. |
| `policies/access/` | M5 capability and memory-scope policy | Commit reviewed policy files. |

## Module-Local Runtime Paths Not Yet Mapped

The table above focuses on the current Home Control Stack compatibility path. It
is not an exhaustive migration checklist. Several modules also define
module-local runtime outputs that should be audited before any default path
change.

| Current path | Notes |
|---|---|
| `sword-voice-agent/.cache/sword_voice_agent/` | Status projection and event log used by sword-voice-agent and thought-core watcher flows. |
| `sword-voice-agent/.cache/codex/web_latest.json` | ai-talk-core handoff/watch compatibility path used by the thought-core experiment. |
| `tts-service/.cache/tts_service/` | TTS status, runtime status, event logs, and generated audio outputs. |
| `environment-state-server/.cache/environment_state_server/` | State-query feedback path used by the environment server examples. |
| `home-assistant-server/.cache/home_control/` | Home-control bridge event/audit style runtime output used by integration scripts. |

## Migration Rules

- Do not move PID files while managed processes are running.
- Do not delete existing logs as part of layout cleanup.
- Add runtime-root options to scripts before changing defaults.
- Keep `.cache/home-control-stack` readable for at least one migration phase
  after a default path change.
- Treat generated audio, Dify payloads, screenshots, event logs, and local paths
  as local-sensitive data.
- Treat M4 memory files as local-sensitive by default. Commit only fixtures or
  examples that have been scrubbed.
- Keep M6 out of logs, memory candidates, examples, and screenshots.

## Source Control Policy

`runtime/` should contain documentation and, if needed, `.gitkeep`-style marker
files only. Runtime logs, state, PID files, caches, and diagnostics should not
be committed. Sample payloads belong under `tests/fixtures/`.
