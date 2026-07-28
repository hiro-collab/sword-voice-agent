# Integration Contract

この文書は、モジュール間の接続契約だけを扱います。設計背景や検討履歴は置きません。

Machine-readable schema files for the current thought-core turn/event stream
live under `contracts/`. This document remains the human-readable integration
map across modules.

## Process Entry

通常の統合起動は workspace 直下から行う。

```powershell
.\start-home-control-stack.bat -StopExisting
.\status-home-control-stack.bat
.\stop-home-control-stack.bat
```

起動入口は `control-plane\core\ops\scripts\system.ps1`。supervisor 実体は
`control-plane\core\ops\scripts\home-control-stack\` にある。root の `.bat` は
canonical control plane への薄いショートカットとして維持する。

Runtime state defaults to `.cache\home-control-stack`. Advanced runs can point
start/status/stop scripts at another compatible state directory with
`-StackStateDir <path>` or `HOME_CONTROL_STACK_STATE_DIR`. Relative paths are
resolved from the workspace root. The default path remains unchanged.

## Launcher API

The local Launcher exposes operator configuration and readiness/status
projections for the current workspace. These payloads are summary/status
contracts only. They are not live command authority, not Home Assistant or Home
Control proof execution, and not release/readiness/final-pass approval.

`GET /api/state` includes:

| Field | Meaning |
|---|---|
| `demoSafeSettings` | Effective `demo_safe_settings.v0` rows formed from tracked defaults plus local gitignored operator overrides. Rows carry editable setting fields such as `enabled`, `restore_required`, `max_action_count`, and `max_duration_sec`, plus read-only route metadata such as `action_ids`, `feedback_stimulus_class`, `state_requirement_class`, `timing_estimate_sec`, `timing_estimate_source_class`, `measurement_required`, `proof_ceiling`, and `does_not_prove`. |
| `demoReadinessStatus` | Read-only `demo_readiness_status.v0` rows derived from local service/status checks. Rows carry `status_class`, `source_class`, `proof_ceiling`, `does_not_prove`, and `last_checked_class`. |

`POST /api/save-config` accepts the existing profile/options payload and may
also include:

```json
{
  "demoSettings": {
    "rows": [
      {
        "id": "appliance.aircon_cool_restore",
        "enabled": false,
        "restore_required": true,
        "max_action_count": 1,
        "max_duration_sec": 120,
        "action_ids": ["aircon_cool", "aircon_hvac_off"],
        "feedback_stimulus_class": "appliance_command_stimulus",
        "state_requirement_class": "current_state_optional_for_command_stimulus",
        "timing_estimate_sec": 70,
        "timing_estimate_source_class": "configured_wait_window_before_live_measurement",
        "measurement_required": true
      }
    ]
  }
}
```

The Launcher persists only normalized local override fields into its gitignored
state directory. It ignores unknown demo row ids and re-normalizes values
against tracked defaults. API consumers must treat `demoSafeSettings` as local
operator preference/config and `demoReadinessStatus` as read-only status. Neither
payload authorizes preview, dry-run, live execute, Home Assistant service calls,
Home Control `/actions` execution, raw/private publication, or proof upgrade.
For appliance command-stimulus demos, unknown current state is reported as a
proof limitation unless the reviewed route explicitly requires current-state
proof before command submission.

Launcher also exposes summary endpoints used by reviewed diagnostic and timing
routes:

| Endpoint | Contract | Does not prove |
|---|---|---|
| `GET /api/startup-timing` | `launcher_startup_timing.v0` class/count/timing summary for expected launcher services, readiness timeline, waiting elapsed milliseconds, and current critical-path service ID. | Chrome cold start, startup-speed pass, browser foreground proof, or user-visible feedback proof. |
| `GET /api/diagnostic-surfaces` | Source/static diagnostic inventory for audio awareness, Self Mirror temporal motion, Projection Visual display/TTS, Projection Visual response binding, and OS display/window prompt. | Live capture, live bubble render, exact display/TTS parity, user-heard audio, browser-visible avatar motion, OS-screen proof, or raw media publication. |
| `GET /api/demo-timed-action-readiness` | Read-only/non-command readiness summary for `demo-fast-action`, including route-local URLs, next operator steps, and reviewed action IDs for later bounded routes. | Home Control command authorization, `/actions` catalog proof, preview, dry-run, execute, CheckTracking, CheckState, HA-visible state, or physical device proof. |

Diagnostic surface proof layers must remain separate: audio awareness is not
Self Mirror motion, Self Mirror temporal metrics are not VRM telemetry or
browser-visible avatar proof, Projection Visual display/TTS is not response
receiver binding, and response binding is not exact display/TTS parity or
user-heard audio.

## Camera Hub

| Item | Contract |
|---|---|
| Topic URL | `ws://127.0.0.1:8765` |
| Gesture topic | `/vision/sword_sign/state` |
| Camera status topic | `/camera/status` |
| Room light topic | `/vision/room_light/state` |
| Video display | MediaMTX WebRTC/HLS, usually `http://127.0.0.1:8889/cam0` |
| Camera Hub inference input | Camera Hub reads MediaMTX RTSP with `ffmpeg-pipe` |
| Snapshot processor input | Vision Snapshot Processor reads MediaMTX RTSP as a stream consumer |

