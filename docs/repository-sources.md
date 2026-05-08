# Repository Sources

このワークスペースは複数の兄弟リポジトリで構成します。環境移行時は、ソースコードをGitHubから復元し、秘密値や再配布できないアセットはローカルで別途用意します。

`sword-voice-agent` は、workspace root が Git 管理の meta repository になるまで、
workspace-level docs と contracts の正規管理場所でもあります。root 直下に一時的な
`docs\`、`contracts\`、`ops\`、`runtime\` が存在する場合でも、commit 済みの正は
この repository 側です。

## Git-managed modules

| Directory | Repository | Notes |
|---|---|---|
| `sword-voice-agent` | `https://github.com/hiro-collab/sword-voice-agent.git` | Home Control Stack orchestration, Dify workflow, docs |
| `ai-talk-core` | `https://github.com/hiro-collab/ai-talk-core.git` | STT / handoff core |
| `mediapipe-sword-sign` | `https://github.com/hiro-collab/mediapipe-sword-sign.git` | Camera Hub / gesture topics |
| `tts-service` | `https://github.com/hiro-collab/tts-service.git` | Local TTS HTTP source |
| `avatar-service` | `https://github.com/hiro-collab/avatar-service.git` | Standalone VRM avatar runtime |
| `environment-state-server` | `https://github.com/hiro-collab/environment-state-server.git` | Environment snapshot API for Dify and displays |
| `home-assistant-server` | `https://github.com/hiro-collab/home-assistant-server.git` | Home Assistant safety bridge |
| `aituber-kit` | `https://github.com/hiro-collab/aituber-kit-sword.git` | Local fork of official AITuber Kit; keep `origin` for upstream if needed |
| `touchdesigner-ai-controller` | `https://github.com/hiro-collab/touchdesigner-ai-controller.git` | Display runtime GUI and TouchDesigner bridge |
| `system-house-renderer` | `https://github.com/hiro-collab/system-house-renderer.git` | System visualization renderer |

Use `sword-voice-agent\scripts\setup-validation-modules.ps1` from the `sword-voice-agent` repo to clone or fast-forward these sibling modules.

## Local-only files

Do not commit these files or directories:

- `.env` and `.env.*`
- `home-assistant-server\config\home-control.yaml`
- Dify Studio app `ENV` values
- API keys, Home Assistant tokens, local auth tokens, or credentials
- `aituber-kit\public\scripts\live2dcubismcore.min.js`
- custom or redistribution-sensitive VRM files, including `aituber-kit\public\vrm\Nutachisan.vrm` and `mediapipe-sword-sign\.vrm\`
- `.cache\`, `logs\`, `archives\`, `.venv\`, `node_modules\`, and build outputs
- root shortcut files generated from `sword-voice-agent\scripts\home-control-stack\install-root-shortcuts.ps1`
- temporary workspace-root planning folders such as `docs\`, `contracts\`,
  `ops\`, and `runtime\`, unless a root meta repository is created
- local SDK unpack directories such as `CubismSdkForWeb-5-r.5\`

When a local-only asset is required for a feature, document the expected path and acquisition/setup step instead of committing the file itself.
