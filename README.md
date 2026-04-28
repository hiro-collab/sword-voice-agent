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
- application: gesture入力からinput gate更新、録音制御、status更新などのユースケース。
- adapters: Dify API、WebSocket、既存モジュール接続などの具体I/O。
- apps: 各部品を組み合わせる実行アプリ。

各ステージのフラグ、ステート、`turn_id` の authority は [docs/state_authority.md](docs/state_authority.md) にまとめています。

## 現在入っているもの

- `GestureState`, `VoiceState`, `AgentRequest`, `AgentResponse`
- 刀印の揺れを吸収する `GestureInputGate`
- `GestureState` をHTTP POSTで受け取るreceiver
- `mediapipe-sword-sign` のUDP publisherから `GestureState` を受け取るreceiver
- `VoiceState` を `ai_talk_core` の input gate payload へ変換するadapter
- `VoiceState` のON/OFFエッジから `start_recording` / `stop_recording` を作るturn controller
- gesture / voice / Dify応答をひも付ける `turn_id`
- Dify Chat App API用の最小クライアント
- `ai_talk_core` のhandoff更新を監視してDifyへ送るwatcher
- `ai_talk_core` / gesture receiver / Dify応答をまとめて見る統合コンソール
- `.cache\sword_voice_agent` に集約するstatus snapshot / event log
- JSON Linesでinput gateを試せるCLI

## リポジトリ配置

このREADMEの `sword_voice_agent` を実行するコマンドは、次の内側ディレクトリから実行します。

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
```

外側の `<workspace>\sword-voice-agent` には `src` がないため、そこで `PYTHONPATH=src` を指定しても `No module named 'sword_voice_agent'` になります。

関連モジュールは別リポジトリです。

```text
<workspace>\sword-voice-agent\sword-voice-agent  # 統合アプリ
<ai_talk_core_root>                              # 音声/STT/Web UI
<mediapipe_sword_sign_root>                      # 刀印検出
```

## 開発

実行場所: このリポジトリのルート、つまり `<workspace>\sword-voice-agent\sword-voice-agent`

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

CLIデモ:

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gate_simulator --demo
```

標準入力から `GestureState` JSON Lines を流すこともできます。

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
'{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}' | python -m sword_voice_agent.apps.gate_simulator
```

HTTP receiver:

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server --host 127.0.0.1 --port 8787
```

`ai_talk_core` 側にinput gate endpointを用意した後は、receiverから転送できます。

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server `
  --host 127.0.0.1 `
  --port 8787 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate
```

別ターミナルから:

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8787/gesture-state `
  -ContentType "application/json" `
  -Body '{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}'
```

UDP receiver:

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver `
  --host 127.0.0.1 `
  --port 8765 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate `
  --debug `
  --debug-every 30 `
  --status-dir .cache\sword_voice_agent
```

`mediapipe-sword-sign` 側から送る場合:

```powershell
cd <mediapipe_sword_sign_root>
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
```

`mediapipe-sword-sign` のカメラ/手検出/信頼度を画面でも確認したい場合は、送信側に `--preview` を追加します。

```powershell
cd <mediapipe_sword_sign_root>
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30 --preview
```

protobuf の非推奨warningが通常ログに混ざって見づらい場合は、送信側に `--suppress-protobuf-warnings` を追加します。

### ローカル外から使う場合の認証

既定の `127.0.0.1` bind は、ローカル実験を優先してトークンなしで使えます。`0.0.0.0` やLAN IPなど、loopback以外にbindする場合は認証トークンが必須です。

```powershell
$env:SWORD_VOICE_AGENT_AUTH_TOKEN = "任意の長いランダム文字列"
```

HTTP receiver と統合コンソールは、次のどちらかでトークンを渡します。

```text
Authorization: Bearer <token>
X-Sword-Agent-Token: <token>
```

UDP receiverで `--auth-token` または `SWORD_VOICE_AGENT_AUTH_TOKEN` を設定した場合、UDP payload に次のいずれかを含める必要があります。トークン値はログやstatus JSONには保存しません。

```json
{
  "auth_token": "<token>"
}
```

```json
{
  "auth": {
    "token": "<token>"
  }
}
```

HTTP receiver の `/gesture-state` は、既定で64KiBを超えるJSON bodyを拒否します。必要な場合だけ `--max-body-bytes` で上限を調整してください。

## ローカル統合手順

以降の例では、次のプレースホルダを使います。実際のローカルパスは各自の環境に合わせて置き換えてください。

```text
<repo_root> = <workspace>\sword-voice-agent\sword-voice-agent
<ai_talk_core_root> = ai_talk_core のリポジトリルート
<mediapipe_sword_sign_root> = mediapipe-sword-sign のリポジトリルート
```

個人の絶対パス、APIキー、認証トークン、ローカルログ、`.cache` 配下の実行結果はコミットしないでください。DifyアプリのAPIキーは `<dify_app_api_key>` のようなプレースホルダで表記します。

1. `ai_talk_core` のWeb UIを起動する。

```powershell
cd <ai_talk_core_root>
uv run python -m src.web.app
```

2. ブラウザで `http://127.0.0.1:8000` を開き、ブラウザ録音の `入力ゲートで録音を制御する` を有効にする。

3. `sword-voice-agent` のUDP receiverを起動する。

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver `
  --host 127.0.0.1 `
  --port 8765 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate `
  --debug `
  --debug-every 30 `
  --status-dir .cache\sword_voice_agent
```

4. `mediapipe-sword-sign` からUDPで `GestureState` を送る。

