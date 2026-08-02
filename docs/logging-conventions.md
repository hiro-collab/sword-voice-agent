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

## Launcher Runtime Diagnostics

The Node Launcher may send optional owner-scoped JSONL records to the existing
bounded rotating `launcher-stack.log`; this does not create a tracing backend
or another authority. The allowlist is owner class, boundary class, operation
reference, generation, revision, phase, reason class, terminal-proof class,
side-effect certainty, cleanup certainty, and retry class. Required boundaries
include command accepted/rejected, worker dispatch/unknown, state transition,
private-plan adapter, and command terminal.

`private_plan_adapter` and `private_plan_cleanup_failed` are fixed attribution
classes, not artifact publication. Do not include private-plan bytes, digest,
path, payload, local command, PID, port, secret, media, transcript, or provider
payload. Sink failure is ignored by lifecycle authority; absence of the expected
bounded record is a later failure-injection proof failure. Source/static tests
do not establish live ACL, retention, or product-runtime reachability.

Repeated-Stop artifact observation uses only the fixed reason classes
`private_plan_artifact_present`, `private_plan_artifact_invalid`, and
`private_plan_artifact_unavailable`. The owner deduplicates this diagnostic
process-locally by operation reference, revision, and observation class. Exact
absence needs no failure record; a later class change may emit one new bounded
record. Diagnostic dedupe never changes the persisted operation or cleanup
authority.

S4B turn-admission diagnostics reuse the same owner-scoped event surfaces.
Launcher emits `launcher_supervisor / runtime_to_turn_admission`; watcher emits
`thought_core_watcher / turn_admission_fetch`. Both retain only fixed
admission/reason/proof/side-effect/cleanup/retry classes and opaque operation
correlation where already allowed. The request-local challenge, raw wish,
endpoint, request or response body, lease/client proof, and private-plan facts
are never logged. Missing required events fail deterministic proof, while a log
sink failure still cannot change lifecycle or admission truth.