Camera Hub owns physical camera capture, frame reading, landmark inference, and gesture inference. Vision Snapshot Processor owns snapshot-style vision inference such as room-light state. Other modules subscribe to topics.

## Environment State Server

| Endpoint | Consumer | Notes |
|---|---|---|
| `GET /environment/current` | Thought Core | Requires Bearer token |
| `GET /environment/current?wait_for=room_light&after=<iso>&timeout_ms=1500` | Thought Core | Short wait for a room-light snapshot newer than `after`; returns 200 even on timeout |
| `POST /environment/relations` | Thought Core | Related metadata only |
| `POST /feedback/state-query` | Thought Core | User correction for the immediately preceding state query; non-authoritative learning data |
| `GET /feedback/state-query/recent` | Thought Core / debug | Recent feedback records; Requires Bearer token |
| `GET /feedback/state-query/summary` | Thought Core / debug | Feedback label/status counts; Requires Bearer token |
| `GET /indicators/current` | HUD / Cube / display-runtime | Loopback display-safe API |
| `GET /health` | launcher / checks | Diagnostics |
| `GET /ready` | launcher / checks | Fails when required state is stale |

Environment State Server subscribes to Camera Hub topics, Vision Snapshot Processor topics, and Home Assistant bridge events. It does not open the camera.

For room-light state queries, Thought Core reads `state_queries.room_light` from `/environment/current`. `vision_snapshot_processor` remains the authority for image-derived `on/off/unknown`, `lighting_type`, and probability values; `environment_state_server` only projects them into `available`, `stale`, `stale_reason`, `confidence_label`, `answer_hint`, `authority`, `projected_by`, `observed_at`, `updated_at`, `source_snapshot_id`, normalized `evidence`, and the non-authoritative `learning` summary.

When Thought Core needs a post-action room-light snapshot, it calls:

```text
GET /environment/current?wait_for=room_light&after=<action_time>&timeout_ms=1500
```

`timeout_ms` is capped by Environment. The response always remains an environment snapshot. It additionally includes:

```json
{
  "wait_result": {
    "target": "room_light",
    "matched": true,
    "after": "2026-05-07T14:15:00+09:00",
    "timeout_ms": 1500,
    "observed_at": "2026-05-07T14:15:00.320000+09:00",
    "elapsed_ms": 320,
    "reason": "matched"
  }
}
```

`wait_result.matched=true` means `state_queries.room_light.observed_at` is newer than `after` and `state_queries.room_light.source_snapshot_id` is present. Only then should Thought Core treat `state_queries.room_light` as post-action evidence. If no newer room-light snapshot arrives in time, the response is still HTTP 200 with `wait_result.matched=false` and `wait_result.reason="timeout"`. Thought Core should then avoid treating the room-light snapshot as post-action evidence.

