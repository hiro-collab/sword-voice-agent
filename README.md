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
- [integration-contract.md](docs/integration-contract.md): 接続先、payload、ポート、認証。
- [module-responsibilities.md](docs/module-responsibilities.md): 各モジュールの責務境界。
- [state_authority.md](docs/state_authority.md): state、flag、ID の authority。
- [retired-paths.md](docs/retired-paths.md): 互換、保留、検証専用の導線。
- [module-maintainer-requests.md](docs/module-maintainer-requests.md): 別担当モジュールへ渡す文書整理依頼。

`archives/` は履歴退避先です。通常の実装判断では読まなくても大丈夫です。必要なときだけ履歴確認として参照します。

## Requirements

- Windows + PowerShell
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
  environment-state-server\
  home-assistant-server\
  aituber-kit\
  touchdesigner-ai-controller\
  system-house-renderer\
```

検証用モジュールは次で clone または pull できます。

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

`.env` では少なくとも次を確認します。

- `DIFY_BASE_URL`
- `DIFY_API_KEY`
- `MEDIAPIPE_SWORD_SIGN_MODEL_PATH`
- 各モジュールの `*_ROOT`
- `HOME_CONTROL_API_TOKEN`
- `AITUBER_MESSAGE_URL`

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

起動スクリプトの本体は `sword-voice-agent\scripts\home-control-stack\` にあります。`<workspace>` 直下の `.bat` と `scripts\*.ps1` はショートカットです。

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

Environment State Server の疎通確認:

```powershell
.\sword-voice-agent\scripts\home-control-stack\check-environment-state-server.ps1
```

## Model Notice

スクリーンショットやローカル表示例では、inotushop / inunoketu 様のオリジナル3Dモデル「アルバイ子のヌタチさん」を使用しています。このリポジトリでは VRM 本体を再配布しません。利用者は配布元から正規に入手し、利用規約を確認してください。
