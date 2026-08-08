# State Authority

この文書は、state、flag、ID の意味をどの module が決めるかを定義します。ほかの層は値を検証、転送、保存、表示できますが、意味を勝手に変更しません。

## Principles

- 外部モジュール由来の値は、このリポジトリでは上書きしない。
- `StatusStore`、console、HUD は projection であり、制御の authority ではない。
- edge command は状態ではなくイベントとして扱う。
- authority が決まらない値は、実装しないか projection として扱う。
- 長期記憶は `memory-core` が commit authority を持つ。`thought-core` や
  `deep-core` は candidate を出せるが、M4 を直接確定しない。
- config/policy/secrets は learned memory ではない。M4 と混ぜない。

## Authority Matrix

| Value | Authority | Transport / Storage | Notes |
|---|---|---|---|
| sword sign active/confidence | `mediapipe-sword-sign` | Camera Hub topic, legacy UDP/HTTP payload | このリポジトリでは型と範囲を検証する |
| room light state/confidence | `vision-snapshot-processor` | Vision Snapshot Processor topic | Environment State Server は cache、stale 判定、Thought Core 向け `state_queries.room_light` projection だけを行う |
| room light state query | `environment-state-server` | `/environment/current` | `authority=vision_snapshot_processor` を明示し、Thought Core は Home Assistant の実スイッチ状態と混ぜない |
| room light user feedback | user via Thought Core | `POST /feedback/state-query`, feedback JSONL | `authority=user_feedback` の学習用ラベル。`idempotency_key` と stale warning を持ち、即時に vision authority を上書きしない |
| Camera Hub topic freshness | Camera Hub publisher | Environment State Server snapshot | 古い topic は stale として扱う |
| payload accept/reject | sword-voice-agent receiver | response, log | auth、JSON、protocol validation |
| `GateDecision.raw_active` | `GestureInputGate` | receiver response, status projection | 入力信号と閾値から判定 |
| `GateDecision.mic_enabled` | `GestureInputGate` | ai-talk-core input gate payload | activation/release delay を含む意図 |
| `GateDecision.reason` | `GestureInputGate` | UI, logs, status projection | gate の説明 |
| `VoiceControlCommand.action` | `VoiceTurnController` | receiver response, status projection | `start_recording`, `stop_recording`, `none` |
| `VoiceControlCommand.turn_id` | `VoiceTurnController` | receiver response, `latest_voice_turn.json` | Thought Core handoff と緩く相関する |
| actual browser recording state | `ai-talk-core` | ai-talk-core Web UI / API | sword-voice-agent は開始/停止意図を送るだけ |
| transcript / command | `ai-talk-core` | handoff files | STT 結果と Thought Core へ送る既定 field |
| Thought Core request text selection | Thought Core watcher | Thought Core API request | `command`, `transcript`, `prompt` の選択 |
| Thought Core answer and turn metadata | Thought Core | status projection | `answer`, `turn_id`, `event_count` |
| closed-loop `assistant_message_id` | Thought Core | turn events, output payloads, Event Journal v1 | `event_id` とは別 identity。`message_id` は同じ値を運ぶ legacy alias としてのみ残す |
| closed-loop `event_id` | Thought Core | `closed-loop-correlation-feedback.v1` event | output adapter は発行せず、`POST /feedback/closed-loop` で Thought Core が発行する |
| reserved agentic decision `event_id` | Thought Core `EventFactory` | process-local reservation, then the existing Thought event envelope | reservation does not consume `seq`; only one same-factory `emit_reserved` consumes it. Abandoned IDs create no event or sequence gap |
| same-call provider attempt facts | OpenAI Broker | ephemeral `sword_provider_attempt_receipt` in the local broker response | exactly one successful canonical decision call owns attempt1/retry0/fallback0; the process request-budget counter is not evidence and the internal correlation header never leaves loopback |
| provider authorship terminal join | Thought Core | `turn.completed.data.provider_attempt_evidence` | joins the broker receipt to the emitted decision and assistant IDs for one non-capability turn; it is not raw-output byte identity, browser visibility, durable Memory, or live-provider proof |
| `journal_entry_id` / `ingest_offset` | Thought Core Event Journal | append-only local JSONL | durable append order。raw text、media、secret、provider payload、private path は保存しない |
| active operation / recent output feedback projection | Operation/Output Projection | process-local derived state | Event Journal v1 から同じ reducer で live/replay 生成し、削除・再構築可能。外部 state authority や durable memory ではない |
| semantic intent / capability selection / structured action proposal | Thought Core AI agent | turn events and validated proposal boundary | conversation, capability schemas, Environment State, memory, and optional Self Mirror are reasoning inputs; this is not execution permission |
| action permission and parameter bounds | deterministic validator / policy boundary | validation event and execution request | schema, allowlist, range, confirmation, and safety policy may accept or reject an AI proposal but must not replace ordinary semantic intent with a fixed phrase table |
| Home Assistant action result | `home-assistant-server` / Home Assistant | bridge API, Environment State Server | 家電状態の根拠 |
| Environment snapshots | `environment-state-server` | `/environment/current`, `/indicators/current` | 複数モジュール状態の cache |
| TTS playback state | `tts-service` | `latest_tts_state.json`, HTTP health | 読み上げ状態 |
| AITuberKit speech queue | AITuberKit | `/api/messages` | 発話キューと表示 |
| TouchDesigner visual trigger | TouchDesigner runtime | UDP 9001 | 視覚演出状態 |
| projection files and event log | `StatusStore` | `.cache/sword_voice_agent` | 表示・デバッグ用 |