When Thought Core asks a follow-up such as "実際はついてる?", the user's next short correction is sent to `POST /feedback/state-query`. The payload includes `target=room_light`, `snapshot_id`, `current_snapshot_id`, `predicted_state`, `predicted_confidence_label`, `user_label`, `user_text`, `workflow_version`, `feedback_reason`, `idempotency_key`, and the original projected evidence. Environment should store this as `authority=user_feedback` training material without rewriting the authoritative vision state for that snapshot. Thought Core only sends feedback while the pending state query is fresh, currently within 120 seconds; stale corrections ask the user to re-check state instead.

Example:

```json
{
  "type": "state_query_feedback",
  "target": "room_light",
  "state_query_id": "room_light",
  "idempotency_key": "state-query-feedback:<conversation_id>:<snapshot_id>:on",
  "snapshot_id": "env_...",
  "current_snapshot_id": "env_...",
  "predicted_state": "unknown",
  "predicted_confidence_label": "low",
  "user_label": "on",
  "user_text": "ついてるよ",
  "authority": "user_feedback",
  "source": "thought_core",
  "workflow_version": "hca-issue-iteration-state-feedback-...",
  "feedback_reason": "user_correction_after_state_query",
  "pending": {
    "authority": "vision_snapshot_processor",
    "projected_by": "environment_state_server",
    "created_at": "2026-05-07T14:15:00+09:00",
    "observed_at": "2026-05-07T14:15:00+09:00",
    "updated_at": "2026-05-07T14:15:00+09:00",
    "answer_hint": "日光の影響が強そうだ。",
    "evidence": {}
  }
}
```

Accepted `user_label` values are `on`, `off`, `daylight`, and `unknown`. Environment adds `received_at` and `received_snapshot_id` at receive time. If `idempotency_key` is repeated, Environment returns the same `feedback_id` with `duplicate=true` and does not append a second JSONL line. If `pending.created_at` / `updated_at` / `observed_at` or `snapshot_id` is older than 120 seconds, Environment stores the record as `status=accepted_with_warning` with `warnings=["pending_stale"]`.

Known feedback warning values are `pending_stale`, `snapshot_mismatch`, `wait_timeout`, `duplicate`, and `invalid_context`. The initial Environment implementation emits `pending_stale`; the others are reserved contract values for later validation layers.

Thought Core can also request room-light feedback after a successful `light_on` or `light_off` action when `wait_result.matched=true` and the fresh post-action Environment snapshot is unknown, low confidence, or disagrees with the expected state. In that case it keeps the same `POST /feedback/state-query` endpoint and sends `feedback_reason=user_correction_after_light_action`, `source_context=post_light_action`, `action_id`, `issue_id`, `expected_state`, and the matching `wait_result`. This remains user_feedback training material; it does not change Home Assistant action authority or vision authority immediately. If `wait_result.matched=false`, Thought Core should not create this post-action feedback pending item from the stale snapshot; it may only tell the user that the vision update has not arrived yet.

Thought Core diagnostics include `feedback_contract.state_query_feedback=true`, `feedback_contract.post_action_light_feedback=true`, `feedback_contract.wait_for_room_light`, `ttl_seconds`, `idempotency_key_format`, and `feedback_reasons` so launcher-side checks can confirm the runtime matches this contract.

Successful response:

```json
{
  "ok": true,
  "feedback_id": "sqf_...",
  "received_snapshot_id": "env_...",
  "duplicate": false,
  "status": "accepted",
  "warnings": []
}
```

If persistence is unavailable, return a non-2xx status with a short `error`; Thought Core treats this endpoint as best-effort and continues the conversation. Debug endpoints accept `target=room_light`; `recent` also accepts `limit`. `summary` returns `label_counts`, `status_counts`, `reason_counts`, `source_context_counts`, `action_counts`, `expected_state_counts`, and `learning`. `learning.level` is one of `none`, `collecting`, `seeded`, `usable`, or `reinforced`; `learning.problems[]` carries machine-readable `code`, `severity`, and `message` for cases such as missing labels, no post-action feedback, stale feedback, duplicates, or rejected payloads. `status_counts` has fixed keys for `accepted`, `accepted_with_warning`, `duplicate`, and `rejected`; duplicate/rejected counts are runtime diagnostics and may reset when Environment State Server restarts.

## Thought Core

