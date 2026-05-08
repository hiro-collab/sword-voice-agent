# Event Contract

The event contract describes the common envelope emitted by the current
thought-core experiment. Event-specific payloads live under `data` and remain
open-ended for now.

Current schema:

- `event.schema.json`

Current schema version:

- `thought-core.event.v0`

The schema intentionally requires the shared metadata fields but allows
additional fields so additive metadata can be introduced without breaking older
consumers.