## Closed-loop correlation and feedback v1

`contracts/turn/closed-loop-correlation-feedback.v1.json` is the single
machine-readable authority for issuer rules, event-kind names, transition
profiles, proof enums, redaction allowlists, and provider bounds. The feature
starts disabled and is enabled only for a fresh session with
`THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED=1`.

For the ordinary AIT browser route, Thought Core remains the sole issuer of
`session_id`, `turn_id`, `assistant_message_id`, and feedback `event_id`.
The browser preserves those identifiers through message-store and TTS
synthesis acknowledgement; it does not mint replacement correlation IDs.
Message-store acknowledgement proves bounded display-state acceptance only,
and TTS acknowledgement proves non-empty synthesis acceptance only. Visible
pixels, audible playback, and user observation remain separate proof layers.

The canonical shared identity vocabulary is limited to `session_id`,
`input_attempt_id`, `turn_id`, `operation_id`, `assistant_message_id`,
`event_id`, `candidate_id`, and `memory_id`, plus the ordering and causal refs
listed in that contract. There is no v1 `feedback_id`, `observation_id`,
`confirmation_id`, retry ID, cleanup ID, generic tag map, or second durable
store. Existing caller-supplied `turn_id` remains a labeled compatibility path
until the separately owned input edge can accept Thought-Core-issued IDs.

Event Journal is redacted operational evidence, not current Environment,
display, playback, physical, or user-observation authority and not Memory Core.
The Control HTTP output route does not accept caller-authored
`source_authority`: Thought Core derives `control_output_adapter` for dispatch
intent and the display/TTS transport authority for feedback. Its fixed ingress
matrix rejects playback, operation transitions, and success claims; those
remain available only to future internal producers with their own authority.
Before append, Event Journal must already contain the exact Thought-Core-issued
assistant tuple. The same Journal enforces one bounded, non-forking transition
chain per assistant message/channel, so caller-selected tuple swaps, unknown
parents, replay, duplicates, and event-volume multiplication cannot enter
Current View or later predecision context.
Immediately before the real output `urlopen`, the watcher durably records a
distinct Control-authored send-attempt transition as
`may_have_submitted / outcome_unknown`. It is provider-visible after replay and
is never an automatic resend instruction. If that append fails, the network
send is blocked; a later transport callback may refine the same correlation.
HTTP completion from the Control output adapter proves at most
`submission_ack`; it remains `needs_feedback`. A timeout or other ambiguous
send becomes `may_have_submitted` plus `outcome_unknown`, with retry zero.
AITuberKit remains authoritative for its queue/display handling and TTS Service
remains authoritative for playback state. Deterministic fake success used in
tests does not upgrade those runtime proof layers.

