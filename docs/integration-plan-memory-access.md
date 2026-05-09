# Memory / Access Kernel Integration Plan

This plan connects the small memory/access kernel helpers to existing services
without changing public APIs first. The initial goal is enforcement and
observability at service boundaries, not a full memory-core service rollout.

## Scope

Targets:

- `thought-core-v0` in `services/thought-core`
- `home-control-server v0` in `../home-assistant-server`
- `environment-state-server v0` in `../environment-state-server`
- expression runtimes: AITuberKit, TTS service, TouchDesigner control GUI

Kernel helpers to integrate:

- `PolicyStore.authorize()` / `require()`
- `EventJournal.append_event()`
- `StateStore.write_state()`
- `MemoryStore.write_candidate()`

Non-goals for the first integration:

- Do not change existing HTTP response shapes.
- Do not move sibling repositories.
- Do not replace existing service-local audit/status stores in one step.
- Do not let `thought-core` commit M4 memory directly.

## 1. Authorization Entry Points

| Service | Entry point | Capability check | Notes |
|---|---|---|---|
| `thought-core-v0` | `ThoughtCoreHandler._handle_turn()` | `events.append`, later `memory.read.*` | Validate the request boundary and journal turn start/error. |
| `thought-core-v0` | `ThoughtLoop._call_tool()` | `environment.observe`, `home.preview`, `home.execute.low_risk`, `memory.write.candidate` | Best single choke point for tool-level allow/deny. |
| `thought-core-v0` | confirmation path before `home.execute` | `home.execute.requires_approval` with `user_confirmed` | Keep confirmation logic in thought-core; do not move it into home-control. |
| `home-control-server v0` | `preview_action()` | `home.preview` | Check request source before preview audit. |
| `home-control-server v0` | `execute_action()` | `home.execute.low_risk` or `home.execute.requires_approval` | Keep existing confirmation/dry-run/duplicate behavior. |
| `environment-state-server v0` | `GET /environment/current` | `environment.observe` | Observe-only boundary. |
| `environment-state-server v0` | `POST /feedback/state-query` | `memory.write.candidate` or future `environment.feedback` | Treat user correction as a memory candidate source, not committed memory. |
| expression runtimes | inbound speech/display/message endpoints | `expression.emit` | Start as journal-only where policy identity mapping is unclear. |
| expression runtimes | local status writers | `state.write.own` | Use own-state guard for state projection files. |

Policy identity should be loaded from `policies/access/services.json`. The
first implementation can use service ids already present in `ops/manifests`.

## 2. Events To Journal

Use `EventJournal` as an additional append-only journal at first. Existing logs
remain in place until consumers are migrated.

| Service | Events |
|---|---|
| `thought-core-v0` | `turn.started`, `tool.started`, `tool.result`, `action.proposed`, `thought.retry_planned`, `memory.candidate_requested`, `turn.completed`, `turn.error` |
| `home-control-server v0` | `home.preview.requested`, `home.preview.result`, `home.execute.requested`, `home.execute.accepted`, `home.execute.confirmation_required`, `home.execute.duplicate`, `home.execute.failed` |
| `environment-state-server v0` | `environment.observe.requested`, `environment.snapshot.returned`, `environment.feedback.accepted`, `environment.feedback.rejected` |
| expression runtimes | `expression.message.received`, `expression.speech.started`, `expression.speech.completed`, `expression.interrupted`, `expression.display.updated` |
| access layer | `access.decision` for both allow and deny once boundary wiring exists |

Minimum fields:

- `trace_id`
- `turn_id` when turn-related
- `service`
- `event`
- redacted `payload`

## 3. State To Move Behind StateStore

The first integration should wrap only small public state projections. Large
runtime files and service-specific internal caches stay untouched.

| Owner | Candidate state |
|---|---|
| `thought-core-v0` | current turn status, pending confirmation count, pending state-query count |
| `home-control-server v0` | bridge health summary, last action id/status, fault mode summary |
| `environment-state-server v0` | latest snapshot id, source freshness, health/ready summary |
| TTS service | current TTS phase, current request id, volume summary |
| TouchDesigner control GUI | UDP readiness, last ping, UI control state |
| AITuberKit | current expression/display/speech status if exposed server-side |

Rule: each service writes only `<service_id>.state.json`. Cross-service reads
should go through status/observe APIs or read-only projections.

## 4. Memory Candidate Flow From Thought Core

`MemoryStore.write_candidate()` should be called from thought-core only at
explicit learning points.

Initial call sites:

1. `ThoughtLoop._save_post_action_room_light_learning()`
   - Scope: `failure_patterns` or `episodic`
   - Source: successful post-action observation with trace/turn id
   - Candidate only; no commit.

2. `ThoughtLoop._persist_state_query_feedback()`
   - Scope: `failure_patterns` for observation correction patterns
   - Scope: `user_preferences` only when the user explicitly expresses a preference
   - `user_preferences` remains confirmation-required.

3. Retry exhaustion path after repeated failed verification
   - Scope: `failure_patterns`
   - Content: action id, expected state, attempts, observation mismatch summary

Do not call `MemoryStore.commit()` from thought-core. Commit remains
`memory_core` authority, even while memory-core is an in-process test helper.

## 5. Minimal Non-Breaking Change Set

Recommended order:

1. Add optional construction parameters to `ThoughtLoop`:
   - `policy: PolicyStore | None`
   - `journal: EventJournal | None`
   - `memory: MemoryStore | None`
   Default remains `None`, preserving current behavior.

2. Wrap `ThoughtLoop._call_tool()` with optional `policy.require()`.
   Existing tests can pass no policy and remain unchanged.

3. Emit journal copies from `ThoughtLoop.run()` and `_call_tool()`.
   Keep existing `ThoughtEvent` output as the public API.

4. Add optional journal/policy adapters to home-control and environment app
   factories. Existing factory defaults keep current tests and launches working.

5. Add StateStore only to service-local status writers after journal wiring is
   stable.

6. Leave AITuberKit and TouchDesigner as read/display consumers first. Add
   expression events only where there is already server-side status output.

## 6. Mock Tests To Fix Before Service Wiring

Add or extend mock tests before touching live startup scripts:

- `thought-core` calls `policy.require("environment.observe")` before observe.
- `thought-core` calls `policy.require("home.preview")` before preview.
- `thought-core` denies `home.execute.requires_approval` without confirmation.
- `thought-core` writes memory candidates but cannot commit them.
- `home-control-server` journals confirmation-required, dry-run, duplicate, and
  accepted execute results.
- `environment-state-server` journals observe requests and feedback acceptance.
- `StateStore` rejects cross-service writes from home/environment/expression.
- Nested secret values in event payloads are redacted.
- Denied authorization decisions are written as `access.decision` events.
- All tests use `TemporaryDirectory` for runtime/logs/local data.

Current home for these tests is `tests/system/`. Keep root-level tests for
current module compatibility and use `tests/system/` for cross-cutting
memory/access/kernel behavior.

## Rollout Gate

Do not enable kernel wiring in the normal `thought-core-v0` profile until:

- `uv run python -m unittest discover -s tests` passes.
- `ops/scripts/system.ps1 start -Profile thought-core-v0 -DryRun` still prints
  the same service commands.
- A mock light retry scenario shows retry in thought-core events, not
  home-control internal retry.
- Denied decisions are visible in the access journal without leaking secrets.
