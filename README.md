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
- `GestureState` をHTTP POSTで受け取るreceiver
- `VoiceState` を `ai_talk_core` の input gate payload へ変換するadapter
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

HTTP receiver:

```powershell
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server --host 127.0.0.1 --port 8787
```

`ai_talk_core` 側にinput gate endpointを用意した後は、receiverから転送できます。

```powershell
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server `
  --host 127.0.0.1 `
  --port 8787 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate
```

別ターミナルから:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8787/gesture-state `
  -ContentType "application/json" `
  -Body '{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}'
```

`ai_talk_core` へ渡すinput gate payloadの形:

```json
{
  "type": "input_gate_state",
  "input_enabled": true,
  "mic_enabled": true,
  "reason": "activation_delay_passed",
  "source": "sword_voice_agent",
  "timestamp": 0.4
}
```

## 次の実装

1. `mediapipe-sword-sign` 側から `GestureState` JSONをHTTP POSTするadapterを追加する。
2. `ai_talk_core` 側に上記payloadを受け取るWeb/API endpointを追加する。
3. 統合アプリで `GestureState -> GestureInputGate -> voice capture -> Dify` を配線する。
4. Web UIに刀印検出、MIC、処理状態のインジケータを表示する。
