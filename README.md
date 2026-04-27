# sword-voice-agent

刀印ジェスチャーを検出している間だけ音声入力を受け付ける、ジェスチャー制御型AI音声エージェントです。

このリポジトリは統合アプリの土台です。`mediapipe-sword-sign` と `ai_talk_core` を直接混ぜ込まず、共通protocolとadapterで接続します。

## 何を作るか

```text
Camera
  -> gesture module
  -> GestureState
  -> input gate
  -> voice module / STT
  -> Dify
  -> response display / TTS
```

最初のMVPは次の流れです。

```text
刀印を0.3秒以上検出
  -> MIC ON
刀印が0.5秒以上消失
  -> MIC OFF
  -> 録音終了
  -> STT
  -> Difyへ送信
```

## 設計方針

Ports and Adapters 型で構成します。

- core: 状態判定や制御ロジック。WebSocket、Dify、MediaPipeを知らない。
- protocol: モジュール間で受け渡すJSON形式。
- adapters: Dify API、WebSocket、既存モジュール接続などの具体I/O。
- apps: 各部品を組み合わせる実行アプリ。

## 現在入っているもの

- `GestureState`, `VoiceState`, `AgentRequest`, `AgentResponse`
- 刀印の揺れを吸収する `GestureInputGate`
- Dify Chat App API用の最小クライアント
- JSON Linesでinput gateを試せるCLI

## 開発

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

CLIデモ:

```powershell
python -m sword_voice_agent.apps.gate_simulator --demo
```

標準入力から `GestureState` JSON Lines を流すこともできます。

```powershell
'{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}' | python -m sword_voice_agent.apps.gate_simulator
```

## 次の実装

1. `mediapipe-sword-sign` 側から `GestureState` JSONを配信するadapterを追加する。
2. `ai_talk_core` 側に `mic_enabled` を外部から渡すadapterを追加する。
3. 統合アプリで `GestureState -> GestureInputGate -> voice capture -> Dify` を配線する。
4. Web UIに刀印検出、MIC、処理状態のインジケータを表示する。
