# 0006 System Cell Rename

## Status

Accepted

## Context

The outer workspace had become a deployment unit, while the inner
`sword-voice-agent` repository held control-plane concerns. Sharing the same
name made it unclear which layer owned docs, contracts, runtime data, and organ
repos.

## Decision

Rename the outer workspace to `sword-agent-system` and the inner control-plane
repo directory to `sword-control-plane`.

Move large sibling modules under `organs/<layer>/...`:

- `organs/voice/ai-talk-core`
- `organs/reflex/mediapipe-sword-sign`
- `organs/environment/environment-state-server`
- `organs/environment/vision-snapshot-processor`
- `organs/action/home-assistant-server`
- `organs/expression/aituber-kit`
- `organs/expression/tts-service`
- `organs/expression/avatar-service`
- `organs/display/touchdesigner-ai-controller`
- `organs/diagnostics/system-house-renderer`

Keep `external/` for third-party SDKs only.

## Consequences

Root shortcuts remain the user-facing entry points. Control-plane scripts and
service manifests must resolve organ paths through the new system-cell layout.
`.cache/home-control-stack` remains the compatibility runtime path until a later
runtime/local migration.
