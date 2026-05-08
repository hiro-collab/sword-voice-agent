# sword-voice-agent

刀印ジェスチャーを入力ゲートにして、STT、Dify、Home Assistant、TTS、AITuberKit、TouchDesigner 表示をつなぐローカル統合アプリです。

```text
Camera Hub gesture topic
  -> sword-voice-agent input gate
  -> ai-talk-core STT / handoff
  -> Dify
  -> Home Assistant / TTS / AITuberKit / TouchDesigner
```

## Document Map

- [system-requirements.md](docs/system-requirements.md): 目的、成功条件、前提。
- [architecture.md](docs/architecture.md): workspace全体の論理構成。
- [component-map.md](docs/component-map.md): 現在のモジュールと将来境界の対応表。
- [service-boundary-map.md](docs/service-boundary-map.md): services/contracts/adapters/ops の現在地図。
- [migration-plan.md](docs/migration-plan.md): 大移動を避ける段階的な整理順。
- [logging-conventions.md](docs/logging-conventions.md): logs/events/status に入れる layer 意識。
- [start-stop-control-plan.md](docs/start-stop-control-plan.md): 全体起動・停止を `ops` レイヤーへまとめる計画。
- [integration-contract.md](docs/integration-contract.md): 接続先、payload、ポート、認証。
- [contracts/](contracts/README.md): thought-core turn/event stream の機械検証できる境界仕様。
- [policies/](policies/README.md): capability、memory scope、action approval の reviewed policy。
- [runtime-layout.md](docs/runtime-layout.md): `.cache` 互換 path と将来の `runtime/` 分類。
- [module-responsibilities.md](docs/module-responsibilities.md): 各モジュールの責務境界。
- [repository-sources.md](docs/repository-sources.md): GitHub保存先とローカル専用ファイル。
- [state_authority.md](docs/state_authority.md): state、flag、ID の authority。
- [retired-paths.md](docs/retired-paths.md): 互換、保留、検証専用の導線。

`archives/` は履歴退避先です。通常の実装判断では読まなくても大丈夫です。必要なときだけ履歴確認として参照します。
`ops/` は profile-aware な起動・停止・状態確認の入口です。起動 supervisor の実体は
`ops/scripts/home-control-stack/` に集約し、`scripts/home-control-stack/` は互換 wrapper として残しています。
`runtime/` は `.cache` 互換 path を将来分類するための足場です。
`tests/system/` は memory/access kernel など、単一アプリではなく OS 的な境界を
検査するテストの置き場です。

## Requirements

- Windows + PowerShell 7 (`pwsh`) for lifecycle scripts
- Python と `uv`
- Node.js / npm
- Chrome
- Web カメラとマイク
- Dify local API
- 必要に応じて Home Assistant、VOICEVOX、TouchDesigner

兄弟ディレクトリに次のモジュールを配置します。

```text
<workspace>\
  sword-voice-agent\
  ai-talk-core\
  mediapipe-sword-sign\
  tts-service\
  avatar-service\
  environment-state-server\
  home-assistant-server\
  aituber-kit\
  touchdesigner-ai-controller\
  system-house-renderer\
```

Git管理している兄弟モジュールは次で clone または pull できます。GitHubに入れない秘密値やローカル専用アセットは [repository-sources.md](docs/repository-sources.md) を参照してください。

```powershell
cd <workspace>\sword-voice-agent
.\scripts\setup-validation-modules.ps1 -DryRun
.\scripts\setup-validation-modules.ps1 -UpdateEnv
```

## Setup

```powershell
cd <workspace>\sword-voice-agent
uv sync
Copy-Item .env.example .env
notepad .env
```

`<workspace>\sword-voice-agent\.env` では少なくとも次を確認します。

- `DIFY_BASE_URL`
- `DIFY_API_KEY`
- `MEDIAPIPE_SWORD_SIGN_MODEL_PATH`
- 各モジュールの `*_ROOT`
- `AITUBER_MESSAGE_URL`

`HOME_CONTROL_API_TOKEN` や Dify Studio 側の `ENV` は、次の Environment Variables を参照します。

## Environment Variables

秘密値は Git に入れません。`DIFY_API_KEY` と `HOME_CONTROL_API_TOKEN` は別物です。Dify のアプリAPIを呼ぶ鍵が `DIFY_API_KEY`、Dify workflow からローカルの Home Assistant bridge / Environment State Server を呼ぶ鍵が `HOME_CONTROL_API_TOKEN` です。

