# State Authority

この文書は、state、flag、ID の意味をどの module が決めるかを定義します。ほかの層は値を検証、転送、保存、表示できますが、意味を勝手に変更しません。

## Principles

- 外部モジュール由来の値は、このリポジトリでは上書きしない。
- `StatusStore`、console、HUD は projection であり、制御の authority ではない。
- edge command は状態ではなくイベントとして扱う。
- authority が決まらない値は、実装しないか projection として扱う。

## Authority Matrix

| Value | Authority | Transport / Storage | Notes |
|---|---|---|---|
| sword sign active/confidence | `mediapipe-sword-sign` | Camera Hub topic, legacy UDP/HTTP payload | このリポジトリでは型と範囲を検証する |
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
