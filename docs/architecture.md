# Architecture

この workspace は、音声入力、思考、家電操作、環境認識、表現、診断を組み合わせる
ローカル agent system です。現時点では複数の sibling module と
`sword-voice-agent` 内の v0 service 実装が混在しています。この文書では、今後の整理で使う
論理構成を定義します。

## 誰の、どんな願いのためか

第一の利用者は、このPCの前で話し、身振りをし、画面を見て、必要なら家電や
外部表現を使いたい人です。Codex、保守者、Launcher、各種テストのためのシステムでは
ありません。利用者の自然な願いを受け取り、意味が曖昧なら確認し、Thought Coreが
考え、同じ応答を声・アバター・映像に表し、許可された能力だけを実行し、その結果を
再観測して訂正できる一つのローカルAI身体を目指します。

入口の機器やUIは交換可能です。刀印は現在ある一つの反射アダプターであり、入力権限の
唯一の正本ではありません。ジェスチャー、TouchDesigner、ローカルWebツール、物理ボタン
などは、それぞれの信号を同じ `input_enabled` 状態へ変換します。実際にマイク入力を
受け付けるかは `ai-talk-core` の Input Gate、入力の意味は Thought Core、起動・停止は
Launcher/Supervisorが所有します。この分離により、入口を増やしても会話経路を増殖させません。

`sword-voice-agent` repository は、当面の workspace-level docs と contracts の
正規管理場所です。workspace root 直下に一時的な `docs/` や `contracts/` が存在する
場合でも、root 自体が Git 管理されるまでは、この repository 側を canonical とします。

既存の標準起動は引き続き workspace 直下の `start-home-control-stack.bat` から行えます。
構成整理用の入口として、`control-plane/core/ops/scripts/system.ps1` も
`start/status/stop -Profile <profile>` を受け付けます。supervisor 実体は
`ops/scripts/home-control-stack/` に集約しています。旧
`scripts/home-control-stack/` の転送 wrapper は、root shortcut を現行入口へ
切り替えたため削除済みです。

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
  openai-compatible
  thought-core-internal-provider
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
  tools
  reflex
  environment
  home-control
  expression
  memory
  access-control

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

The useful mental model is close to an operating system. `contracts/` are the
system-call-like boundary, `services/` are kernel-style capabilities,
`adapters/` are drivers, `ops/` is init/process management, and `runtime/` is
the generated state/log area. `docs/decisions/` records ADR-style decisions.

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

## Memory Layers

Memory management is wider than long-term AI memory. It separates fast signals,
current state, core working memory, append-only journals, durable memory,
human-managed configuration, policy, and secrets.

| Layer | Name | Speed / size | Owner pattern | Current / target location |
|---|---|---|---|---|
| `M0` | Raw signal buffer | streaming, high-volume, very short TTL | sensing modules only | Camera/audio buffers inside reflex/environment inputs |
| `M1` | Module state | snapshot, small to medium | each service writes only its own state | `.cache/...`, future `runtime/state/` |
| `M2` | Core working memory | turn/task-local, small to medium | reflex/thought/deep cores | in-process, optional checkpoint |
| `M3` | Event journal | append-heavy, growing | services append facts | `.cache/.../*.jsonl`, future `runtime/logs/events/` |
| `M4` | Semantic / episodic memory | indexed retrieval, durable | `memory-core` commits | future `local/memory/` |
| `M5` | Config / policy | low-write, reviewed | humans / ops-managed policy | `.env.example`, future `local/config/`, `policies/` |
| `M6` | Secrets | isolated, tiny | ops/adapter runtime only | `.env`, OS secret store, local-only secret files |

The rule of thumb is: high-speed layers stay short-lived and local; meaning-rich
layers are summarized, indexed, and permissioned. `thought-core` should retrieve
selected M4 facts or summaries, not scan raw M0 signals or full M3 journals.

## Core Services

| Logical service | Purpose | Current implementation |
|---|---|---|
| `reflex-core` | Fast reactions that do not wait for an LLM | `mediapipe-sword-sign` plus gesture gate policy in this repo. |
| `thought-core` | One-turn reasoning, tool choice, response shaping | `control-plane/core/services/thought-core/src/thought_core`. |
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
- `memory-core` is the commit authority for M4. Other cores may create memory
  candidates, but should not directly commit long-term memory.
- Config, policy, and secrets are not learned memory. They stay separate from
  M4 and require explicit human/ops control.

## Existing Source Documents

Detailed integration docs live under this repository's `docs/` directory:

- `module-responsibilities.md`
- `service-boundary-map.md`
- `integration-contract.md`
- `state_authority.md`
- `system-requirements.md`
