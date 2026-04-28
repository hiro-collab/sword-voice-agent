# sword-voice-agent

刀印ジェスチャーを検出している間だけ音声入力を受け付け、`ai_talk_core` の handoff を Dify へ送るローカル統合アプリです。

この README は、ローカル実行に必要な `.env` と PowerShell 起動スクリプトだけを扱います。

## 作業ルート

この README の `<repo_root>` は、内側のリポジトリディレクトリです。

```powershell
cd <workspace>\sword-voice-agent\sword-voice-agent
```

外側の `<workspace>\sword-voice-agent` ではありません。

## .env を作る

`.env.example` をコピーして、ローカル環境の実値を入れます。

```powershell
cd <repo_root>
Copy-Item .env.example .env
notepad .env
```

最低限、次を設定します。

```text
AI_TALK_CORE_ROOT=..\ai_talk_core
MEDIAPIPE_SWORD_SIGN_ROOT=..\mediapipe-sword-sign
AI_TALK_CORE_INPUT_GATE_URL=http://127.0.0.1:8000/api/input-gate
AI_TALK_CORE_WEB_TOKEN=
DIFY_BASE_URL=http://localhost:8080/v1
DIFY_API_KEY=<dify_app_api_key>
DIFY_USER=local-user
SWORD_VOICE_AGENT_AUTH_TOKEN=
SWORD_VOICE_AGENT_REDACT_STATUS=0
```

`.env` には個人の絶対パス、Dify API key、認証 token を入れるため、コミットしません。`.env` は `.gitignore` 済みです。

`DIFY_BASE_URL` と `AI_TALK_CORE_INPUT_GATE_URL` は `http://` の場合、`localhost` / `127.0.0.1` / `::1` のみ許可します。リモートホストへ向ける場合は `https://` を使ってください。URL に `user:password@host` のような認証情報を埋め込む設定も拒否します。

HTTP / UDP / 統合コンソールを `0.0.0.0` など loopback 以外へ bind する場合は、`SWORD_VOICE_AGENT_AUTH_TOKEN` または `--auth-token` が必須です。HTTP / console は `Authorization: Bearer <token>` または `X-Sword-Agent-Token`、UDP は payload の `auth_token` または `auth.token` を検証します。

各入口には軽量なIP単位rate limitがあります。通常のローカル検証では既定値のままで問題ありません。必要な場合だけ、HTTP receiver / UDP receiver の `--rate-limit-per-minute`、console の `--api-rate-limit-per-minute` で調整してください。`0` で無効化できます。

統合コンソールの `/api/status` は音声認識結果、Dify応答、conversation_id、ローカルパスを扱います。共有画面や外部公開に近い使い方では、`SWORD_VOICE_AGENT_REDACT_STATUS=1` または console の `--redact-sensitive` を使うと、本文・ID・パスを `[redacted]` にして返します。

更新後の `ai_talk_core` は local API に `X-AI-Core-Token` を要求します。まとめて起動する場合は、`.env` の `AI_TALK_CORE_WEB_TOKEN` が空でも起動スクリプトが一時トークンを生成して各プロセスへ共有します。個別に別 PowerShell から起動する場合は、同じ `AI_TALK_CORE_WEB_TOKEN` を `.env` または環境変数に設定してください。

`..\ai_talk_core` と `..\mediapipe-sword-sign` は、外側の `<workspace>\sword-voice-agent` 直下に置く検証用 clone です。開発用 clone と分けておくと、外部モジュールを並行開発していても本プロジェクトの検証が安定します。

検証用 clone を作成または更新する場合:

```powershell
cd <repo_root>
.\scripts\setup-validation-modules.ps1 -DryRun
.\scripts\setup-validation-modules.ps1
```

既存の `.env` も検証用 clone へ向けたい場合:

```powershell
.\scripts\setup-validation-modules.ps1 -UpdateEnv
```

このスクリプトは既存 clone に未コミット差分がある場合、pull せずにスキップします。破壊的な reset は行いません。

`mediapipe-sword-sign` の UDP publisher は、モジュール側で作成した `gesture_model.pkl` を使います。この統合リポジトリでは `.pkl` を生成・同梱しません。`<mediapipe-sword-sign>` 側の README を見ながら、データ収集と学習を行ってください。

代表的な流れ:

```powershell
cd <workspace>\sword-voice-agent\mediapipe-sword-sign
uv run collect_data.py
uv run train_model.py
uv run predict.py
```

`gesture_model.pkl` は pickle/joblib 形式なので、信頼できないファイルを使わないでください。自分で収集・学習したモデルを使うのが基本です。

読み込み確認:

```powershell
cd <repo_root>
.\scripts\load-env.ps1
$env:AI_TALK_CORE_ROOT
$env:DIFY_BASE_URL
```

