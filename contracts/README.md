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
| `environment/` | Environment snapshots and feedback surfaces consumed by Dify/thought-core. |
| `home-control/` | Safe home action preview/execute boundary. |
| `expression/` | Current TTS and AITuberKit presentation payloads. |

## Current Sources

The initial contract files mirror these implementation files:

- `services/thought-core/thought_core/schema.py`
- `services/thought-core/thought_core/events.py`
- `tests/test_thought_core_contract.py`

Environment and home-control contracts are extracted from sibling module docs
and tests:

- `../environment-state-server/README.md`
- `../environment-state-server/tests/test_http_api.py`
- `../home-assistant-server/docs/integration-contract.md`
- `../home-assistant-server/tests/test_bridge.py`

Expression contracts are extracted from:

- `src/sword_voice_agent/apps/watch_handoff_to_thought_core.py`
- `docs/integration-contract.md`