| 書き込み場所 | 読むもの | 主な値 | 備考 |
|---|---|---|---|
| `<workspace>\sword-voice-agent\.env` | sword-voice-agent / dify watcher / thought-core / 診断スクリプト | `DIFY_BASE_URL`, `DIFY_API_KEY`, `THOUGHT_CORE_BASE_URL`, `THOUGHT_CORE_LLM_*` | Dify app key と Thought Core responder adapter を置く。`HOME_CONTROL_API_TOKEN` は通常ここではなく `home-assistant-server\.env`。 |
| `<workspace>\aituber-kit\.env` | AITuberKit Next.js API / Projection Visual | `NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE`, `THOUGHT_CORE_BASE_URL`, `NEXT_PUBLIC_THOUGHT_CORE_BASE_URL`, `DIFY_URL`, `DIFY_API_KEY`, `VOICEVOX_SERVER_URL` | Projection Visual の主経路は `thought-core` / `dify` で切り替える。Launcher 起動時は一部を自動注入する。 |
| `<workspace>\home-assistant-server\.env` | home-assistant-server / environment-state-server | `HOME_CONTROL_API_TOKEN`, `ENVIRONMENT_API_TOKEN`, `HOME_ASSISTANT_TOKEN` | `HOME_CONTROL_API_TOKEN` は32文字以上のランダム値。`ENVIRONMENT_API_TOKEN` は空なら同じ値を使う。 |
| Dify Studio のアプリ `ENV` | Dify workflow HTTP nodes | `HOME_CONTROL_API_TOKEN`, `ENVIRONMENT_STATE_URL`, `ENVIRONMENT_RELATIONS_URL`, `ENVIRONMENT_FEEDBACK_URL` | YAMLをインポートしても secret の実値は入らないため、公開前にDify画面で設定する。 |

Thought Core を主経路にする最小構成は次です。

```text
# <workspace>\sword-voice-agent\.env
THOUGHT_CORE_BASE_URL=http://127.0.0.1:18787
THOUGHT_CORE_LLM_ENABLED=true
THOUGHT_CORE_LLM_BASE_URL=https://api.openai.com/v1
THOUGHT_CORE_LLM_API_KEY=<llm_api_key>
THOUGHT_CORE_LLM_MODEL=gpt-4o-mini
```

```text
# <workspace>\aituber-kit\.env
NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE=thought-core
THOUGHT_CORE_BASE_URL=http://127.0.0.1:18787
NEXT_PUBLIC_THOUGHT_CORE_BASE_URL=http://127.0.0.1:18787
NEXT_PUBLIC_THOUGHT_CORE_SESSION_ID=aituber-kit
VOICEVOX_SERVER_URL=http://127.0.0.1:50021
```

```text
# <workspace>\home-assistant-server\.env
HOME_CONTROL_API_TOKEN=<32文字以上のランダム値>
ENVIRONMENT_API_TOKEN=
HOME_ASSISTANT_TOKEN=<Home Assistant long-lived access token>
```

Dify Studio の `ENV` は次を基準にします。

```text
HOME_CONTROL_API_TOKEN=<workspace>\home-assistant-server\.env と同じ値
ENVIRONMENT_STATE_URL=http://host.docker.internal:8790/environment/current
ENVIRONMENT_RELATIONS_URL=http://host.docker.internal:8790/environment/relations
ENVIRONMENT_FEEDBACK_URL=http://host.docker.internal:8790/feedback/state-query
```

`ENVIRONMENT_API_TOKEN` を Environment State Server 専用に分けることもできますが、現在の Dify YAML は `HOME_CONTROL_API_TOKEN` を使って Environment と Home Assistant bridge の両方を呼びます。分離する場合は Dify YAML 側の env/header も合わせて変更してください。

### Environment Checkpoints

1. Dify API key が正しいか:

```powershell
cd <workspace>
.\start-home-control-stack.bat -StopExisting
```

起動ログに次が出れば、`<workspace>\sword-voice-agent\.env` と `<workspace>\aituber-kit\.env` の Dify API key は通っています。

```text
[dify] DIFY_API_KEY valid ...
[aituber_kit] DIFY_API_KEY valid ...
```

2. Home Control token があるか:

起動ログに次が出れば、`<workspace>\home-assistant-server\.env` の `HOME_CONTROL_API_TOKEN` は読み込めています。

```text
[home_assistant_bridge] HOME_CONTROL_API_TOKEN present ...
[environment_state_server] API token present ...
```

3. Dify に最新YAMLと Environment が見えているか:

