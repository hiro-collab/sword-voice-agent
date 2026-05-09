# Reflex Contract

The reflex contract covers fast input state that should not wait for an LLM.
Current reflex inputs come from Camera Hub / MediaPipe and are consumed by the
voice input gate, console status, environment projection, and future
`reflex-core`.

Current implementation:

- `../mediapipe-sword-sign/`
- `src/sword_voice_agent/application/gesture_pipeline.py`
- `src/sword_voice_agent/adapters/status_store.py`

## Current Surfaces

| Surface | Schema | Current sender | Current receiver |
|---|---|---|---|
| Raw gesture state | `gesture-state.schema.json` | Camera Hub / demo gesture tools | `sword-voice-agent` gesture gate |
| Voice gate status | `gesture-receiver-status.schema.json` | `gesture_udp_receiver` / gesture HTTP path | status store, console, input gate |
| Gesture diagnostic status | `gesture-diagnostic-status.schema.json` | Camera Hub diagnostic topic | status store, console, environment status |
| Local reflex status event | `reflex-status-event.schema.json` | status store | console/runtime event log |

## Boundary Rules

- `reflex-core` publishes fast facts and gate decisions. It does not choose
  assistant replies, Home Assistant actions, or TTS text.
- Camera capture and MediaPipe inference remain in `mediapipe-sword-sign` for
  the current v0 implementation.
- `sword-voice-agent` may apply voice input policy to reflex state, but the raw
  gesture authority remains the Camera Hub / MediaPipe source.
- Environment services may project reflex state as observation, but should not
  rewrite the gesture source of truth.