Every v1 envelope/detail string crosses the same fixed secret-like matcher
before the Journal boundary. Secret-like caller identifiers or detail values
are rejected without mutation, append, projection, replay, or provider-context
change. Accepted identifiers retain their exact value.

The provider-attempt terminal join is an ephemeral turn-event contract. The
current Event Journal summary does not persist its nested receipt value, so it
must not be described as a durable provider receipt. A browser/API consumer may
validate and project the fixed join for the active request, but it may not mint
replacement IDs or upgrade source/fake-transport evidence to live authorship.

## Launcher Demo-Safe Settings

`demo_safe_settings.v0` is the Launcher-owned operator settings projection for
bounded local demos. The tracked defaults live in the workspace repo at
`manifests/demo-safe-settings/defaults.json`. Fresh clones must treat those
tracked defaults as candidate metadata only and must start with every candidate
`enabled=false`.

Local operator overrides are stored by the Launcher in the existing gitignored
stack state directory as `demo-safe-settings.json`. That local file is the
authority for the operator's current demo-safe choices on that machine. It may
store only normalized setting fields such as `enabled`, `restore_required`,
`max_action_count`, and `max_duration_sec`; it must not store secrets, raw Home
Assistant values, raw transcripts, media, screenshots, provider payloads, or
private paths.

Tracked defaults may also expose read-only command-stimulus metadata such as
`action_ids`, `feedback_stimulus_class`, `state_requirement_class`,
`timing_estimate_sec`, and `measurement_required`. These fields help later
routes plan all-appliance feedback-loop demos and timing estimates, but they are
not local operator overrides and do not authorize command execution by
themselves.

`demo_readiness_status.v0` is a read-only Launcher status projection derived
from local readiness/status checks. It carries classes such as `status_class`,
`source_class`, `proof_ceiling`, `does_not_prove`, and `last_checked_class`.
It is not an editable setting and is not the authority for actual audio
playback, browser-visible avatar motion, Projection Visual / Self Mirror
success, Home Assistant state, external observation, or physical device state.

Settings and readiness are not command authorization. They do not submit Home
Assistant or Home Control commands, do not execute proof routes, do not publish
raw/private values, and do not create release/readiness/final-pass authority.
Any demo action still needs the explicit route gate that owns command
submission, restore/off behavior, cleanup, and proof wording.
For command-stimulus rows, unknown current state is a proof limitation to feed
back into the loop unless the route specifically requires current-state proof;
it is not automatically a reason to skip command submission.

## Launcher Diagnostic And Timing Projections

Launcher diagnostic and timing endpoints are projections for reviewed routes.
They can summarize local classes, counts, route metadata, and timing buckets,
but they do not own the underlying proof.

| Projection | Authority for meaning | Boundary |
|---|---|---|
| `launcher_startup_timing.v0` | Launcher process supervisor and route-local checks | Timing summary only; not Chrome cold-start proof or startup-speed pass. |
| `audio_awareness_summary_only` | Audio/STT/TTS owning services | Awareness summary only; not microphone capture, system-audio capture, user-heard audio, or exact TTS output proof. |
| `self_mirror_metric_summary_only` | Self Mirror diagnostic route | Temporal metric summary only; not VRM telemetry, browser-visible avatar-motion proof, screenshot proof, or camera proof. |
| `projection_visual_display_tts_summary_only` | Projection Visual and local speech/TTS surfaces | Display/TTS summary only; not exact same-text parity or user-heard audio. |
| `projection_visual_receiver_binding_summary_only` | Projection Visual receiver-binding diagnostics | Response-binding discoverability only; not live bubble-render proof or receiver-runtime pass. |
| `os_display_diagnostic_summary_only` | OS display/window diagnostic route | Prompt/window summary only; not full-desktop capture or raw screenshot publication. |
| `demo_timed_action_readiness.v0` | Launcher route summary plus reviewed metadata | Read-only/non-command readiness only; not Home Control execute, `/actions` catalog proof, CheckTracking, CheckState, HA-visible state, or physical proof. |

