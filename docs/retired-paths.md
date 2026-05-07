# Retired Paths

この文書は、残してよいが通常の統合導線では使わないものを短く記録する場所です。

## Legacy Full Stack

`sword-voice-agent\scripts\start-full-stack.ps1` and `start-full-stack-supervisor.ps1` bundle ai-talk-core, UDP receiver, TTS service, Avatar service, and console in the older layout.

Use this only for compatibility checks or when specifically debugging that path. The standard start flow is Home Control Stack.

## MediaPipe UDP Publisher

`mediapipe-sword-sign\apps\publish_udp.py` sends gesture JSON directly over UDP. It remains useful for isolated receiver tests and older integrations. The standard flow uses Camera Hub WebSocket topics.

## `serve_websocket.py` Direct JSON

`mediapipe-sword-sign\apps\serve_websocket.py` publishes direct gesture JSON. It remains useful for older AITuberKit gesture voice bridge tests. The standard flow uses Camera Hub topic envelopes.

## Python JPEG Topic

Camera Hub can publish JPEG frames from Python for debugging. Do not use it as the normal video path. Use MediaMTX for browser video and Camera Hub topics for state overlays.

## AITuberKit External WebSocket Mode

AITuberKit can connect to a fixed external WebSocket endpoint. This was evaluated, but the standard speech path is `POST /api/messages?...type=direct_send`.

## AITuberKit Renderer API Proposal

A custom renderer API for structured `avatar_state` was considered. It remains a design option, not an implemented integration contract.

## TTS Status File Source

`tts-service` can poll `latest_dify_response.json`. The standard integration uses the HTTP source and streaming chunk endpoint.

## Archived Markdown

Detailed setup logs, review notes, worker coordination notes, and evaluation documents were moved under `archives/legacy-md/2026-05-07-doc-rebuild/`. They are history, not requirements.
