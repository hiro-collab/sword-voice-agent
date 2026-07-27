# Turn Contract

The turn contract is the boundary between outer runtimes and thought-core.

Current schemas:

- `turn-request.schema.json`
- `turn-response-events.schema.json`
- `agentic-predecision-context.schema.json`

## Agentic Predecision Context

`agentic-predecision-context.v1` is the bounded private input assembled before
the primary AI provider authors one semantic turn decision. It carries explicit
sections for current Environment State, relevant memory, same-session
continuity, and system topology, plus the immutable capability view used for
the decision.

Every section reports `available`, `missing`, `unavailable`, `stale`, or
`conflict`. The current slice supplies Environment State, relevant memory, and
same-session continuity before the provider call. System topology remains an
explicit `missing` section until its producer is connected. The current human
wish stays authoritative as the newest input; a bounded latest same-session
correction overrides older continuity and memory summaries.

The contract contains summaries and bounded facts only. It excludes raw
credentials, secrets, paths, URLs, ports, command lines, provider payloads,
configuration documents, and JSONL records. Runtime serialization is capped at
32,768 UTF-8 bytes, eight items per section, twelve properties per item, eight
values per nested list, three nested value levels, and 512 total value nodes.

The context informs semantic judgment but does not authorize execution.
Existing catalog, schema, confirmation, execution, observation, receipt, and
cleanup code retains those responsibilities. Explicit no-provider operation
continues to use its compatibility route and is not the primary agentic route.

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
