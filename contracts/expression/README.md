# Expression Contracts

Expression contracts describe payloads sent from the integration runtime to
speech and display surfaces. The current stable surfaces are adapter payloads,
not a full `expression-core` service API yet.

## Current Surfaces

| Surface | Schema | Current sender | Current receiver |
|---|---|---|---|
| TTS streaming chunk | `speech-chunk.schema.json` | `watch_handoff_to_thought_core.py` | `tts-service` `POST /api/tts/chunk` |
| AITuberKit direct speech | `aituber-message.schema.json` | `watch_handoff_to_thought_core.py` | AITuberKit `POST /api/messages?clientId=<id>&type=direct_send` |

Logical boundary:

- Service: future `expression-core`
- Apps: `aituber-ui`, `avatar-ui`, projection/display surfaces
- Current adapter targets: `tts-service`, `aituber-kit`, TouchDesigner display
  runtime

## Boundary Rules

- `thought-core` emits semantic turn events such as `assistant.speech_delta` and
  `assistant.message`; expression adapters decide how those become TTS chunks or
  UI messages.
- The TTS surface receives incremental speech and a final marker. It should not
  decide tool orchestration or action success.
- The AITuberKit surface receives display/speech queue messages. It is a
  presentation target, not state authority.
- Canonical assistant output uses `assistant_message_id`. The legacy
  `message_id` field carries the same canonical value and is not the enclosing
  Thought Core event ID.
- Adapter HTTP completion is transport submission evidence only. It does not
  prove TTS playback, visible pixels, physical state, or user observation.
- Future `expression-core` event schemas should be added only after a concrete
  sender/receiver pair exists.
