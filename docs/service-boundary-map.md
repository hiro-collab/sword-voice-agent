# Service Boundary Map

This is the current service and adapter map before large physical moves. It is
the short operational version of the OS-style architecture:

```text
contracts = system-call boundary
services  = system capabilities
adapters  = drivers and external/sibling protocols
ops       = init, process registry, start/stop/status
runtime   = generated evidence
docs      = ADRs and design intent
```

## Current V0 Map

| Logical service | Layer | Current implementation | Contracts | Adapter / driver edges |
|---|---|---|---|---|
| `sword-voice-agent` | `app` | `sword-voice-agent/` | `turn`, `events`, `expression`, `reflex`, `memory`, `access-control` | `ai-talk-core`, `aituber-kit`, `tts-service`, `thought-core` client |
| `reflex-core` | `reflex` | `mediapipe-sword-sign/` plus gesture gate policy in this repo | `reflex`, `access-control` | MediaPipe, camera, MediaMTX, Camera Hub topics |
| `thought-core` | `turn` | `sword-voice-agent/services/thought-core/` | `turn`, `events`, `tools`, `memory`, `access-control` | OpenAI-compatible LLM, Dify compatibility, environment/home-control clients |
| `environment-server` | `environment` | `environment-state-server/` | `environment` | MediaPipe status, vision snapshot, Home Assistant state, module status |
| `home-control-server` | `action` | `home-assistant-server/` | `home-control` | Home Assistant scripts and allowlist |
| `expression-core` | `expression` | `tts-service/`, `aituber-kit/`, `touchdesigner-ai-controller/`, avatar modules | `expression` | VOICEVOX/TTS, AITuberKit, TouchDesigner |
| `deep-core` | `deep` | future | `memory`, `access-control` | research/review engines |
| `memory-core` | `memory` | future | `memory`, `events`, `access-control` | storage, summary, search backends |
| `ops` | `ops` | `ops/scripts/system.ps1`, `ops/scripts/home-control-stack/`, `ops/manifests/` | `events/layer` for layer names | PowerShell, process registry, Docker Compose when selected |

## Memory I/O Map

| Logical service | Reads | Writes | Rule |
|---|---|---|---|
| `reflex-core` | `M0`, `M5` | `M1`, `M2`, `M3` | Never reads long-term memory; emits meaning-level reflex events. |
| `thought-core` | `M1`, `M2`, selected `M4`, `M5` | `M2`, `M3`, M4 candidates | Orchestrates turns and proposes memory; does not commit M4 or read M6. |
| `deep-core` | `M3` summaries, `M4`, `M5` | `M2`, `M3`, M4 candidates | Researches and proposes; does not execute home actions. |
| `environment-server` | `M0` projections, `M1`, `M5` | `M1`, `M3` | Observes and projects; does not execute. |
| `home-control-server` | `M5`, adapter secrets through ops runtime | `M1`, `M3` | Executes one action boundary request; does not own meaning-level retry. |
| `expression-core` | selected `M4` preferences, `M5` | `M1`, `M3` | Presents speech/display/motion; does not rewrite memory. |
| `memory-core` | `M3` summaries, candidates, existing `M4`, `M5` policy | `M4`, `M3` | Classifies, deduplicates, commits, summarizes; does not read M6. |
| `ops` | `M5`, `M6`, process state | `M1`, `M3`, `runtime/pids` | Starts/stops/statuses services and owns secret injection. |

## Movement Rules

- Keep large Git-managed sibling modules as physical implementation modules
  until contracts, ops profiles, tests, and launch docs agree on a move.
- Promote behavior by naming its logical service and contract first, then move
  files only when the move can be verified.
- Do not create parallel implementations such as a second `services/thought-core`
  outside the current canonical root.
- Keep compatibility wrappers while root shortcuts and the launcher still use
  them.
- Delete generated caches and clearly retired artifacts freely; archive
  historical design context only when it is no longer referenced by active docs.

## Practical Next Moves

1. Harden `thought-core` as the canonical turn service v0.
2. Keep MediaPipe code in `mediapipe-sword-sign`, but treat its public payloads
   as the `reflex` contract.
3. Keep `environment-state-server` and `home-assistant-server` as current v0
   service implementations while their adapters stay behind their service
   boundaries.
4. Keep the lifecycle system centralized in `ops/scripts/system.ps1` and the
   inherited supervisor under `ops/scripts/home-control-stack/`.
