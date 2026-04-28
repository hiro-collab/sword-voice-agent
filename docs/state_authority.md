# State Authority

このドキュメントは、各ステージのフラグ、ステート、IDについて「誰が決めるのか」を整理するためのものです。

ここでの authority は、その値の意味を決めてよい唯一の責任者を指します。ほかの層は、値を検証、転送、表示、保存できますが、意味を勝手に変更しません。

## 原則

- `core` は判定の意味を決める。
- `application` はユースケースとして値をつなぐ。
- `adapters` は外部 I/O と形式変換だけを担当する。
- `StatusStore` と console は projection であり、制御の authority ではない。
- 外部モジュール由来の値は、このリポジトリでは上書きせず、検証して受け取る。
- edge command は状態ではなくイベントとして扱う。

## Authority Matrix

| Stage | Flag / State | Authority | 主な保存/転送先 | 備考 |
|---|---|---|---|---|
| Gesture detector | `GestureState.gestures.sword_sign.active` | `mediapipe-sword-sign` | UDP/HTTP payload | このリポジトリでは bool 型検証だけ行う |
| Gesture detector | `GestureState.gestures.sword_sign.confidence` | `mediapipe-sword-sign` | UDP/HTTP payload | `0.0 <= confidence <= 1.0` の有限数だけ許可 |
| Receiver | payload accept/reject | HTTP/UDP receiver | response / log | auth、JSON、protocol validation の責任を持つ |
| Input gate | `GateDecision.raw_active` | `GestureInputGate` | receiver response / status store | 入力信号と閾値を見た判定結果 |
| Input gate | `GateDecision.mic_enabled` | `GestureInputGate` | `VoiceState` / input gate payload | activation/release delay を含めた最終ゲート判定 |
| Input gate | `GateDecision.reason` | `GestureInputGate` | UI / logs / status store | `waiting_for_activation_delay`, `stable` など |
| Voice state | `VoiceState.phase` | `GestureInputGate` 由来の local pipeline | receiver response / status store | 現状は local gate 由来の簡易フェーズ |
| Voice state | `VoiceState.mic_enabled` | `GestureInputGate` | ai_talk_core input gate payload | ai_talk_core に送る意図としての mic gate |
| Voice turn | `VoiceControlCommand.action` | `VoiceTurnController` | receiver response / status store | `start_recording`, `stop_recording`, `none` |
| Voice turn | `VoiceControlCommand.turn_id` | `VoiceTurnController` | receiver response / `latest_voice_turn.json` | local voice turn のID。Difyには自動送信しない |
| ai_talk_core input gate | actual browser recording state | `ai_talk_core` | ai_talk_core Web UI / API | このリポジトリは開始/停止意図を送るだけ |
| STT / handoff | `transcript` | `ai_talk_core` | `.cache/codex/*_latest.json` | 音声認識結果の authority は ai_talk_core |
| STT / handoff | `command` | `ai_talk_core` | `.cache/codex/*_latest.json` | Difyへ送る既定 field は `command` |
| Dify request | `AgentRequest.text` | `watch_handoff_to_dify` / manual sender | Dify API request | `--field` で `command`, `transcript`, `prompt` を選ぶ |
| Dify request | transcript context inclusion | sender CLI option | Dify `inputs` | `--include-transcript-context` 指定時だけ送る |
| Dify response | `answer`, `conversation_id`, `message_id`, `usage` | Dify | output json/text / status store | Dify応答の authority は Dify |
| Conversation continuity | persisted conversation id file | watcher policy | `.cache/codex/*_conversation_id.txt` | Difyが発行したIDを次回使うための local selection |
| Status projection | `latest_gesture.json` | `StatusStore` projection | `.cache/sword_voice_agent` | 表示・デバッグ用。制御の authority ではない |
| Status projection | `latest_voice_turn.json` | `StatusStore` projection | `.cache/sword_voice_agent` | Dify結果との緩い相関に使う |
| Status projection | `latest_dify_response.json` | `StatusStore` projection | `.cache/sword_voice_agent` | console表示用。本文を含むためコミット禁止 |
| Event log | `events.jsonl` | `StatusStore` projection | `.cache/sword_voice_agent` | 履歴用。Dify本文などは redacted |
| Console | displayed status | console status builder | browser UI | 表示専用。ここから制御状態を決めない |

## Turn Lifecycle

`turn_id` の authority は `VoiceTurnController` です。

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

`StatusStore` は `stop_recording` の `turn_id` を `latest_voice_turn.json` に残します。これにより、直後にDify watcherが処理した結果へ、最新ターンIDを緩く付与できます。ただし、これは厳密な handoff correlation ではありません。

## Data Flow

```text
mediapipe-sword-sign
  owns: sword_sign active/confidence
  sends: GestureState

sword-voice-agent receiver
  owns: payload validation, auth decision
  passes: GestureState

GestureInputGate
  owns: mic_enabled, gate reason, voice state basis
  emits: VoiceState

VoiceTurnController
  owns: start/stop edge and turn_id
  emits: VoiceControlCommand

ai_talk_core
  owns: actual browser recording, STT, transcript, command
  writes: handoff files

watch_handoff_to_dify
  owns: which handoff field becomes AgentRequest.text
  sends: Dify request

Dify
  owns: answer, conversation_id, message_id, usage

StatusStore / console
  owns: projection files and display
  does not own: control decisions
```

## Conflict Resolution

| Conflict | 優先する authority | 理由 |
|---|---|---|
| `GestureState.active` と console 表示が違う | `GestureState` | console は projection なので遅延や欠落がありうる |
| local `mic_enabled` と ai_talk_core UI が違う | ai_talk_core for actual recording, local gate for intended command | local は意図、ai_talk_core は実際の録音状態 |
| `latest_voice_turn.json` と新しい receiver response が違う | receiver response | latest file は過去の projection |
| Dify response file と `events.jsonl` が違う | Dify response file | event は履歴用に redacted される |
| conversation id file と Dify最新応答が違う | Dify最新応答 | ファイルは次回継続用の local cache |

## Adding New Flags

新しい flag や state を追加する時は、先に以下を決めます。

1. その値の authority はどの module か。
2. その値は state か、edge command か、projection か。
3. `protocol` に載せるか、adapter固有 payload に留めるか。
4. `StatusStore` に保存する場合、本文や secret を含まないか。
5. GUIに出す場合、制御可能にするのか、表示だけにするのか。
6. 既存 consumer が未知フィールドを無視できるか。

authority が決まらない値は、実装しないか、まず `StatusStore` の projection として扱います。