`<ai_talk_core_root>` のようなプレースホルダが表示される場合は、`.env` を実パスへ直してください。

## まとめて起動する

まず `-DryRun` で、起動されるコマンドだけ確認します。`-DryRun` では別 PowerShell ウィンドウもサーバーも起動しません。

```powershell
cd <repo_root>
.\scripts\start-full-stack.ps1 -DryRun
```

問題なければ、`-DryRun` を外して起動します。各プロセスは別 PowerShell ウィンドウで開きます。

```powershell
cd <repo_root>
.\scripts\start-full-stack.ps1 -Preview -SuppressProtobufWarnings
```

Dify watcher を起動する場合、`DIFY_BASE_URL` が `localhost` / `127.0.0.1` なら、起動前に Docker engine と Dify API の到達性を確認します。Docker Desktop が起動していなければ自動起動し、Dify API が応答するまで待ちます。Dify コンテナが停止している場合は、ここで止まるので Docker Desktop 側で Dify を起動してください。

この起動方法では、`ai_talk_core` Web UI の統合向け初期設定として、次のチェックが最初から入ります。

- `入力ゲートで録音を制御する`
- `handoff を保存する`

最新版の `ai_talk_core` では、Web UI の `integration` プリセットを使ってこの初期設定を適用します。古い `ai_talk_core` checkout では、互換レイヤーが初期HTMLだけを調整します。どちらの場合も、`ai_talk_core` 単体起動時の既定値は変更しません。

起動後に開く画面:

```text
ai_talk_core Web UI: http://127.0.0.1:8000
sword-voice-agent console: http://127.0.0.1:8790
```

よく使うオプション:

| Option | 用途 |
|---|---|
| `-DryRun` | 起動せず、実行予定のコマンドだけ表示 |
| `-Preview` | `mediapipe-sword-sign` の OpenCV preview を表示 |
| `-SuppressProtobufWarnings` | MediaPipe / protobuf warning を抑制 |
| `-SkipAiTalkCore` | すでに `ai_talk_core` を起動済みの場合に省略 |
| `-SkipMediapipe` | カメラ送信を起動しない |
| `-SkipDifyWatch` | Dify watcher を起動しない |
| `-SkipConsole` | 統合コンソールを起動しない |
| `-SkipDockerCheck` | Docker Desktop / Dify API の起動前チェックを省略 |
| `-NoStartDockerDesktop` | Docker Desktop を自動起動せず、未起動ならエラーにする |
| `-NoAiTalkCoreIntegrationDefaults` | `ai_talk_core` Web UI の統合向けチェックを入れない |
| `-NoRecordGateAuto` | `入力ゲートで録音を制御する` だけ入れない |
| `-NoSaveHandoff` | `handoff を保存する` だけ入れない |

統合コンソール上段の `Dify API` も緑になっていることを確認してください。`Dify watcher` が緑でも、`Dify API` が緑でない場合は Docker Desktop または Dify コンテナがまだ準備できていません。

## 個別に起動する

切り分けたい場合は、個別スクリプトを使います。各スクリプトは既定で `.env` を読み込みます。

| Script | 起動するもの |
|---|---|
| `.\scripts\start-ai-talk-core.ps1` | `ai_talk_core` Web UI |
| `.\scripts\start-gesture-udp.ps1` | sword-voice-agent UDP receiver |
| `.\scripts\start-mediapipe-udp.ps1` | mediapipe-sword-sign UDP publisher |
| `.\scripts\start-dify-watch.ps1` | ai_talk_core handoff -> Dify watcher |
| `.\scripts\start-console.ps1` | 統合コンソール |
| `.\scripts\start-demo-udp.ps1` | デモ用 gesture UDP sender |

例:

```powershell
cd <repo_root>
.\scripts\start-dify-watch.ps1 -DryRun
.\scripts\start-console.ps1
```

`ai_talk_core` を単体既定値に近い状態で起動したい場合:

```powershell
.\scripts\start-ai-talk-core.ps1 -NoIntegrationDefaults
```

別の env ファイルを使う場合:

```powershell
.\scripts\start-full-stack.ps1 -EnvPath .env.local -DryRun
```

## 停止する

起動した各 PowerShell ウィンドウで `Ctrl+C` を押します。

古い status 表示を消したい場合:

```powershell
cd <repo_root>
$env:PYTHONPATH = "src"
python -m sword_voice_agent.apps.clear_status --status-dir .cache\sword_voice_agent --yes
```

統合コンソールを起動している場合は、画面右上の `履歴クリア` でも同じ status キャッシュとイベント履歴を削除できます。外部 bind 時は console API と同じ auth token が必要です。

## チェック

コードと PowerShell スクリプトの基本チェック:

```powershell
cd <repo_root>
.\scripts\check.ps1
```

期待する結果:

```text
Ran ... tests
OK
```
