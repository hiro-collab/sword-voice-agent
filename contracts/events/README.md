# Event Contract

The event contract describes the common envelope emitted by the current
thought-core experiment. Event-specific payloads live under `data` and remain
open-ended for now.

Current schema:

- `event.schema.json`
- `layer.schema.json`

Current schema version:

- `thought-core.event.v0`

The schema intentionally requires the shared metadata fields but allows
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
