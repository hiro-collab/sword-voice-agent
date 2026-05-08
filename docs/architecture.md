# Architecture

この workspace は、音声入力、思考、家電操作、環境認識、表現、診断を組み合わせる
ローカル agent system です。現時点では複数の sibling module と
`sword-voice-agent` 内の実験実装が混在しています。この文書では、今後の整理で使う
論理構成を定義します。

`sword-voice-agent` repository は、当面の workspace-level docs と contracts の
正規管理場所です。workspace root 直下に一時的な `docs/` や `contracts/` が存在する
場合でも、root 自体が Git 管理されるまでは、この repository 側を canonical とします。

コード移動や起動経路の変更は、この文書だけでは行いません。既存の標準起動は
引き続き workspace 直下の `start-home-control-stack.bat` から行います。

## Logical Layout

```text
apps/
  sword-voice-agent
  aituber-ui
  avatar-ui
  projection-ui
  launcher-ui

services/
  reflex-core
  thought-core
  deep-core
  environment-server
  home-control-server
  expression-core
  memory-core

adapters/
  dify
  openai-compatible
  langgraph
  home-assistant
  mediapipe
  stt
  tts
  aituber-kit
  touchdesigner

contracts/
  turn
  events
  environment
  home-control
  expression

runtime/
  logs
  state
  pids
  cache
  diagnostics

ops/
  scripts
  manifests
  process-registry
```

This is a logical map, not a required immediate directory layout.

## Responsibility Layers

`apps` are close to humans and displays. They own local UI, microphone surfaces,
projection views, launcher screens, and avatar presentation.

`services` are system-owned capabilities that may run as local processes or may
start as in-process modules. A service name does not require a separate HTTP
server until the boundary needs one.

`adapters` isolate external systems, frameworks, and module-specific protocols.
An adapter may live inside a service while it has only one consumer. It should
move to a shared adapter package only when multiple services need it.

`contracts` hold boundary definitions that should remain stable while
implementations change.

`runtime` holds generated files: logs, state snapshots, PID files, caches, and
diagnostics.

`ops` holds launch, stop, status, manifest, and process registry concerns.

## Core Services

| Logical service | Purpose | Current implementation |
|---|---|---|
| `reflex-core` | Fast reactions that do not wait for an LLM | `mediapipe-sword-sign` plus gesture gate policy in this repo. |
| `thought-core` | One-turn reasoning, tool choice, response shaping | `sword-voice-agent/services/thought-core`. |
| `deep-core` | Long-running analysis, research, review, planning | Future boundary only. |
| `environment-server` | Observes world and module state | `environment-state-server`. |
| `home-control-server` | Executes approved actions | `home-assistant-server`. |
| `expression-core` | Routes speech, display, motion, emotion, log events | `tts-service`, `aituber-kit`, avatar/display modules. |
| `memory-core` | Stores, summarizes, searches, and retrieves memory | Future boundary until a single authority exists. |

## Key Boundary Rules

- `thought-core` must not become a catch-all folder. It owns turn reasoning and
  orchestration, not camera capture, display rendering, or long-term runtime
  storage.
- `environment-server` observes. It should not execute home actions.
- `home-control-server` executes one request to the action boundary. Retry,
  re-observation, evaluation, and user feedback decisions belong above it.
- `Home Assistant` is an external platform. The system-facing API is
  `home-control-server`; the Home Assistant client itself is an adapter.
- Runtime files should not become design inputs. If a generated log or state
  file becomes part of a contract, document the contract separately.

## Existing Source Documents

Detailed integration docs live under this repository's `docs/` directory:

- `module-responsibilities.md`
- `integration-contract.md`
- `state_authority.md`
- `system-requirements.md`