| Env | Meaning |
|---|---|
| `THOUGHT_CORE_BASE_URL` | Thought Core API base URL, for example `http://127.0.0.1:18787` |
| `THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED` | Disabled by default. Enables the fresh-session v1 Journal/projection/output-feedback chain. |
| `THOUGHT_CORE_EVENT_JOURNAL_PATH` / `THOUGHT_CORE_EVENT_JOURNAL_DIR` | Existing local append-only Journal location; v0 telemetry and v1 closed-loop entries share the file without conversion. |
| `ENVIRONMENT_STATE_URL` | URL for `/environment/current` |
| `ENVIRONMENT_RELATIONS_URL` | URL for `/environment/relations` |
| `ENVIRONMENT_FEEDBACK_URL` | URL for `/feedback/state-query` |

State lookups such as "電気ついてる?" must not execute Home Assistant actions. Thought Core should set `action_id` to `none` and answer from `state_queries.room_light`; `available=false` or `stale=true` means the current sensor state cannot be confirmed.

When the v1 gate is enabled, the loopback/auth policy used by `/turn` also
protects `POST /feedback/closed-loop`. The caller sends one bounded candidate
without `schema_version`, `event_id`, `observed_at`, or `source_authority`.
Thought Core derives source authority from the fixed route matrix, issues the
canonical identity and timestamp, validates the exact event-kind, channel,
component, and transition-profile tuple, durably appends the redacted entry,
and updates the replay-derived projection. This route accepts only display/TTS
`output.dispatch_intent` and `output.feedback`; it rejects playback,
`operation.transition`, visible/user-observation success, and every non-matrix
tuple before Journal append. Success returns only:

```json
{
  "ok": true,
  "event_id": "evt_opaque",
  "journal_entry_id": "jrn_opaque",
  "ingest_offset": 1
}
```

The general internal v1 contract defines exactly `operation.transition`,
`output.dispatch_intent`, and `output.feedback`, while the Control HTTP route
accepts only the latter two under its narrower matrix. An output adapter must
obtain the successful durable response for `output.dispatch_intent` before its
external HTTP send. At the worker boundary immediately before `urlopen`, it
must also durably append the Control-authored
`send_attempt_started_outcome_unknown` transition. This intentionally records
`may_have_submitted / outcome_unknown` before the uncertain crash window. A
failure to append blocks `urlopen`; replay retains the unknown state without
resend, and only a later result callback may refine it to submission
acknowledgement or terminal ambiguity. General post-turn v0 telemetry remains
best-effort and nonfatal; this stricter barrier applies only to an external
output/action dispatch intent.

The provider-facing `agentic-predecision-context.v1` orders current correction,
Environment, `active_operations`, `feedback_context`, same-session continuity,
relevant Memory, and capability view. The projection is snapshotted before the
provider call, so feedback arriving from turn N is visible starting with a
later decision and never rewrites turn N. Replay reads only validated v1
entries, invokes the same pure reducer as live ingest, and performs no output,
tool, provider, browser, device, or Memory action.

Canonical v1 validation applies one fixed secret-like string matcher to every
envelope and detail string before Journal append. This includes `session_id`,
message/event correlation references, and component values. Matching values
are rejected unchanged rather than redacted or stored; otherwise identifiers
remain byte-for-byte intact.

## OpenAI Broker (phase-one bounded contract)

| Item | Contract |
|---|---|
| Bind | Literal `127.0.0.1` only |
| Allowed ports | `18786` standard or `18886` isolated; `18888` is never a broker port |
| Local API | Exact `POST /v1/chat/completions`; bounded `GET /health` |
| Upstream | Fixed `https://api.openai.com/v1/chat/completions`; no caller-selected URL, model, proxy, redirect, or parameters |
| Model | Fixed `gpt-4o-mini` for the first connectivity proof |
| Input | Exactly system and user messages, `temperature: 0`, `response_format: {"type":"json_object"}`, and `max_tokens` exactly `720` or `240` |
| Bounds | Inbound <=32 KiB; system <=12 KiB; user <=16 KiB; upstream <=64 KiB; timeout <=12 seconds; concurrency one; queue zero; retry zero |
| HTTP admission | One synchronous handler with one queued connection; incremental bounded one-raw-read `read1` chunks under a monotonic total body deadline <=12 seconds; reject `Transfer-Encoding`, duplicate/ambiguous `Content-Length`, and incomplete bodies |

