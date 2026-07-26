# Module Responsibilities

This document summarizes the whole workspace. Module-specific details stay
inside each module's own docs.

## Responsibility Table

| Module | Owns | Does Not Own |
|---|---|---|
| `sword-voice-agent` | Integration runtime, gesture gate policy, Thought Core watcher entrypoints, local status projection, launcher-facing scripts | Physical camera capture, STT implementation, Home Assistant device semantics, avatar rendering internals |
| `control-plane/core/services/thought-core` | Canonical turn service v0, AI-agent semantic intent, capability/tool selection, structured action proposal, turn event stream, orchestration loop, natural response; package code under `src/thought_core` | STT, camera capture, Home Assistant implementation, unsafe direct execution, display rendering |
| `sword_voice_agent.adapters.openai_broker` | Fixed-destination credential-isolating compatibility boundary, strict payload/bounds validation, broker-owned existing-secret resolution, TLS/proxy/redirect policy, and minimal response reconstruction | AI semantic interpretation, caller-selected model/upstream/parameters, persistent prompt/response storage, or Launcher/Thought Core credential access |
| `sword_voice_agent.apps.openai_broker` | Literal-loopback process entry, single-admission/monotonic-total-deadline HTTP body handling, and bounded health/completion surface | Secret parsing, caller-selectable bind/upstream policy, unbounded request queueing, action/device execution, or runtime adoption authority |
| `ai-talk-core` | Browser/microphone recording, STT, transcript, handoff files | Thought Core request policy, home actions, gesture inference |
| `mediapipe-sword-sign` | Camera Hub, gesture model inference, Camera Hub topics, MediaMTX helper stack | STT, Thought Core, TTS, Home Assistant action state |
| `vision-snapshot-processor` | Snapshot-style vision inference from MediaMTX streams | Camera ownership, gesture inference, environment aggregation |
| `environment-state-server` | Environment snapshot cache, indicator projection, feedback capture, module health aggregation | Camera capture, gesture inference, authoritative home action execution |
| `home-assistant-server` | Safe action allowlist, Home Assistant script execution, action tracking | STT, gesture inference, avatar rendering, turn reasoning |
| `tts-service` | TTS synthesis, playback, status, volume control | Assistant answer generation, avatar rendering, home action decisions |
| `aituber-kit` | Projection Visual, avatar speech queue, browser-facing AITuber UI | Thought Core watcher policy, Home Assistant safety, environment authority |
| `avatar-service` | Standalone avatar runtime and avatar event integration | Thought Core, TTS playback, gesture inference |
| `touchdesigner-ai-controller` | TouchDesigner control GUI, UDP visual trigger, display bridge | Thought Core, TTS synthesis, camera inference |
| `system-house-renderer` | Topology and runtime trace visualization | Long-running service state, runtime control |

## Boundary Questions

When adding or moving functionality, classify it by asking:

1. Is this fast reflex behavior that should not wait for an LLM?
2. Is this one-turn reasoning or tool orchestration?
3. Is this long-running research or review work?
4. Is this observation of the world or module state?
5. Is this execution of an approved action?
6. Is this expression through speech, display, motion, or logs?
7. Is this memory storage, retrieval, or summarization?
8. Is this an external adapter?
9. Is this generated runtime data?

If a feature answers more than one question, split the boundary before moving
files.

## Authority Rules

- Thought Core の AI agent は通常発話の semantic intent、tool/API 選択、引数案、自然な応答を
  所有する。validator/policy は案を許可・拒否できるが、固定語彙で別の意味へ置換しない。
- 実行 adapter は許可済みの構造化操作だけを実行し、発話の意味を再解釈しない。
- Emergency Stop、明示的 Reset、低遅延 reflex は AI を待たない決定的経路にできる。
- fallback-only mode は compatibility/degraded 診断であり、通常 product route ではない。
- Cross-module fields must be documented in `integration-contract.md`.
- State, flag, and ID authority must be documented in `state_authority.md`.
- A module README should explain the module itself, not the whole integration design.
- Experimental and compatibility paths belong in `retired-paths.md`, not in the main start flow.
- Environment snapshots are projections unless their source module is the
  authority for the underlying value.
- Home Assistant action results are owned by `home-assistant-server` and Home
  Assistant. Higher layers may evaluate them but should not redefine their
  meaning.
- AITuber, TouchDesigner, console, and HUD surfaces are displays. They are not
  state authority.
- Runtime files are operational evidence. They are not contracts unless a
  contract document explicitly defines their fields.
- Physical sibling repositories remain implementation modules until contracts,
  manifests, tests, and launch scripts agree on a move.
