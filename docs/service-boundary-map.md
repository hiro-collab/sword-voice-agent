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
| `sword-voice-agent` | `app` | `sword-voice-agent/` | `turn`, `events`, `expression`, `reflex` | `ai-talk-core`, `aituber-kit`, `tts-service`, `thought-core` client |
| `reflex-core` | `reflex` | `mediapipe-sword-sign/` plus gesture gate policy in this repo | `reflex` | MediaPipe, camera, MediaMTX, Camera Hub topics |
| `thought-core` | `turn` | `sword-voice-agent/services/thought-core/` | `turn`, `events`, `tools` | OpenAI-compatible LLM, Dify compatibility, environment/home-control clients |
| `environment-server` | `environment` | `environment-state-server/` | `environment` | MediaPipe status, vision snapshot, Home Assistant state, module status |
| `home-control-server` | `action` | `home-assistant-server/` | `home-control` | Home Assistant scripts and allowlist |
| `expression-core` | `expression` | `tts-service/`, `aituber-kit/`, `touchdesigner-ai-controller/`, avatar modules | `expression` | VOICEVOX/TTS, AITuberKit, TouchDesigner |
| `deep-core` | `deep` | future | future | research/review engines |
| `memory-core` | `memory` | future | future | storage, summary, search backends |
| `ops` | `ops` | `ops/scripts/system.ps1`, `ops/scripts/home-control-stack/`, `ops/manifests/` | `events/layer` for layer names | PowerShell, process registry, Docker Compose when selected |

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

