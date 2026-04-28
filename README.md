# sword-voice-agent

刀印ジェスチャーを検出している間だけ音声入力を受け付ける、ジェスチャー制御型 AI 音声エージェントです。

このリポジトリは統合アプリの土台です。`mediapipe-sword-sign` と `ai_talk_core` を直接混ぜ込まず、共通 protocol と adapter で接続します。

## まず何をするか

| やりたいこと | 読む場所 | 到達点 |
|---|---|---|
| この repo 単体で動作確認したい | [5分で動かす](#5分で動かす) | test / CLI demo が通る |
| ジェスチャーなしで UDP receiver を試したい | [UDP receiver を単体で試す](#udp-receiver-を単体で試す) | `start_recording` / `stop_recording` が見える |
| 実機で録音制御したい | [フル統合の全体像](#フル統合の全体像) | gesture -> ai_talk_core 録音制御 |
| Dify 送信だけ確認したい | [Dify 送信だけ確認する](#dify-送信だけ確認する) | dry-run または Dify 応答保存 |
| 状態を画面で見たい | [統合コンソール](#統合コンソール) | browser で最新状態を見る |
| 何が保存されるか知りたい | [status store](#status-store) | `.cache/sword_voice_agent` の役割が分かる |

## 5分で動かす

外部リポジトリ、カメラ、Dify を使わず、この repo だけで確認する最短ルートです。

```powershell
cd <repo_root>
.\scripts\check.ps1
```

期待する結果:

```text
Ran ... tests
OK
```

CLI demo:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gate_simulator --demo
```

期待する見え方:

- 刀印が安定したところで `mic_enabled` が `true` になる。
- 刀印が消えて release delay を過ぎると `mic_enabled` が `false` になる。

## 作業ルート

この README の `<repo_root>` は、この内側ディレクトリです。

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
```

外側の `<workspace>\sword-voice-agent` には `src` がないため、そこで `PYTHONPATH=src` を指定しても `No module named 'sword_voice_agent'` になります。

関連リポジトリ:

```text
<repo_root>                    # sword-voice-agent、このリポジトリ
<ai_talk_core_root>            # 音声/STT/Web UI
<mediapipe_sword_sign_root>    # 刀印検出
```

## 前提条件

| 必要なもの | いつ必要か | 確認ポイント |
|---|---|---|
| Python 3.10+ | 常時 | `python --version` |
| PowerShell | Windows の手順実行 | `.\scripts\check.ps1` が実行できる |
| `ai_talk_core` | 実機録音制御 | Web UI と input gate API が使える |
| `mediapipe-sword-sign` | 実機ジェスチャー入力 | UDP publisher `apps/publish_udp.py` が使える |
| `uv` | 兄弟 repo の起動例 | `uv run ...` が使える |
| Dify Chat App API key | Dify 送信 | `DIFY_API_KEY` を環境変数で渡す |
| ブラウザ録音許可 | 実機録音 | `ai_talk_core` の Web UI でマイク許可 |

個人の絶対パス、API key、認証 token、ローカルログ、`.cache` 配下の実行結果はコミットしないでください。README では Dify アプリの API key を `<dify_app_api_key>` と書きます。

## 実行方法

開発中は install なしで `PYTHONPATH=src` を使います。

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver --help
```

パッケージとして使う場合は editable install できます。

```powershell
cd <repo_root>
python -m pip install -e .
```

インストール後のコマンド:

| コマンド | 対応 module |
|---|---|
| `sword-gate-sim` | `sword_voice_agent.apps.gate_simulator` |
| `sword-gesture-http` | `sword_voice_agent.apps.gesture_http_server` |
| `sword-gesture-udp` | `sword_voice_agent.apps.gesture_udp_receiver` |
| `sword-demo-gestures` | `sword_voice_agent.apps.send_demo_gestures` |
| `sword-dify-handoff` | `sword_voice_agent.apps.send_handoff_to_dify` |
| `sword-dify-watch` | `sword_voice_agent.apps.watch_handoff_to_dify` |
| `sword-console` | `sword_voice_agent.apps.console_server` |
| `sword-status-clear` | `sword_voice_agent.apps.clear_status` |

この README の例は、未インストールでも動かせるように `python -m ...` で書いています。

## UDP receiver を単体で試す

実機カメラなしで、receiver と status store の動きを確認する手順です。2つの PowerShell を使います。

Terminal 1: UDP receiver

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_udp_receiver `
  --host 127.0.0.1 `
  --port 8765 `
  --debug `
  --debug-every 1 `
  --status-dir .cache\sword_voice_agent
```

期待するログ:

```text
listening for GestureState UDP on 127.0.0.1:8765
```

Terminal 2: demo gesture sender

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.send_demo_gestures --host 127.0.0.1 --port 8765 --print-json
```

Terminal 1 で期待するログ:

```text
[gesture-udp] ... reason=activation_delay_passed ... action=start_recording ...
[gesture-udp] ... reason=release_delay_passed ... action=stop_recording ...
```

作成されるファイル:

```text
<repo_root>\.cache\sword_voice_agent\latest_gesture.json
<repo_root>\.cache\sword_voice_agent\latest_voice_turn.json
<repo_root>\.cache\sword_voice_agent\events.jsonl
```

## フル統合の全体像

実機統合では、複数のブロッキングプロセスを同時に起動します。番号順に開きますが、Terminal 1, 3, 4, 5 は起動したままにします。

| Terminal | 起動するもの | ブロッキング | 役割 |
|---|---|---:|---|
| 1 | `ai_talk_core` Web UI | yes | ブラウザ録音 / STT / handoff 保存 |
| 2 | ブラウザ | no | input gate 録音制御を有効化 |
| 3 | `sword-voice-agent` UDP receiver | yes | gesture を受けて input gate へ転送 |
| 4 | `mediapipe-sword-sign` UDP publisher | yes | カメラで刀印を検出して送信 |
| 5 | Dify watcher | yes | handoff を Dify へ送信 |
| 6 | 統合コンソール | yes | 最新状態を browser で確認 |

データの流れ:

```text
Camera
  -> mediapipe-sword-sign UDP publisher
  -> sword-voice-agent UDP receiver
  -> GestureInputGate
  -> ai_talk_core input gate API
  -> browser recording / STT
  -> handoff files
  -> Dify watcher
  -> Dify Chat API
  -> status store / console
```

## フル統合手順

### Terminal 1: ai_talk_core を起動

```powershell
cd <ai_talk_core_root>
uv run python -m src.web.app
```

期待する状態:

- `http://127.0.0.1:8000` が開ける。
- Web UI でブラウザ録音を使える。

### Terminal 2: ブラウザ設定

ブラウザで `http://127.0.0.1:8000` を開きます。

設定:

- `入力ゲートで録音を制御する` を有効にする。
- `handoff payload を保存する` を有効にする。
- ブラウザのマイク権限を許可する。

### Terminal 3: sword-voice-agent UDP receiver

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

期待するログ:

```text
listening for GestureState UDP on 127.0.0.1:8765
```

刀印が検出されると、次のようなログが出ます。

```text
[gesture-udp] ... raw_active=1 ... mic_enabled=1 ... input_gate=ok ...
```

### Terminal 4: mediapipe-sword-sign UDP publisher

```powershell
cd <mediapipe_sword_sign_root>
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30
```

カメラ/手検出/信頼度を画面で見たい場合:

```powershell
cd <mediapipe_sword_sign_root>
uv run python apps/publish_udp.py --host 127.0.0.1 --port 8765 --debug --debug-every 30 --preview
```

protobuf の非推奨 warning が見づらい場合は、送信側に `--suppress-protobuf-warnings` を追加します。

期待する動き:

- 刀印を 0.3 秒以上検出すると `ai_talk_core` の input gate が enabled になる。
- Web UI 側のブラウザ録音が開始する。
- 刀印を解除して 0.5 秒以上経つと input gate が disabled になる。
- 録音停止後、STT と handoff 保存へ進む。

### Terminal 5: Dify watcher

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

`DIFY_BASE_URL` で平文 HTTP を使えるのは `localhost` / `127.0.0.1` / `::1` などの loopback だけです。LAN 上や外部の Dify へ接続する場合は `https://...` を使ってください。

期待する保存先:

```text
<ai_talk_core_root>\.cache\codex\web_dify_latest.json
<ai_talk_core_root>\.cache\codex\web_dify_latest.txt
<ai_talk_core_root>\.cache\codex\web_dify_conversation_id.txt
```

`web_dify_conversation_id.txt` がある場合は、次回以降の送信で Dify の同じ会話を継続します。会話を継続したくない場合は `--no-conversation-state` を追加します。

## 統合コンソール

Terminal 6:

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

表示されるもの:

- gesture receiver の最新状態
- ai_talk_core input gate の状態
- 最新 handoff の transcript / command
- Dify の最新応答
- token 使用量
- `turn_id`
- status store の event log

`/api/status` には音声文字起こし、command、Dify 応答、conversation_id、ローカルパスが含まれます。loopback 以外に公開する場合は必ず `SWORD_VOICE_AGENT_AUTH_TOKEN` または `--auth-token` を設定してください。ブラウザ画面右上の `auth token` 欄に同じ値を入れると、そのセッション中だけ `Authorization: Bearer <token>` を付けて `/api/status` を取得します。

## Dify 送信だけ確認する

実送信せず、handoff から作られる `AgentRequest` だけ確認する場合:

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

手動で現在の handoff を 1 回だけ送る場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
$env:AI_TALK_CORE_ROOT = "<ai_talk_core_root>"
$env:DIFY_BASE_URL = "http://localhost:8080/v1"
$env:DIFY_API_KEY = "<dify_app_api_key>"
python -m sword_voice_agent.apps.send_handoff_to_dify --source web --field command
```

手動送信で Dify へ実送信せず、`AgentRequest` だけ確認する場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.send_handoff_to_dify `
  --ai-talk-core-root <ai_talk_core_root> `
  --source web `
  --field command `
  --dry-run `
  --print-json
```

`--field` は `command`, `transcript`, `prompt` から選べます。Dify Chat API には `response_mode=blocking` で `/chat-messages` へ送ります。

`ai_talk_core` が無音として保存した `音声を認識できませんでした。` は、watcher ではデフォルトで Dify へ送りません。確認用に送信したい場合だけ `--send-no-speech` を追加します。

既定では Dify へ送る本文は `--field` で選んだ値だけです。`--field command` の場合、Dify の `query` には `command` が送られますが、元の `transcript` は `inputs/context` へ自動では含めません。transcript も Dify へ渡したい場合だけ、明示的に `--include-transcript-context` を追加します。

## HTTP receiver を試す

UDP ではなく HTTP POST で `GestureState` を受けたい場合の手順です。

Terminal 1:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server --host 127.0.0.1 --port 8787
```

ai_talk_core input gate へ転送する場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.gesture_http_server `
  --host 127.0.0.1 `
  --port 8787 `
  --input-gate-url http://127.0.0.1:8000/api/input-gate
```

Terminal 2:

```powershell
cd <repo_root>
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8787/gesture-state `
  -ContentType "application/json" `
  -Body '{"type":"gesture_state","source":"demo","timestamp":0.0,"gestures":{"sword_sign":{"active":true,"confidence":0.95}}}'
```

HTTP receiver の `/gesture-state` は、既定で 64 KiB を超える JSON body を拒否します。必要な場合だけ `--max-body-bytes` で上限を調整してください。

## status store

統合コンソール向けに、次のファイルへ最新状態とイベント履歴を書きます。

```text
<repo_root>\.cache\sword_voice_agent\latest_gesture.json
<repo_root>\.cache\sword_voice_agent\latest_voice_turn.json
<repo_root>\.cache\sword_voice_agent\latest_dify_response.json
<repo_root>\.cache\sword_voice_agent\events.jsonl
```

`events.jsonl` は `event_id`, `type`, `timestamp`, `source`, `turn_id`, `payload` を持つ JSON Lines です。Dify event は履歴用途のため、request/response 本文と conversation_id を `[redacted]` として保存し、直近 200 件に制限します。最新の Dify 応答本文は `latest_dify_response.json` に残るため、`.cache` 配下はコミットしないでください。

status store のローカル状態を消す場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.clear_status --status-dir .cache\sword_voice_agent --yes
```

初回実装では、handoff と `turn_id` の厳密な対応付けはまだ行いません。watcher は `latest_voice_turn.json` に残っている最新 `turn_id` を status store 側の Dify 結果と event へ緩く付与しますが、Dify へ送る `inputs/context` には自動では含めません。

## 認証

既定の `127.0.0.1` bind は、ローカル実験を優先して token なしで使えます。`0.0.0.0` や LAN IP など、loopback 以外に bind する場合は認証 token が必須です。

```powershell
$env:SWORD_VOICE_AGENT_AUTH_TOKEN = "任意の長いランダム文字列"
```

HTTP receiver と統合コンソールは、次のどちらかで token を渡します。

```text
Authorization: Bearer <token>
X-Sword-Agent-Token: <token>
```

UDP receiver で `--auth-token` または `SWORD_VOICE_AGENT_AUTH_TOKEN` を設定した場合、UDP payload に次のいずれかを含める必要があります。token 値は log や status JSON には保存しません。

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

## protocol 例

`ai_talk_core` へ渡す input gate payload の形:

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

HTTP / UDP receiver の応答には、録音制御用の command も含まれます。

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

## トラブルシュート

| 症状 | 確認すること |
|---|---|
| `No module named 'sword_voice_agent'` | `<repo_root>` で実行しているか、`$env:PYTHONPATH = "src"` を設定したか |
| UDP receiver に何も出ない | `mediapipe` 側の送信先 host/port が `127.0.0.1:8765` か |
| `input_gate=error` | `ai_talk_core` が起動しているか、`--input-gate-url` が正しいか |
| Web UI が録音しない | ブラウザのマイク権限と `入力ゲートで録音を制御する` を確認 |
| Dify watcher が送信しない | `handoff payload を保存する` が有効か、`--skip-existing` で既存 handoff を無視していないか |
| Dify が `api_key is required` | `$env:DIFY_API_KEY` が設定されているか |
| remote から HTTP/console にアクセスできない | loopback 以外では `SWORD_VOICE_AGENT_AUTH_TOKEN` または `--auth-token` が必須 |
| コンソールの状態が古い | `.cache\sword_voice_agent` を `clear_status` で消してから再起動 |

## 設計方針

Ports and Adapters 型で構成します。

- `core`: 状態判定や制御ロジック。WebSocket、Dify、MediaPipe を知らない。
- `protocol`: モジュール間で受け渡す JSON 形式。
- `application`: gesture 入力から input gate 更新、録音制御、status 更新などのユースケース。
- `adapters`: Dify API、HTTP、UDP、既存モジュール接続、file-backed status store などの具体 I/O。
- `apps`: 各部品を組み合わせる実行アプリ。
- `web`: ローカル統合コンソールの静的 UI。

各ステージの flag、state、`turn_id` の authority は [docs/state_authority.md](docs/state_authority.md) にまとめています。

## 現在入っているもの

- `GestureState`, `VoiceState`, `VoiceControlCommand`, `AgentRequest`, `AgentResponse`
- 刀印の揺れを吸収する `GestureInputGate`
- `GestureState` を HTTP POST で受け取る receiver
- `mediapipe-sword-sign` の UDP publisher から `GestureState` を受け取る receiver
- gesture 入力、input gate 更新、録音制御をつなぐ application pipeline
- `VoiceState` を `ai_talk_core` の input gate payload へ変換する adapter
- `VoiceState` の ON/OFF edge から `start_recording` / `stop_recording` を作る turn controller
- gesture / voice / Dify 応答をひも付ける local `turn_id`
- Dify Chat App API 用の最小 client
- `ai_talk_core` の handoff 更新を監視して Dify へ送る watcher
- `ai_talk_core` / gesture receiver / Dify 応答をまとめて見る統合コンソール
- `.cache\sword_voice_agent` に集約する status snapshot / event log
- JSON Lines で input gate を試せる CLI

## 次の実装

1. 統合コンソールから各プロセスの起動/停止を扱えるようにする。
2. Dify 応答の TTS 連携を追加する。
3. 必要なら WebSocket receiver も追加する。
4. status store の JSON 契約を schema 化する。
