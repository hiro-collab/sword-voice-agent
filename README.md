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

設定項目は次の表を目安にします。

### モジュール配置

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `AI_TALK_CORE_ROOT` | `..\ai_talk_core` | 必須 | 検証用 `ai_talk_core` clone の場所。相対パスは `<repo_root>` 基準です。 |
| `MEDIAPIPE_SWORD_SIGN_ROOT` | `..\mediapipe-sword-sign` | 必須 | 検証用 `mediapipe-sword-sign` clone の場所。 |
| `TTS_SERVICE_ROOT` | `..\tts-service` | TTS使用時 | 検証用 `tts-service` clone の場所。TTSを使わない場合は未使用です。 |

`..\ai_talk_core`、`..\mediapipe-sword-sign`、`..\tts-service` は、外側の `<workspace>\sword-voice-agent` 直下に置く検証用 clone です。開発用 clone と分けておくと、外部モジュールを並行開発していても本プロジェクトの検証が安定します。

### ai_talk_core

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `AI_TALK_CORE_INPUT_GATE_URL` | `http://127.0.0.1:8000/api/input-gate` | 必須 | `ai_talk_core` の入力ゲートAPI。`http://` の場合は loopback のみ許可します。 |
| `AI_TALK_CORE_WEB_TOKEN` | 空、または任意の長い文字列 | 任意 | `ai_talk_core` local API 用トークン。空なら `start-full-stack.ps1` が一時トークンを生成して各プロセスへ共有します。個別起動では同じ値を各 PowerShell に読み込ませます。 |

### mediapipe-sword-sign

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `MEDIAPIPE_SWORD_SIGN_MODEL_PATH` | `gesture_model.pkl` | 必須 | `MEDIAPIPE_SWORD_SIGN_ROOT` からの相対パス、または絶対パス。起動前に存在、サイズ、SHA-256、読み込み可否、カメラ可否を確認します。 |
| `MEDIAPIPE_SWORD_SIGN_MODEL_SHA256` | 空、または期待するSHA-256 | 任意 | 外部から受け取ったモデルを使う場合の検証用hash。 |
| `MEDIAPIPE_SWORD_SIGN_ALLOW_UNTRUSTED_MODEL` | `0` | 任意 | `1` にするとhash未検証モデルの読み込みを許可します。通常は `0` のままにします。 |
| `MEDIAPIPE_SWORD_SIGN_HEARTBEAT_EVERY` | `1s` | 任意 | MediaPipe publisher からの診断 heartbeat 間隔。`0` / `off` / `none` で無効化できます。 |

### Dify

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `DIFY_BASE_URL` | `http://localhost:8080/v1` | Dify連携時 | Dify API のURL。`http://` の場合は loopback のみ許可します。リモートホストへ向ける場合は `https://` を使ってください。 |
| `DIFY_API_KEY` | `<dify_app_api_key>` | Dify連携時 | DifyアプリのAPIキー。コミットしません。 |
| `DIFY_USER` | `local-user` | 任意 | Dify conversation の user 識別子。 |
| `DIFY_RESPONSE_MODE` | `streaming` | 任意 | Dify watcher の応答モード。`streaming` は SSE を読み、first token / done の計測イベントを status に出します。互換確認時は `blocking` にできます。 |

### TTS

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `TTS_OUTPUT_STATUS_DIR` | `.cache\tts_service` | TTS使用時 | `tts-service` が `latest_tts_state.json` を書く場所。統合コンソールがここを読みます。 |
| `TTS_SOURCE` | `http` | 任意 | TTS入力方式。`http` は Dify streaming delta を `/api/tts/chunk` へ直接送り、`status-file` は従来通り `latest_dify_response.json` を監視します。 |
| `TTS_HTTP_HOST` | `127.0.0.1` | HTTP TTS使用時 | `tts-service` HTTP source の bind host。 |
| `TTS_HTTP_PORT` | `8765` | HTTP TTS使用時 | `tts-service` HTTP source の port。 |
| `TTS_HTTP_CHUNK_MAX_CHARS` | `80` | HTTP TTS使用時 | streaming delta をTTS requestへ切り出す最大文字数。句点・改行でも切り出します。 |
| `TTS_HTTP_CHUNK_URL` | `http://127.0.0.1:8765/api/tts/chunk` | HTTP TTS使用時 | Dify watcher が streaming delta をPOSTするURL。`start-full-stack.ps1 -EnableTts` では自動生成します。 |
| `TTS_HTTP_TIMEOUT_S` | `0.75` | HTTP TTS使用時 | Dify watcher からTTS HTTP sourceへの1回のPOST timeout。 |
| `TTS_ENGINE` | `windows-sapi` | TTS使用時 | TTSエンジン。`windows-sapi` または `noop`。`noop` は音声生成せず status 連携だけ確認します。 |
| `TTS_PLAYER` | `speaker` | TTS使用時 | 再生先。`speaker` / `file` / `noop`。`file` は音声ファイル出力、`noop` は再生なしです。 |
| `TTS_POLL_INTERVAL` | `1.0` | 任意 | Dify応答ファイルを監視する間隔、秒。 |
| `TTS_VOICE_NAME` | 空、またはSAPI音声名 | 任意 | Windows SAPI の音声名。空なら既定音声です。 |
| `TTS_RATE` | `0` | 任意 | Windows SAPI の読み上げ速度。 |
| `TTS_VOLUME` | `100` | 任意 | Windows SAPI の音量。 |
| `TTS_OUTPUT_AUDIO_DIR` | `.cache\tts_service\audio_output` | 任意 | `TTS_PLAYER=file` の出力先。 |

