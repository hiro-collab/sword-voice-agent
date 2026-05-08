# Contracts

Contracts define the public boundaries between `sword-voice-agent`,
`thought-core`, and surrounding services. They are extracted from the current
implementation and docs so the implementation can change without surprising
consumers.

## Areas

| Area | Purpose |
|---|---|
| `turn/` | Turn request and turn response event stream. |
| `events/` | Common thought-core event envelope. |

## Current Sources

The initial contract files mirror these implementation files:

- `services/thought-core/thought_core/schema.py`
- `services/thought-core/thought_core/events.py`
- `tests/test_thought_core_contract.py`

Future schemas for environment, home-control, and expression should be added
from the corresponding module docs before implementation paths are moved.

