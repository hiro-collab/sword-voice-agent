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
| room light state/confidence | `vision-snapshot-processor` | Vision Snapshot Processor topic | Environment State Server は cache、stale 判定、Dify 向け `state_queries.room_light` projection だけを行う |
| room light state query | `environment-state-server` | `/environment/current` | `authority=vision_snapshot_processor` を明示し、Dify は Home Assistant の実スイッチ状態と混ぜない |
| room light user feedback | user via Dify | `POST /feedback/state-query`, feedback JSONL | `authority=user_feedback` の学習用ラベル。`idempotency_key` と stale warning を持ち、即時に vision authority を上書きしない |
| Camera Hub topic freshness | Camera Hub publisher | Environment State Server snapshot | 古い topic は stale として扱う |
| payload accept/reject | sword-voice-agent receiver | response, log | auth、JSON、protocol validation |
| `GateDecision.raw_active` | `GestureInputGate` | receiver response, status projection | 入力信号と閾値から判定 |
| `GateDecision.mic_enabled` | `GestureInputGate` | ai-talk-core input gate payload | activation/release delay を含む意図 |
| `GateDecision.reason` | `GestureInputGate` | UI, logs, status projection | gate の説明 |
| `VoiceControlCommand.action` | `VoiceTurnController` | receiver response, status projection | `start_recording`, `stop_recording`, `none` |
| `VoiceControlCommand.turn_id` | `VoiceTurnController` | receiver response, `latest_voice_turn.json` | Dify へ自動送信しない |
| actual browser recording state | `ai-talk-core` | ai-talk-core Web UI / API | sword-voice-agent は開始/停止意図を送るだけ |
| transcript / command | `ai-talk-core` | handoff files | STT 結果と Dify へ送る既定 field |
| Dify request text selection | Dify watcher | Dify API request | `command`, `transcript`, `prompt` の選択 |
| Dify answer and IDs | Dify | output files, status projection | `answer`, `conversation_id`, `message_id`, `usage` |
| Home Assistant action result | `home-assistant-server` / Home Assistant | bridge API, Environment State Server | 家電状態の根拠 |
| Environment snapshots | `environment-state-server` | `/environment/current`, `/indicators/current` | 複数モジュール状態の cache |
| TTS playback state | `tts-service` | `latest_tts_state.json`, HTTP health | 読み上げ状態 |
| AITuberKit speech queue | AITuberKit | `/api/messages` | 発話キューと表示 |
| TouchDesigner visual trigger | TouchDesigner runtime | UDP 9001 | 視覚演出状態 |
| projection files and event log | `StatusStore` | `.cache/sword_voice_agent` | 表示・デバッグ用 |

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

`latest_voice_turn.json` は厳密な handoff correlation ではありません。Dify 連携との相関は時刻と直近 projection による緩い紐づけです。

## Conflict Resolution

| Conflict | Prefer | Reason |
|---|---|---|
| Camera Hub topic と HUD 表示が違う | Camera Hub topic | HUD は projection で遅延や欠落がありうる |
| local `mic_enabled` と ai-talk-core UI が違う | ai-talk-core for actual recording, local gate for intended command | local は意図、ai-talk-core は実録音状態 |
| Environment snapshot と直接 module API が違う | 直接 module API | Environment は cache |
| Dify response file と event log が違う | Dify response file | event log は履歴用に redacted される |
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
