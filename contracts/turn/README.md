# Turn Contract

The turn contract is the boundary between outer runtimes and thought-core.

Current schemas:

- `accepted-user-speech-candidate.schema.json`
- `turn-request.schema.json`
- `turn-response-events.schema.json`

## Current Request Shape

The current v0 `TurnInput` requires `text`, `turn_id`, and `session_id`. It
accepts optional `locale` and `context_refs` fields.

```json
{
  "text": "turn text",
  "turn_id": "turn_001",
  "session_id": "living_room_main",
  "locale": "ja-JP",
  "context_refs": {
    "voice_turn": "voice_789"
  }
}
```

## Current Endpoints

```text
POST /turn
POST /turn?stream=true
POST /turn/stream
```

`POST /turn` returns JSON with an `events` array. Streaming endpoints return
SSE messages where each `data` payload is the full event envelope.

## Demo And Compatibility Endpoint

```text
GET /turn/stream?text=...
```

The GET stream route is a lightweight EventSource/demo entrypoint in the current
implementation. It is not the primary turn contract. Consumers that can send a
full turn payload should use `POST /turn` or a POST streaming endpoint.

## Accepted Speech Candidate Boundary

`accepted-user-speech-candidate.schema.json` is the local AI Talk Core/input-gate
handoff shape for speech that has been accepted as a user turn candidate before
conversion to a normal Thought Core turn request.

Redacted recognition summaries, self-output observations, and browser STT
class/count/bucket results must not be converted directly to Thought Core
`TurnInput`. Only an accepted candidate with `accepted_text`,
`may_start_user_turn=true`, `turn_adoption_authority=true`, and
`raw_private_publication_flags=false` may be converted to a normal turn request.

Shared reports still publish only class/count/bucket/opaque-ref fields. Curated
prepared local sample expected and recognized text may be used in exact
source/static routes and tests as non-private development material; live/private
utterances, raw audio/media, private paths, provider payloads, logs, browser
storage, tokens, and secrets remain protected.
