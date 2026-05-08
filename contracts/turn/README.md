# Turn Contract

The turn contract is the boundary between outer runtimes and thought-core.

Current schemas:

- `turn-request.schema.json`
- `turn-response-events.schema.json`

## Current Request Shape

The current experimental `TurnInput` requires `text`, `turn_id`, and
`session_id`. It accepts optional `locale` and `context_refs` fields.

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
experiment. It is not the primary turn contract. Consumers that can send a full
turn payload should use `POST /turn` or a POST streaming endpoint.

