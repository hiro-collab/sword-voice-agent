# Component Map

This map names the current modules and their intended logical home. It is a
planning document only: paths listed here are not moved by this change.

## Current To Logical Mapping

| Current path | Logical role | Future candidate | Notes |
|---|---|---|---|
| `<workspace>/sword-voice-agent/` | Integration app, gesture input policy, watchers, console, launcher scripts | `apps/sword-voice-agent` plus `ops/scripts` | Keep current path while launch scripts depend on it. |
| `<workspace>/sword-voice-agent/services/thought-core/` | Canonical turn API v0 and event stream | `services/thought-core` | Current canonical service root; implementation package lives under `src/thought_core`. |
| `<workspace>/services/thought-core/` | Retired local placeholder, if present | none until a split repo is created | Do not add a second implementation here. |
| `<workspace>/ai-talk-core/` | Microphone/browser recording, STT, transcript, handoff | `adapters/stt` or `apps/voice-input` | Existing module owns STT and browser recording. |
| `<workspace>/mediapipe-sword-sign/` | Camera Hub, gesture inference, fast gesture state | `services/reflex-core` and `adapters/mediapipe` | Camera capture authority remains here for now. |
| `<workspace>/vision-snapshot-processor/` | Low-frequency vision snapshots such as room light | environment input source | Feeds environment state; does not aggregate Dify state. |
| `<workspace>/environment-state-server/` | Environment snapshot and indicators API | `services/environment-server` | Current environment-server v0. |
| `<workspace>/home-assistant-server/` | Safe Home Assistant action bridge | `services/home-control-server` and `adapters/home-assistant` | Current home-control-server v0. |
| `<workspace>/tts-service/` | Text-to-speech synthesis, playback, status | `services/expression-core` speech component | Does not generate assistant answers. |
| `<workspace>/aituber-kit/` | Projection Visual, avatar speech queue, browser UI | `apps/aituber-ui` and `adapters/aituber-kit` | Treat as a full upstream-style app, not a folder to absorb. |
| `<workspace>/avatar-service/` | Standalone Three.js/VRM avatar runtime | `apps/avatar-ui` or expression runtime | Useful for future expression-core split. |
| `<workspace>/touchdesigner-ai-controller/` | TouchDesigner GUI and visual trigger bridge | `apps/display-runtime` and `adapters/touchdesigner` | Does not decide action or environment state. |
| `<workspace>/system-house-renderer/` | Topology and runtime trace visualization CLI | `ops/diagnostics` or `tools/system-house-renderer` | Short-lived diagnostic tool, not a service. |
| `<workspace>/.cache/home-control-stack/` | Generated logs, state, PID files, diagnostics | `runtime/` | Compatibility path while scripts still default to it. |
| `<workspace>/scripts/` and root `*.bat` | Root launch shortcuts | `ops/scripts` | Keep shortcuts stable until migration is explicit. |
| `<workspace>/archives/` | Historical snapshots and retired artifacts | `archives/` | Not part of active architecture. |
| `<workspace>/logs/` | Existing runtime-style logs | `runtime/logs` | Audit before moving; may contain local-sensitive data. |
| `<workspace>/tests/` | Workspace-level tests, legacy module tests, and system-boundary tests | `tests/system`, future `tests/contract`, `tests/e2e`, `tests/fixtures` | Keep legacy-compatible root tests stable; put cross-cutting memory/access tests under `tests/system`. |

## Layer View

### Reflex Layer

Current:

- `mediapipe-sword-sign/`
- Camera Hub topics such as `/vision/sword_sign/state`
- gesture input gate code inside `sword-voice-agent/`

Future:

- `services/reflex-core`
- `adapters/mediapipe`
- `contracts/reflex`

### Turn Layer

Current:

- `sword-voice-agent/services/thought-core/`
- Dify workflow and Dify watcher inside `sword-voice-agent/`
- handoff from `ai-talk-core/`

Future:

- `services/thought-core`
- `contracts/turn`
- `contracts/events`

### Environment Layer

Current:

- `environment-state-server/`
- `vision-snapshot-processor/`
- Home Assistant bridge event state
- Camera Hub topic cache

Future:

- `services/environment-server`
- `contracts/environment`

### Action Layer

Current:

- `home-assistant-server/`
- Dify HTTP nodes calling the bridge

Future:

- `services/home-control-server`
- `adapters/home-assistant`
- `contracts/home-control`

### Expression Layer

Current:

- `tts-service/`
- `aituber-kit/`
- `avatar-service/`
- `touchdesigner-ai-controller/`

Future:

- `services/expression-core`
- `apps/aituber-ui`
- `apps/avatar-ui`
- `contracts/expression`

### Ops And Runtime Layer

Current:

- `start-home-control-stack.bat`
- `sword-voice-agent/ops/scripts/home-control-stack/`
- `sword-voice-agent/scripts/home-control-stack/` compatibility wrappers
- `.cache/home-control-stack/`
- launcher process registry and status files

Future:

- `ops/scripts`
- `ops/manifests`
- `ops/process-registry`
- `runtime/logs`
- `runtime/state`
- `runtime/pids`
- `runtime/diagnostics`

## Canonical Path Notes

For now, `<workspace>/sword-voice-agent/services/thought-core/` is the canonical
service root for thought-core v0, with package code under
`src/thought_core/`. The root
`<workspace>/services/thought-core/` path should not receive a second
implementation. A future split should move or mirror the current implementation
only after tests and launch scripts agree on the new path.
