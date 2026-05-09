# Logging Conventions

Logs and status records should make the active system layer visible. This keeps
debugging from collapsing into "the stack is broken" and instead points at the
boundary that is currently doing work.

## Layer Tags

Use these layer names for new structured logs, events, status records, and
diagnostics:

| Layer | Meaning |
|---|---|
| `app` | Human-facing UI or outer local runtime. |
| `reflex` | Fast input reactions that do not wait for an LLM. |
| `turn` | One-turn reasoning, tool choice, response shaping. |
| `deep` | Long-running research, review, or planning. |
| `environment` | Observation of world or module state. |
| `action` | Execution of approved operations. |
| `expression` | Speech, display, motion, emotion, and presentation. |
| `memory` | Storage, retrieval, summarization, and feedback memory. |
| `adapter` | External API, framework, device, or sibling-module driver. |
| `contract` | Boundary schema or compatibility validation. |
| `runtime` | Generated logs, PID files, caches, state, diagnostics. |
| `ops` | Start, stop, status, supervision, manifests, process registry. |
| `docs` | Design intent and decision records. |

## Structured Records

Prefer a top-level `layer` field when adding new JSON/JSONL records:

```json
{
  "layer": "ops",
  "type": "process.started",
  "source": "home-control-stack",
  "service": "thought-core"
}
```

For thought-core events, `layer` is optional and defined by
`contracts/events/layer.schema.json`.

## Event Journal

The cross-service event journal is M3. It records operational facts, not raw
signals or committed long-term memory. Prefer
`contracts/events/system-event.schema.json` for new shared journal records.

Journal records should be append-only and include enough correlation to replay
or audit an action path:

```json
{
  "schema_version": "system.event.v0",
  "event_id": "evt_001",
  "ts": "2026-05-08T12:00:00+09:00",
  "trace_id": "trace_001",
  "turn_id": "turn_001",
  "service": "thought-core",
  "layer": "turn",
  "event": "tool.started",
  "level": "info",
  "payload": {
    "tool": "environment.observe"
  }
}
```

Do not append M0 frames, secrets, or full prompt drafts to the journal. If a
journal pattern becomes useful long-term knowledge, create a memory candidate
and let `memory-core` decide whether to commit it.

## Text Logs

For text logs, put the layer in the prefix or logger name:

```text
[ops] starting thought-core
[adapter:mediapipe] connected camera hub topic
[turn] tool.result home.execute status=accepted
```

Do not use a runtime log as the only place where a boundary is defined. If a log
field becomes part of an integration promise, move the promise to `contracts/`
or an integration doc.