`tts-service` は Dify応答の読み上げ用モジュールです。TTSは実際に音が出るため、まとめて起動では既定OFFです。使う場合だけ `start-full-stack.ps1 -EnableTts`、または個別に `start-tts-service.ps1` を起動します。

### セキュリティと表示

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `SWORD_VOICE_AGENT_AUTH_TOKEN` | 空、または任意の長い文字列 | 外部bind時 | HTTP / UDP / 統合コンソールを `0.0.0.0` など loopback 以外へ bind する場合に必須。HTTP / console は `Authorization: Bearer <token>` または `X-Sword-Agent-Token`、UDP は payload の `auth_token` または `auth.token` を検証します。 |
| `SWORD_VOICE_AGENT_REDACT_STATUS` | `0` | 任意 | `1` にすると `/api/status` の本文、ID、ローカルパスを `[redacted]` にします。共有画面や外部公開に近い使い方で有効にします。 |

`.env` には個人の絶対パス、Dify API key、認証 token を入れるため、コミットしません。`.env` は `.gitignore` 済みです。URL に `user:password@host` のような認証情報を埋め込む設定も拒否します。

各入口には軽量なIP単位rate limitがあります。通常のローカル検証では既定値のままで問題ありません。必要な場合だけ、HTTP receiver / UDP receiver の `--rate-limit-per-minute`、console の `--api-rate-limit-per-minute` で調整してください。`0` で無効化できます。

### ジェスチャー判定

| 項目 | 例 | 必須 | 説明 |
|---|---|---:|---|
| `SWORD_VOICE_AGENT_MIN_CONFIDENCE` | `0.8` | 任意 | receiver 側で刀印として扱う最小 confidence。録音が始まりにくい場合は下げ、誤作動が多い場合は上げます。 |
| `SWORD_VOICE_AGENT_ACTIVATION_DELAY` | `0.3` | 任意 | 録音開始まで、刀印が継続している必要がある秒数。検出は来ているのに録音が始まりにくい場合は `0.1` などで切り分けます。 |
| `SWORD_VOICE_AGENT_RELEASE_DELAY` | `0.5` | 任意 | 刀印が消えてから録音停止までの猶予秒数。短いと途切れやすく、長いと停止が遅くなります。 |

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

統合コンソールは外部モジュール向けに status event の SSE も公開します。

```text
GET http://127.0.0.1:8790/api/events
GET http://127.0.0.1:8790/api/events?once=1
```

`/api/events` は `events.jsonl` を投影元にし、`event_id` を SSE の `id`、イベント種別を SSE の `event` として送ります。外部 bind 時は `/api/status` と同じ認証 token が必要です。

## 動作確認チェックリスト

起動後は、まず統合コンソール上段の module card を確認します。次のカードがすべて緑なら、各プロセスと外部APIの最低限の疎通はできています。

- `ai_talk_core Web UI`
- `Gesture UDP receiver`
- `MediaPipe UDP publisher`
- `Dify API`
- `Dify watcher`
- `TTS service` は `-EnableTts` 指定時のみ緑になります
- `Integration console`

その後、刀印を出した状態で短く発話します。正常なら次の流れになります。

```text
Gesture: idle -> active
Input Gate: disabled -> enabled
Voice: ready -> transcript/command 更新
Dify: ready -> answer 更新
```

