# Integration Contract

この文書は、モジュール間の接続契約だけを扱います。設計背景や検討履歴は置きません。

## Process Entry

通常の統合起動は workspace 直下から行う。

```powershell
.\start-home-control-stack.bat -StopExisting
.\status-home-control-stack.bat
.\stop-home-control-stack.bat
```

起動スクリプト本体は `sword-voice-agent\scripts\home-control-stack\` にある。root の `.bat` と `scripts\*.ps1` はショートカット。

## Camera Hub

| Item | Contract |
|---|---|
| Topic URL | `ws://127.0.0.1:8765` |
| Gesture topic | `/vision/sword_sign/state` |
| Camera status topic | `/camera/status` |
| Room light topic | `/vision/room_light/state` |
| Video display | MediaMTX WebRTC/HLS, usually `http://127.0.0.1:8889/cam0` |
| Inference input | Camera Hub reads MediaMTX RTSP with `ffmpeg-pipe` |

Camera Hub owns physical camera capture, frame reading, landmark inference, gesture inference, and room-light inference. Other modules subscribe to topics.

## Environment State Server

| Endpoint | Consumer | Notes |
|---|---|---|
| `GET /environment/current` | Dify | Requires Bearer token |
| `GET /environment/relations` | Dify | Related metadata only |
| `GET /indicators/current` | HUD / Cube / display-runtime | Loopback display-safe API |
| `GET /health` | launcher / checks | Diagnostics |
| `GET /ready` | launcher / checks | Fails when required state is stale |

Environment State Server subscribes to Camera Hub topics and Home Assistant bridge events. It does not open the camera.

## Dify

| Env | Meaning |
|---|---|
| `DIFY_BASE_URL` | Dify API base URL, for example `http://localhost:8080/v1` |
| `DIFY_API_KEY` | Dify app API key |
| `DIFY_USER` | conversation user |
| `DIFY_RESPONSE_MODE` | `streaming` or `blocking` |
| `ENVIRONMENT_STATE_URL` | Dify-side URL for `/environment/current` |
| `ENVIRONMENT_RELATIONS_URL` | Dify-side URL for `/environment/relations` |

When Dify runs in Docker on the same machine, use `host.docker.internal` for host services.

## AITuberKit

| Item | Contract |
|---|---|
| Projection URL | `http://127.0.0.1:3000/projection-visual` |
| Speech queue API | `POST /api/messages?clientId=<id>&type=direct_send` |
| Env from sword side | `AITUBER_MESSAGE_URL` |
| Message Receiver | Enable in AITuberKit with matching client ID |

Dify streaming responses are sent to AITuberKit through `direct_send`. AITuberKit external WebSocket mode is not part of the standard integration path.

## TTS Service

| Item | Contract |
|---|---|
| HTTP source | `http://127.0.0.1:8765` |
| Single request | `POST /api/tts` |
| Streaming chunk | `POST /api/tts/chunk` |
| Health | `GET /health` |
| Shutdown | `POST /shutdown` |
| Volume | `GET/POST /api/volume` |
| Status file | `latest_tts_state.json` under output status dir |

Dify watcher sends response chunks to `TTS_HTTP_CHUNK_URL` when TTS is enabled.

## Home Assistant Bridge

| Item | Contract |
|---|---|
| Health | `http://127.0.0.1:8787/health` |
| Action list | `GET /actions` |
| Preview | `POST /actions/{action_id}/preview` |
| Execute | `POST /actions/{action_id}/execute` |
| Auth | `HOME_CONTROL_API_TOKEN` |

Home Assistant bridge owns action safety, action tracking, and Home Assistant execution result interpretation.

## TouchDesigner

| Item | Contract |
|---|---|
| UDP trigger | `127.0.0.1:9001` |
| Payload | JSON with source, phase, action/result metadata |
| Display input | Projection Visual or Cube background URL |

TouchDesigner owns visual effect state. It does not decide Dify or Home Assistant state.

## Runtime Status

Long running services write runtime status files when launched by integration scripts. These files are for process management and status display, not authority for business state.

## Security

- Tokens and API keys are environment variables.
- URLs with embedded credentials are rejected.
- Loopback is the default bind policy.
- Remote use requires explicit opt-in and token/origin controls.
- Runtime logs, cache files, generated audio, and Dify payloads may contain local-sensitive data.
