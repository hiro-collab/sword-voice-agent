# Event Contract

The event contract describes the common envelope emitted by the current
thought-core experiment. Event-specific payloads live under `data` and remain
open-ended for now.

Current schemas:

- `event.schema.json`
- `system-event.schema.json`
- `layer.schema.json`

Current schema version:

- `thought-core.event.v0`
- `system.event.v0`

`event.schema.json` is the thought-core event stream envelope.
`system-event.schema.json` is the cross-service M3 event journal envelope.
The schemas intentionally require shared metadata fields but allow
additional fields so additive metadata can be introduced without breaking older
consumers.

## Layer Metadata

New logs, status records, and events should identify the logical layer when it
helps debugging. For structured events, use optional `layer`; for text logs,
include the layer in the logger name, prefix, or adjacent status payload.

Layer values are shared with `layer.schema.json`:

- `app`
- `reflex`
- `turn`
- `deep`
- `environment`
- `action`
- `expression`
- `memory`
- `adapter`
- `contract`
- `runtime`
- `ops`
- `docs`