Any route that needs live browser, capture, Home Control operation, raw
artifact retention, or proof upgrade must be separately scoped with exact
surfaces, action IDs, counts or duration bounds, cleanup, and post-result
review.

## Memory And Policy Authority

| Area | Authority | Current / target storage | Rule |
|---|---|---|---|
| M0 raw signal | Source module such as Camera Hub, STT, or vision processor | in-memory buffers | Do not journal or send to thought-core unless summarized. |
| M1 module state | Each owning service | `.cache/...`, future `runtime/state/` | A service writes only its own state. Other services observe or project it. |
| M2 core working memory | The owning core | in-process, optional checkpoint | Expires with the turn/task unless explicitly summarized. |
| M3 event journal | Appending service; schema owned by contracts | `.cache/.../*.jsonl`, future `runtime/logs/events/` | Append-only facts; rotate or summarize, do not treat as learned memory. |
| M4 semantic/episodic memory | `memory-core` | future `local/memory/` | Writes go through candidate -> policy -> optional confirmation -> commit. |
| M5 config/policy | Human/ops-controlled config and policy files | `.env.example`, future `local/config/`, `policies/` | AI may propose changes; direct edits require explicit implementation/review. |
| M6 secrets | Ops / OS secret store / local env | `.env`, OS secret store, local-only files | Never log, never commit, never expose to memory-core or thought-core. |

## Capability Rules

- `reflex-core` may emit reflex events and write its own state, but must not
  read long-term memory or execute home actions.
- `thought-core` may call `environment.observe`, `home.preview`,
  approved `home.execute`, and `memory.write_candidate`; it must not call
  `memory.commit` or read secrets.
- `deep-core` may research and write memory candidates, but must not execute
  home actions directly.
- `memory-core` may commit M4 memory under policy, but must not handle M6
  secrets or home actions.
- `expression-core` may read selected preference memory, but must not commit
  memory or decide action success.

## Turn Lifecycle

```text
mic_enabled false
  -> no turn_id

mic_enabled true edge
  -> action = start_recording
  -> new turn_id

mic_enabled true stable
  -> action = none
  -> same turn_id

mic_enabled false edge
  -> action = stop_recording
  -> same turn_id
  -> controller clears current turn_id after emitting the command

mic_enabled false stable
  -> action = none
  -> no turn_id
```

`latest_voice_turn.json` は厳密な handoff correlation ではありません。Thought Core 連携との相関は時刻と直近 projection による緩い紐づけです。

## Conflict Resolution

| Conflict | Prefer | Reason |
|---|---|---|
| Camera Hub topic と HUD 表示が違う | Camera Hub topic | HUD は projection で遅延や欠落がありうる |
| local `mic_enabled` と ai-talk-core UI が違う | ai-talk-core for actual recording, local gate for intended command | local は意図、ai-talk-core は実録音状態 |
| Environment snapshot と直接 module API が違う | 直接 module API | Environment は cache |
| Thought Core response file と event log が違う | Thought Core response file | event log は履歴用に redacted される |
| TTS status と AITuberKit 表示が違う | `tts-service` for playback, AITuberKit for avatar speech queue | 責務が違う |

## Adding Values

新しい flag、state、ID を追加する時は、先に以下を決めます。

1. authority を持つ module。
2. state、edge command、projection のどれか。
3. protocol に載せるか、adapter 固有 payload に留めるか。
4. secret、本文、個人パスを含まないか。
5. GUI で制御可能にするのか、表示だけにするのか。
6. 既存 consumer が未知フィールドを無視できるか。
7. どの M0-M6 layer に属し、どの capability が読める/書けるか。
