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
| `tools/` | Tool started/result data payloads carried by thought-core events. |
| `reflex/` | Fast gesture/camera input state and local reflex status events. |
| `environment/` | Environment snapshots and feedback surfaces consumed by Dify/thought-core. |
| `home-control/` | Safe home action preview/execute boundary. |
| `expression/` | Current TTS and AITuberKit presentation payloads. |
| `memory/` | M4 retrieval, candidate, and commit protocol shapes. |
| `access-control/` | Capability authorization decision and audit payloads. |

## Current Sources

The initial contract files mirror these implementation files:

- `services/thought-core/src/thought_core/schema.py`
- `services/thought-core/src/thought_core/events.py`
- `tests/test_thought_core_contract.py`

Reflex contracts are extracted from:

- `src/sword_voice_agent/protocol/messages.py`
- `src/sword_voice_agent/application/gesture_pipeline.py`
- `src/sword_voice_agent/adapters/status_store.py`

Environment and home-control contracts are extracted from sibling module docs
and tests:

- `../environment-state-server/README.md`
- `../environment-state-server/tests/test_http_api.py`
- `../home-assistant-server/docs/integration-contract.md`
- `../home-assistant-server/tests/test_bridge.py`

Expression contracts are extracted from:

- `src/sword_voice_agent/apps/watch_handoff_to_thought_core.py`
- `docs/integration-contract.md`

Memory and access-control contracts are policy-first skeletons. They define the
boundary shape before a standalone `memory-core` or authorization service is
introduced.
