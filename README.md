# sword-voice-agent

刀印ジェスチャーを検出している間だけ音声入力を受け付ける、ジェスチャー制御型AI音声エージェントです。

このリポジトリは統合アプリの土台です。`mediapipe-sword-sign` と `ai_talk_core` を直接混ぜ込まず、共通protocolとadapterで接続します。

## 何を作るか

```text
Camera
  -> gesture module
  -> GestureState
  -> input gate
  -> ai_talk_core input gate
  -> browser recording / STT
  -> response display / handoff
```

最初のMVPは次の流れです。

```text
刀印を0.3秒以上検出
  -> MIC ON
刀印が0.5秒以上消失
  -> MIC OFF
  -> 録音終了
  -> STT
  -> handoff生成
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
- `mediapipe-sword-sign` のUDP publisherから `GestureState` を受け取るreceiver
- `VoiceState` を `ai_talk_core` の input gate payload へ変換するadapter
- `VoiceState` のON/OFFエッジから `start_recording` / `stop_recording` を作るturn controller
- Dify Chat App API用の最小クライアント
- JSON Linesでinput gateを試せるCLI

## リポジトリ配置

このREADMEの `sword_voice_agent` を実行するコマンドは、次の内側ディレクトリから実行します。

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
```

外側の `C:\Users\kawai\dev\works\sword-voice-agent` には `src` がないため、そこで `PYTHONPATH=src` を指定しても `No module named 'sword_voice_agent'` になります。

関連モジュールは別リポジトリです。

```text
C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent  # 統合アプリ
C:\Users\kawai\dev\works\ai_talk_core\ai_talk_core            # 音声/STT/Web UI
C:\Users\kawai\dev\works\mediapipe_test                       # 刀印検出
```

## 開発

実行場所: `C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent`

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

CLIデモ:

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gate_simulator --demo
```

標準入力から `GestureState` JSON Lines を流すこともできます。

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
'{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}' | python -m sword_voice_agent.apps.gate_simulator
```

HTTP receiver:

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server --host 127.0.0.1 --port 8787
```

`ai_talk_core` 側にinput gate endpointを用意した後は、receiverから転送できます。

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server `
  --host 127.0.0.1 `
  --port 8787 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate
```

別ターミナルから:

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8787/gesture-state `
  -ContentType "application/json" `
  -Body '{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}'
```

UDP receiver:

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver `
  --host 127.0.0.1 `
  --port 8765 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate `
  --debug `
  --debug-every 30
```

`mediapipe-sword-sign` 側から送る場合:

```powershell
cd C:\Users\kawai\dev\works\mediapipe_test
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
```

`mediapipe-sword-sign` のカメラ/手検出/信頼度を画面でも確認したい場合は、送信側に `--preview` を追加します。

```powershell
cd C:\Users\kawai\dev\works\mediapipe_test
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30 --preview
```

protobuf の非推奨warningが通常ログに混ざって見づらい場合は、送信側に `--suppress-protobuf-warnings` を追加します。

## ローカル統合手順

1. `ai_talk_core` のWeb UIを起動する。

```powershell
cd C:\Users\kawai\dev\works\ai_talk_core\ai_talk_core
uv run python -m src.web.app
```

2. ブラウザで `http://127.0.0.1:8000` を開き、ブラウザ録音の `入力ゲートで録音を制御する` を有効にする。

3. `sword-voice-agent` のUDP receiverを起動する。

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver `
  --host 127.0.0.1 `
  --port 8765 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate `
  --debug `
  --debug-every 30
```

4. `mediapipe-sword-sign` からUDPで `GestureState` を送る。

```powershell
cd C:\Users\kawai\dev\works\mediapipe_test
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
```

この状態で刀印が安定検出されると、`ai_talk_core` のinput gateがenabledになり、Web UI側のブラウザ録音が開始します。刀印を解除するとinput gateがdisabledになり、録音停止とアップロード処理に進みます。

5. `ai_talk_core` のWeb UIで `handoff payload を保存する` を有効にしておく。

6. 保存されたhandoffをDifyへ送る。

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
$env:AI_TALK_CORE_ROOT = "C:\Users\kawai\dev\works\ai_talk_core\ai_talk_core"
$env:DIFY_BASE_URL = "http://localhost/v1"
$env:DIFY_API_KEY = "app-..."
python -m sword_voice_agent.apps.send_handoff_to_dify --source web --field command
```

Difyへ実送信せず、handoffから作られる `AgentRequest` だけ確認する場合:

```powershell
cd C:\Users\kawai\dev\works\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.send_handoff_to_dify `
  --ai-talk-core-root C:\Users\kawai\dev\works\ai_talk_core\ai_talk_core `
  --source web `
  --field command `
  --dry-run
```

`--field` は `command`, `transcript`, `prompt` から選べます。Dify Chat APIには `response_mode=blocking` で `/chat-messages` へ送ります。

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

HTTP receiverの応答には、録音制御用のcommandも含まれます。

```json
{
  "type": "voice_control_command",
  "action": "start_recording",
  "mic_enabled": true,
  "reason": "activation_delay_passed",
  "source": "sword_voice_agent",
  "timestamp": 0.4
}
```

## 次の実装

1. 実機で `mediapipe-sword-sign -> sword-voice-agent -> ai_talk_core` の録音開始/停止を確認する。
2. Dify応答の表示/TTSを追加する。
3. `ai_talk_core` の処理完了を監視してDify送信まで自動化する。
4. 必要ならWebSocket receiverも追加する。