```powershell
cd <mediapipe_sword_sign_root>
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
```

この状態で刀印が安定検出されると、`ai_talk_core` のinput gateがenabledになり、Web UI側のブラウザ録音が開始します。刀印を解除するとinput gateがdisabledになり、録音停止とアップロード処理に進みます。

5. `ai_talk_core` のWeb UIで `handoff payload を保存する` を有効にしておく。

6. 保存されたhandoffをDifyへ自動送信するwatcherを起動する。

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
$env:AI_TALK_CORE_ROOT = "<ai_talk_core_root>"
$env:DIFY_BASE_URL = "http://localhost:8080/v1"
$env:DIFY_API_KEY = "<dify_app_api_key>"
python -m sword_voice_agent.apps.watch_handoff_to_dify `
  --source web `
  --field command `
  --skip-existing `
  --status-dir .cache\sword_voice_agent
```

`DIFY_BASE_URL` で平文HTTPを使えるのは `localhost` / `127.0.0.1` / `::1` などのloopbackだけです。LAN上や外部のDifyへ接続する場合は `https://...` を使ってください。

新しいhandoffが保存されるたびにDifyへ送信し、結果を次のファイルに保存します。

```text
<ai_talk_core_root>\.cache\codex\web_dify_latest.json
<ai_talk_core_root>\.cache\codex\web_dify_latest.txt
<ai_talk_core_root>\.cache\codex\web_dify_conversation_id.txt
```

統合コンソール向けには、同時に次のstatus storeへ最新状態とイベント履歴を書きます。

```text
<repo_root>\.cache\sword_voice_agent\latest_gesture.json
<repo_root>\.cache\sword_voice_agent\latest_voice_turn.json
<repo_root>\.cache\sword_voice_agent\latest_dify_response.json
<repo_root>\.cache\sword_voice_agent\events.jsonl
```

`events.jsonl` は `event_id`, `type`, `timestamp`, `source`, `turn_id`, `payload` を持つJSON Linesです。Difyイベントは履歴用途のため、request/response本文とconversation_idを `[redacted]` として保存し、直近200件に制限します。最新のDify応答本文は `latest_dify_response.json` に残るため、`.cache` 配下は引き続きコミットしないでください。

初回実装では、handoffと `turn_id` の厳密な対応付けはまだ行いません。watcherは `latest_voice_turn.json` に残っている最新 `turn_id` をstatus store側のDify結果とイベントへ緩く付与しますが、Difyへ送る `inputs/context` には自動では含めません。

`web_dify_conversation_id.txt` がある場合は、次回以降の送信でDifyの同じ会話を継続します。会話を継続したくない場合は `--no-conversation-state` を追加します。

Difyへ実送信せず、handoffから作られる `AgentRequest` だけ確認する場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.watch_handoff_to_dify `
  --ai-talk-core-root <ai_talk_core_root> `
  --source web `
  --field command `
  --once `
  --dry-run `
  --print-json `
  --status-dir .cache\sword_voice_agent
```

手動で現在のhandoffを1回だけ送る場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
$env:AI_TALK_CORE_ROOT = "<ai_talk_core_root>"
$env:DIFY_BASE_URL = "http://localhost:8080/v1"
$env:DIFY_API_KEY = "<dify_app_api_key>"
python -m sword_voice_agent.apps.send_handoff_to_dify --source web --field command
```

手動送信でDifyへ実送信せず、handoffから作られる `AgentRequest` だけ確認する場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.send_handoff_to_dify `
  --ai-talk-core-root <ai_talk_core_root> `
  --source web `
  --field command `
  --dry-run
```

`--field` は `command`, `transcript`, `prompt` から選べます。Dify Chat APIには `response_mode=blocking` で `/chat-messages` へ送ります。

`ai_talk_core` が無音として保存した `音声を認識できませんでした。` は、watcherではデフォルトでDifyへ送りません。確認用に送信したい場合だけ `--send-no-speech` を追加します。

既定ではDifyへ送る本文は `--field` で選んだ値だけです。`--field command` の場合、Difyの `query` には `command` が送られますが、元の `transcript` は `inputs/context` へ自動では含めません。transcriptもDifyへ渡したい場合だけ、明示的に `--include-transcript-context` を追加します。

7. 統合コンソールを起動する。

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.console_server `
  --host 127.0.0.1 `
  --port 8790 `
  --ai-talk-core-root <ai_talk_core_root> `
  --status-dir .cache\sword_voice_agent `
  --input-gate-url http://127.0.0.1:8000/api/input-gate
```

ブラウザで開きます。

```text
http://127.0.0.1:8790
```

このコンソールは仮組みの監視画面です。gesture receiver、ai_talk_core input gate、最新handoff、Difyの最新応答、トークン使用量、`turn_id`、イベント履歴を1秒間隔で表示します。

統合コンソールの `/api/status` には、音声文字起こし、command、Dify応答、conversation_id、ローカルパスが含まれます。loopback以外に公開する場合は必ず `SWORD_VOICE_AGENT_AUTH_TOKEN` または `--auth-token` を設定してください。ブラウザ画面右上の `auth token` 欄に同じ値を入れると、そのセッション中だけ `Authorization: Bearer <token>` を付けて `/api/status` を取得します。

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
  "timestamp": 0.4,
  "turn_id": "b4f5c1e2..."
}
```

## 次の実装

1. 統合コンソールから各プロセスの起動/停止を扱えるようにする。
2. Dify応答のTTS連携を追加する。
3. 必要ならWebSocket receiverも追加する。
4. status storeのJSON契約をschema化する。
