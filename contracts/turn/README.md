# Turn Contract

The turn contract is the boundary between outer runtimes and thought-core.

Current schemas:

- `turn-request.schema.json`
- `turn-response-events.schema.json`
- `agentic-predecision-context.schema.json`
- `closed-loop-correlation-feedback.v1.json`
- `closed-loop-correlation-feedback.v1.schema.json`

## Agentic Predecision Context

`agentic-predecision-context.v1` is the bounded private input assembled before
the primary AI provider authors one semantic turn decision. It carries explicit
sections for current Environment State, relevant memory, same-session
continuity, system topology, active operations, and recent closed-loop
feedback, plus the immutable capability view used for the decision.

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

## Closed-loop correlation and feedback

`closed-loop-correlation-feedback.v1.json` is the one runtime-loaded authority
for issuer rules, fixed event kinds, transition profiles, proof classes,
redaction allowlists, and projection/provider bounds. Its adjacent schema
validates the descriptor shape; it does not duplicate the descriptor enum
lists. The chain is disabled by default and starts from a fresh v1 session.

The descriptor also pins the Control HTTP ingress matrix. Callers cannot set
`source_authority`; Thought Core derives it after accepting only the exact
display/TTS intent, acknowledgement, pre-send rejection, or ambiguous-send
tuple. Playback, operation transitions, and success proof do not enter through
this route.

Immediately before the output worker calls `urlopen`, it must durably append
the distinct Control-authored `send_attempt_started_outcome_unknown` profile.
That profile is provider-visible as `may_have_submitted / outcome_unknown` and
survives replay. Failure to append blocks the network call; restart/replay does
not resend it, while a later callback may refine it to acknowledgement or
terminal ambiguity.

Historical v0 Journal entries remain telemetry and are not converted into
unresolved v1 operations. The v1 Operation/Output Projection is derived only
from validated Journal entries and may be rebuilt without replaying an
external side effect.

Before any v1 append, one shared fixed secret-like matcher checks every string
in the event envelope and details, including caller correlation identifiers.
A match rejects the event unchanged; it is never redacted into a stored event,
so Journal redaction metadata cannot falsely claim that an embedded secret was
absent.

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
POST /feedback/closed-loop  (v1 gate only)
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