The broker alone owns source class `thought-core-existing-env-v1`: it resolves
the existing ignored `services/thought-core/.env` file and accepts exactly one
non-empty `OPENAI_API_KEY`. The key never enters Thought Core, Launcher, a child
environment, command arguments, process state, events, logs, Git, or a copied
runtime secret. Its sole permitted use is the `Authorization: Bearer` header on
the fixed upstream request. The broker retains no raw request, response, or
header and reconstructs only `choices[0].message.content` in a compatible
response envelope.

Phase 1 proof is limited to source/static inspection and deterministic
fake-transport tests only. It does not prove real key discovery, external
OpenAI reachability, Thought Core or Launcher integration, runtime, readiness,
or product completion. After review/adoption and separate runtime authorization,
the first live request on a new or materially changed route is exactly one
synthetic, non-private, conversation-only, single-attempt smoke before any real
Thought Core, operator, repository, or private payload. That smoke uses mock
tools with action/tool/device execution zero, followed by the owned Stop,
cleanup, and residue verification.

## AITuberKit

| Item | Contract |
|---|---|
| Projection URL | `http://127.0.0.1:3000/projection-visual` |
| Speech queue API | `POST /api/messages?clientId=<id>&type=direct_send` |
| Env from sword side | `AITUBER_MESSAGE_URL` |
| Message Receiver | Enable in AITuberKit with matching client ID |

Thought Core streaming responses are sent to AITuberKit through `direct_send`. AITuberKit external WebSocket mode is not part of the standard integration path. Canonical output payloads carry `assistant_message_id`; the legacy `message_id` field carries the same value and must not be replaced by the enclosing Thought Core `event_id`. With closed-loop v1 enabled, a successful HTTP response records only transport `submission_ack / needs_feedback`, not visible pixels or user observation.

## TTS Service

| Item | Contract |
|---|---|
| HTTP source | `http://127.0.0.1:8765` |
| Single request | `POST /api/tts` |
| Streaming chunk | `POST /api/tts/chunk` |
| Health | `GET /health` |
| Shutdown | `POST /shutdown` |
| Volume | `GET/POST /api/volume` |
| Status file | `latest_tts_state.json` under output status dir |

Thought Core watcher sends response chunks to `TTS_HTTP_CHUNK_URL` when TTS is enabled. Canonical chunks carry `assistant_message_id`, with `message_id` as the same-value compatibility alias. The current adapter has no authoritative playback receipt source: HTTP completion is only `submission_ack / needs_feedback`; an ambiguous send is `may_have_submitted / outcome_unknown`, and no automatic retry occurs.

## Home Assistant Bridge

| Item | Contract |
|---|---|
| Health | `http://127.0.0.1:8787/health` |
| Action list | `GET /actions` |
| Preview | `POST /actions/{action_id}/preview` |
| Execute | `POST /actions/{action_id}/execute` |
| Auth | `HOME_CONTROL_API_TOKEN` |

Home Assistant bridge owns action safety, action tracking, and Home Assistant execution result interpretation.

## TouchDesigner

| Item | Contract |
|---|---|
| UDP trigger | `127.0.0.1:9001` |
| Payload | JSON with source, phase, action/result metadata |
| Display input | Projection Visual or Cube background URL |

TouchDesigner owns visual effect state. It does not decide Thought Core or Home Assistant state.

## Runtime Status

Long running services write runtime status files when launched by integration scripts. These files are for process management and status display, not authority for business state.
The stack state directory is the compatibility root for `pids.json`, launcher
state, per-module status directories, feedback JSONL, and service logs.

## Security

- Tokens and API keys are environment variables.
- URLs with embedded credentials are rejected.
- Loopback is the default bind policy.
- Remote use requires explicit opt-in and token/origin controls.
- Runtime logs, cache files, generated audio, and provider payloads may contain local-sensitive data.
