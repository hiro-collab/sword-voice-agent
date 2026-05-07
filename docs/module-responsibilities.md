# Module Responsibilities

## Responsibility Table

| Module | Owns | Does Not Own |
|---|---|---|
| `sword-voice-agent` | Integration scripts, input gate policy, Dify watcher, local status projection, console | Physical camera capture, STT implementation, Home Assistant device semantics, AITuberKit rendering |
| `ai-talk-core` | Browser/microphone recording, STT, transcript, command, handoff files | Dify request policy, Home Assistant actions, Camera Hub topics |
| `mediapipe-sword-sign` | Camera Hub, gesture model inference, Camera Hub topics, MediaMTX helper stack | Dify, STT, TTS, Home Assistant state |
| `vision-snapshot-processor` | Snapshot-style vision inference from MediaMTX streams, `/vision/.../state` topics | Physical camera capture, gesture inference, Dify snapshot aggregation |
| `environment-state-server` | Snapshot cache for Dify and display indicators | Camera capture, gesture inference, authoritative Home Assistant action execution |
| `home-assistant-server` | Safe action API, Home Assistant script execution, action tracking | STT, gesture inference, avatar rendering |
| `tts-service` | Text-to-speech synthesis/playback, TTS HTTP source, TTS status | Dify answer generation, avatar rendering |
| `aituber-kit` | Projection Visual, avatar speech queue, browser STT surface, HUD | Dify watcher policy, Home Assistant action safety |
| `touchdesigner-ai-controller` | TouchDesigner control GUI, UDP visual trigger, display-runtime HUD bridge | Dify, TTS, Camera Hub inference |
| `avatar-service` | Standalone Three.js/VRM avatar runtime and avatar event contract | Dify, TTS playback, gesture inference |
| `system-house-renderer` | System topology and runtime trace visualization | Long-running service state, runtime control |

## Boundaries

- Cross-module fields must be documented in `integration-contract.md`.
- state, flag, and ID authority must be documented in `state_authority.md`.
- A module README should explain the module itself, not the whole integration design.
- Experimental and compatibility paths belong in `retired-paths.md`, not in the main start flow.