`ai_talk_core` の local API は `X-AI-Core-Token` を要求します。`start-full-stack.ps1` でまとめて起動した場合は、起動スクリプトが `AI_TALK_CORE_WEB_TOKEN` を各プロセスへ共有します。個別起動で `Input Gate` が更新されない場合は、同じ `AI_TALK_CORE_WEB_TOKEN` を各PowerShellに読み込ませてください。

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
| `-EnableTts` | tts-service watcher を起動してDify応答を読み上げる |
| `-SkipDockerCheck` | Docker Desktop / Dify API の起動前チェックを省略 |
| `-NoStartDockerDesktop` | Docker Desktop を自動起動せず、未起動ならエラーにする |
| `-NoAiTalkCoreIntegrationDefaults` | `ai_talk_core` Web UI の統合向けチェックを入れない |
| `-NoRecordGateAuto` | `入力ゲートで録音を制御する` だけ入れない |
| `-NoSaveHandoff` | `handoff を保存する` だけ入れない |
| `-NoSkipShortAscii` | `inverse` のような短い英字1語のSTT結果も Dify へ送る |
| `-ShortAsciiMaxChars <number>` | 短い英字1語として skip する最大文字数を上書き |
| `-DifyResponseMode blocking\|streaming` | Dify watcher の応答モードを上書き。既定は `streaming` |
| `-GestureMinConfidence <number>` | この起動だけ receiver の最小 confidence を上書き |
| `-GestureActivationDelay <seconds>` | この起動だけ録音開始までの継続秒数を上書き |
| `-GestureReleaseDelay <seconds>` | この起動だけ録音停止までの猶予秒数を上書き |
| `-TtsEngine windows-sapi\|noop` | TTSエンジンを上書き |
| `-TtsPlayer speaker\|file\|noop` | TTSの再生先を上書き |
| `-TtsVoiceName <name>` | Windows SAPI音声名を上書き |
| `-TtsPollInterval <seconds>` | TTS watcherの監視間隔を上書き |
| `-TtsSource http\|status-file` | TTS入力方式を上書き。既定は `http` |
| `-TtsHttpPort <port>` | HTTP TTS source の port を上書き |
| `-TtsHttpChunkMaxChars <number>` | HTTP TTS source の chunk 最大文字数を上書き |

統合コンソール上段の `Dify API` も緑になっていることを確認してください。`Dify watcher` が緑でも、`Dify API` が緑でない場合は Docker Desktop または Dify コンテナがまだ準備できていません。

検出が不安定な環境で一時的に試す例:

```powershell
.\scripts\start-full-stack.ps1 -Preview -GestureActivationDelay 0.1 -GestureMinConfidence 0.7
```

TTSも含めて試す例。既定では Dify streaming delta を `tts-service` の HTTP source に直接流します。

```powershell
.\scripts\start-full-stack.ps1 -Preview -EnableTts
```

音を出さずにTTS連携だけ確認する例:

```powershell
.\scripts\start-full-stack.ps1 -EnableTts -TtsEngine noop
```

## 個別に起動する

切り分けたい場合は、個別スクリプトを使います。各スクリプトは既定で `.env` を読み込みます。

| Script | 起動するもの |
|---|---|
| `.\scripts\start-ai-talk-core.ps1` | `ai_talk_core` Web UI |
| `.\scripts\start-gesture-udp.ps1` | sword-voice-agent UDP receiver |
| `.\scripts\start-mediapipe-udp.ps1` | mediapipe-sword-sign UDP publisher |
| `.\scripts\start-dify-watch.ps1` | ai_talk_core handoff -> Dify watcher |
| `.\scripts\start-tts-service.ps1` | Dify応答 -> tts-service watcher |
| `.\scripts\start-console.ps1` | 統合コンソール |
| `.\scripts\start-demo-udp.ps1` | デモ用 gesture UDP sender |

例:

```powershell
cd <repo_root>
.\scripts\start-dify-watch.ps1 -DryRun
.\scripts\start-console.ps1
```

Dify watcher は既定で、`inverse` のような短い英字1語だけのSTT結果を送信せずに skip します。日本語利用時の無音・環境音・TTS回り込みによるWhisper幻聴を抑えるためです。英単語1語の指示も送信したい場合は次のように起動します。

```powershell
.\scripts\start-dify-watch.ps1 -NoSkipShortAscii
```

Dify watcher は通常 `streaming` モードで起動し、Dify の SSE から first token と完了の時刻を統合コンソールのイベントへ出します。Dify 側の切り分けで従来の blocking に戻したい場合:

```powershell
.\scripts\start-dify-watch.ps1 -ResponseMode blocking
```

MediaPipeモデルだけを事前確認したい場合:

```powershell
.\scripts\start-mediapipe-udp.ps1 -PrecheckOnly
.\scripts\start-mediapipe-udp.ps1 -SkipModelPrecheck
```

通常起動では事前チェックが実行されます。`-SkipModelPrecheck` は、カメラを別プロセスが掴んでいるなど、理由が明確な場合だけ使います。

TTSだけを個別起動する場合:

```powershell
.\scripts\start-tts-service.ps1 -DryRun
.\scripts\start-tts-service.ps1
```

Windows SAPI の事前診断や音声一覧を確認する場合:

```powershell
.\scripts\start-tts-service.ps1 -HealthJson
.\scripts\start-tts-service.ps1 -ListVoices
```

音を出さずにファイル出力へ切り替える場合:

```powershell
.\scripts\start-tts-service.ps1 -Player file
```

音声生成・再生を行わず、watcher と status 連携だけ確認する場合:

```powershell
.\scripts\start-tts-service.ps1 -Engine noop
```

Dify watcher から HTTP TTS source へ直接流す場合:

```powershell
.\scripts\start-tts-service.ps1 -Source http -Engine noop
.\scripts\start-dify-watch.ps1 -TtsChunkUrl http://127.0.0.1:8765/api/tts/chunk
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
