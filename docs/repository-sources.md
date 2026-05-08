# Repository Sources

この system cell は複数の Git-managed organ repo と、1つの control plane repo で構成します。
環境移行時はソースコードを GitHub から復元し、秘密値や再配布できないアセットはローカルで別途用意します。

## Cell Layout

| Cell path | Repository | Notes |
|---|---|---|
| `sword-control-plane` | `https://github.com/hiro-collab/sword-voice-agent.git` | Control plane: docs, contracts, ops, policies, tests, thought-core v0 |
| `organs/voice/ai-talk-core` | `https://github.com/hiro-collab/ai-talk-core.git` | STT / handoff core |
| `organs/reflex/mediapipe-sword-sign` | `https://github.com/hiro-collab/mediapipe-sword-sign.git` | Camera Hub / gesture topics |
| `organs/expression/tts-service` | `https://github.com/hiro-collab/tts-service.git` | Local TTS HTTP source |
| `organs/expression/avatar-service` | `https://github.com/hiro-collab/avatar-service.git` | Standalone VRM avatar runtime |
| `organs/environment/environment-state-server` | `https://github.com/hiro-collab/environment-state-server.git` | Environment snapshot API for Dify and displays |
| `organs/environment/vision-snapshot-processor` | local/Git-managed module | Low-frequency vision snapshot input |
| `organs/action/home-assistant-server` | `https://github.com/hiro-collab/home-assistant-server.git` | Home Assistant safety bridge |
| `organs/expression/aituber-kit` | `https://github.com/hiro-collab/aituber-kit-sword.git` | Local fork of official AITuber Kit; keep `origin` for upstream if needed |
| `organs/display/touchdesigner-ai-controller` | `https://github.com/hiro-collab/touchdesigner-ai-controller.git` | Display runtime GUI and TouchDesigner bridge |
| `organs/diagnostics/system-house-renderer` | `https://github.com/hiro-collab/system-house-renderer.git` | System visualization renderer |

Use `sword-control-plane\scripts\setup-validation-modules.ps1` from the control plane repo to clone or fast-forward these organ modules.

## Local-only files

Do not commit these files or directories:

- `.env` and `.env.*`
- `organs\action\home-assistant-server\config\home-control.yaml`
- Dify Studio app `ENV` values
- API keys, Home Assistant tokens, local auth tokens, or credentials
- `organs\expression\aituber-kit\public\scripts\live2dcubismcore.min.js`
- custom or redistribution-sensitive VRM files, including `organs\expression\aituber-kit\public\vrm\Nutachisan.vrm` and `organs\reflex\mediapipe-sword-sign\.vrm\`
- `.cache\`, `runtime\`, `local\`, `logs\`, `archives\`, `.venv\`, `node_modules\`, and build outputs
- root shortcut files generated from `sword-control-plane\ops\scripts\home-control-stack\install-root-shortcuts.ps1`
- local SDK unpack directories under `external\`, such as `external\CubismSdkForWeb-5-r.5\`

When a local-only asset is required for a feature, document the expected path and acquisition/setup step instead of committing the file itself.