```powershell
cd <workspace>\sword-voice-agent
.\ops\scripts\home-control-stack\check-dify-home-control-workflow.ps1
```

正常時は `ok` になり、`version match: True` と `Dify sees env: ... room_light=True` が出ます。

```text
[hca-dify-check] ok
  version match:  True
  Environment:    direct_ok=True status=200 room_light=True
  Dify sees env:  status=200 room_light=True
```

代表的な診断結果:

| 表示 | 見る場所 |
|---|---|
| `workflow_version_mismatch` | `dify-apps\Home Control Assistant.issue-iteration.yml` をDifyへ再インポートし、公開する。 |
| `diagnostic_marker_missing` | Difyが古いYAMLを実行している可能性が高い。再インポート、公開、API keyの対象アプリを確認する。 |
| `dify_environment_state_not_visible` | Dify Studio の `ENV` で `HOME_CONTROL_API_TOKEN`, `ENVIRONMENT_STATE_URL`, `ENVIRONMENT_RELATIONS_URL` を確認する。 |
| `environment_room_light_missing` | Dify以前に Environment State Server / Vision Snapshot Processor 側を確認する。 |

AITuberKit は別アプリとして準備します。

```powershell
cd <workspace>\aituber-kit
npm install
```

## Start

`<workspace>` 直下から Home Control Stack を起動します。

```powershell
cd <workspace>
.\start-home-control-stack.bat -StopExisting
```

状態確認と停止:

```powershell
.\status-home-control-stack.bat
.\stop-home-control-stack.bat
```

起動系の正規入口は `ops` レイヤーです。まず dry-run で profile から supervisor 引数へ
どう変換されるか確認できます。

```powershell
cd <workspace>\sword-voice-agent
.\ops\scripts\system.ps1 start  -Profile thought-core-v0 -DryRun
.\ops\scripts\system.ps1 status -Profile thought-core-v0
.\ops\scripts\system.ps1 stop   -Profile thought-core-v0 -DryRun
```

起動スクリプトの本体は `sword-voice-agent\ops\scripts\home-control-stack\` にあります。
`<workspace>` 直下の `.bat` と `sword-voice-agent\scripts\home-control-stack\` は互換入口です。

## User Surface

AITuberKit を起動し、Projection Visual を開きます。

```powershell
cd <workspace>\aituber-kit
npm run dev
```

```text
http://127.0.0.1:3000/projection-visual
```

Chrome のマイク権限を許可します。Projection Visual では、STT、Gesture Sensor、Home Assistant、Dify、TTS、TouchDesigner 連携状態を確認します。

## Main Endpoints

| Surface | URL |
|---|---|
| AITuberKit Projection Visual | `http://127.0.0.1:3000/projection-visual` |
| ai-talk-core Web UI | `http://127.0.0.1:8000` |
| Environment State Server | `http://127.0.0.1:8790` |
| Home Assistant bridge | `http://127.0.0.1:8787` |
| TTS HTTP source | `http://127.0.0.1:8765` |
| Camera Hub topics | `ws://127.0.0.1:8765` |
| Vision Snapshot Processor topics | `ws://127.0.0.1:8776` |
| MediaMTX browser video | `http://127.0.0.1:8889/cam0` |

## Checks

```powershell
cd <workspace>\sword-voice-agent
.\scripts\check.ps1
```

Home Control Stack の失敗注入を含む確認:

```powershell
cd <workspace>
.\scripts\run-home-control-fault-e2e.ps1 -NoOpenBrowser -DelayBetweenCasesSeconds 1
```

Dify に最新 workflow YAML が反映されているか、また Dify 実行時に Environment State Server の `state_queries.room_light` が見えているかを確認:

```powershell
cd <workspace>\sword-voice-agent
.\ops\scripts\home-control-stack\check-dify-home-control-workflow.ps1
```

`workflow_version_mismatch` または `diagnostic_marker_missing` の場合は、`dify-apps\Home Control Assistant.issue-iteration.yml` をDifyへ再インポートし、公開してから再実行します。

Environment State Server の疎通確認:

```powershell
.\sword-voice-agent\ops\scripts\home-control-stack\check-environment-state-server.ps1
```

## Model Notice

スクリーンショットやローカル表示例では、inotushop / inunoketu 様のオリジナル3Dモデル「アルバイ子のヌタチさん」を使用しています。このリポジトリでは VRM 本体を再配布しません。利用者は配布元から正規に入手し、利用規約を確認してください。
