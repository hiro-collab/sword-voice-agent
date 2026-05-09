# sword-control-plane

`sword-control-plane` は、Sword Agent System の制御盤です。

ここには、AI身体OSを安全に動かすための設計、契約、権限、起動定義、テスト、小さな共有部品を置きます。  
音声、MediaPipe、AITuber Kit、Home Assistant、TouchDesigner などの大きな実体は、このrepoへ吸収せず、system cell の `organs/` に置きます。

![Projection Visual 標準状態](docs/images/標準状態.jpg)

この画面は、control plane が束ねている system cell の状態を人間に見せる代表的な表示です。中央のアバターが会話し、HUDが器官、反射、状態推定、家電操作、表示連携を示します。

```text
C:\Users\kawai\works\sword-agent-system\
  sword-control-plane\   # このrepo。制御盤
  organs\                # 実体repo。声、反射、認識、手足、表現、表示
```

## このrepoの役割

| 領域 | 役割 |
|---|---|
| `docs/` | 設計、判断、移行方針、運用ルール |
| `contracts/` | organ間のAPI、イベント、tool境界 |
| `policies/` | capability、memory scope、action approval |
| `catalogs/` | 家電操作などのカタログ |
| `ops/` | 起動、停止、状態確認、manifest |
| `services/thought-core/` | 通常思考を担当する v0 service |
| `src/sword_voice_agent/` | control plane用の小さな共有部品 |
| `tests/` | contract、policy、memory/access、integration検査 |
| `local/` | 開発用のlocalデータ置き場。実データは原則Git管理しない |
| `runtime/` | 将来のruntime出力置き場。実データは原則Git管理しない |

## コンセプト

このシステムは、単一アプリではなく、複数の器官を持つローカルAI身体OSとして扱います。

```text
reflex-core
  MediaPipeやVADなど、LLMを待たない速い反射

thought-core
  1 turn単位の通常思考、tool選択、再観測、応答

environment-server
  部屋、カメラ、家電、表示系の状態観測

home-control-server
  Home Assistantなどを通した単発操作

expression
  AITuber Kit、TTS、TouchDesigner、HUD、背景表示

ops
  起動、停止、PID、health、manifest
```

`contracts/` は system call のような境界仕様、`policies/` は権限ルール、`ops/` は init system のような役割です。

## よく使うコマンド

### system cell から起動する

通常運用では、外側の system cell 直下で `.bat` を使います。

![Sword System Launcher](docs/images/launcher.png)

GUIの Launcher も同じ起動系を使います。CLIとGUIで別々の起動ルールを持たないよう、起動定義は `ops/manifests/` に集約します。

```powershell
cd C:\Users\kawai\works\sword-agent-system
.\start-home-control-stack.bat -Profile thought-core-v0
.\status-home-control-stack.bat -Profile thought-core-v0
.\stop-home-control-stack.bat -Profile thought-core-v0 -Force
```

## 画面イメージ

### Projection Visual

AITuber Kit の投影・配信用画面です。アバター、HUD、入力欄、system cell の状態をまとめて表示します。

![Projection Visual 標準状態](docs/images/標準状態.jpg)

### 通常会話

Thought Core の応答、発話、turn trace が表示されます。

![通常会話](docs/images/通常会話.jpg)

### 家電操作時

家電操作時は、操作要求、実行、再観測、結果確認の流れを HUD に出します。

![家電操作時](docs/images/家電操作時.jpg)

### 刀印ジェスチャー

MediaPipe Camera Hub は、刀印とカメラ状態を reflex layer の入力として配信します。

![刀印ジェスチャー](docs/images/sword-sign-gesture.png)

### control plane からdry-runする

実際に起動せず、どのサービスがどの引数で起動されるか確認できます。

```powershell
cd C:\Users\kawai\works\sword-agent-system\sword-control-plane
.\ops\scripts\system.ps1 start -Profile thought-core-v0 -DryRun
.\ops\scripts\system.ps1 status -Profile thought-core-v0 -ManifestOnly
```

### テストする

```powershell
cd C:\Users\kawai\works\sword-agent-system\sword-control-plane
uv run python -m unittest discover -s tests
```

一部だけ確認したい場合です。

```powershell
uv run python -m unittest tests.test_contract_schemas tests.test_ops_manifests
```

## 初回セットアップ

```powershell
cd C:\Users\kawai\works\sword-agent-system\sword-control-plane
uv sync
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

主に確認する値です。

| 変数 | 用途 |
|---|---|
| `THOUGHT_CORE_BASE_URL` | Thought Core API。通常は `http://127.0.0.1:18787` |
| `THOUGHT_CORE_LLM_BASE_URL` | OpenAI互換APIのURL |
| `THOUGHT_CORE_LLM_API_KEY` | LLM API key |
| `THOUGHT_CORE_LLM_MODEL` | 使用モデル |
| `THOUGHT_CORE_PERSONA` | 応答口調。既定は `cheerful_ossan` |
| `AITUBER_MESSAGE_URL` | AITuber Kitへ発話イベントを送るURL |

Home Assistant や Environment State Server の秘密情報は、基本的に organ 側の `.env` に置きます。

```text
C:\Users\kawai\works\sword-agent-system\organs\action\home-assistant-server\.env
```

## 起動profile

| Profile | 用途 |
|---|---|
| `thought-core-v0` | 現在の主経路。Thought Core API と watcher を使う |
| `thought-core-experimental` | 旧名の互換エイリアス。新しい手順では `thought-core-v0` を使う |
| `camera-debug` | Camera Hub と Vision Snapshot Processor だけを見る |
| `aituber-only` | AITuber Kit 表示だけを見る |
| `full-local` | Dify互換を含む旧寄りの構成。通常は使わない |

## 重要な文書

| 文書 | 内容 |
|---|---|
| `docs/architecture.md` | 全体アーキテクチャ |
| `docs/deployment-cell.md` | system cell / control plane / organ の考え方 |
| `docs/component-map.md` | 現在の物理配置と論理役割 |
| `docs/module-responsibilities.md` | 各器官の責務 |
| `docs/state_authority.md` | 状態推定、authority、source of truth |
| `docs/action-driver-catalog.md` | 家電操作カタログと更新フロー |
| `docs/runtime-layout.md` | `.cache`, `runtime`, `local` の使い分け |
| `docs/logging-conventions.md` | layerを意識したログの書き方 |
| `docs/retired-paths.md` | 退役済み・互換用パス |

`archives/` は履歴退避先です。現役の設計判断には使いません。

## 家電操作の管理

家電操作の意味は control plane が管理します。

```text
catalogs/actions/home-actions.json
  action_id、aliases、risk、confirmation、expected_state

organs/action/home-assistant-server/
  実際のHome Assistant実行

organs/environment/environment-state-server/
  現在状態と使える操作の投影

services/thought-core/
  1 turnの中で観測、preview、execute、再観測、評価
```

既存 `action_id` の意味を変えるのは互換性破壊です。原則として、新しい `action_id` を追加し、古いものを段階的に退役させます。

## メモリと状態

このrepoでは、メモリをAIの会話履歴だけとして扱いません。状態、作業記憶、イベントログ、長期記憶、設定、秘密情報を分けます。

| 層 | 内容 | 主な場所 |
|---|---|---|
| M1 | module state | `runtime/state/`, `.cache/` |
| M2 | core working memory | Thought Core内部 |
| M3 | event journal | `runtime/logs/events/`, `.cache/` |
| M4 | semantic / episodic memory | `local/memory/` |
| M5 | config / policy | `local/config/`, `policies/` |
| M6 | secrets | `.env`, `local/secrets/` |

重要なルールです。

- 各サービスは自分のstateだけを書く。
- Thought Core は記憶候補を作れるが、確定記憶を直接commitしない。
- secrets は memory や event log に混ぜない。
- 重要な操作には trace_id / turn_id を残す。

## Thought Core

`services/thought-core/` は、通常会話と家電操作の中心です。

主な流れです。

```text
turn input
  -> memory.retrieve
  -> environment.observe
  -> home.preview
  -> home.execute
  -> environment.observe
  -> evaluate
  -> response
```

`home.execute` の中に意味レベルのretryは隠しません。再観測、成功判定、再試行、ユーザー確認は Thought Core が turn の中で扱います。

## Difyについて

Dify は現在の主経路ではありません。過去ワークフローとの比較、外部互換、検証用として残っています。

関連ファイルは `dify-apps/` と一部の互換manifestにあります。通常の起動確認では、まず `thought-core-v0` を使ってください。

## TouchDesigner投影

TouchDesigner本体のプロジェクトは system cell 側にあります。

```text
C:\Users\kawai\works\sword-agent-system\organs\display\touchdesigner-ai-controller\touchdesigner\20260501AITuber.toe
```

control plane の起動スクリプトは、TouchDesigner制御GUIとUDP送信側を起動します。TouchDesigner本体やプロジェクター出力設定は、実機側で手動確認します。

## 変更するときの考え方

- 大きなorgan repoを `sword-control-plane` に吸収しない。
- 新しい境界は、まず `contracts/` と `docs/` に書く。
- 起動対象を増やすときは `ops/manifests/` を更新する。
- 権限や家電操作の意味を変えるときは `policies/` と `catalogs/` を更新する。
- runtimeやlocalの実データをGitに入れない。
- UIやHUDを変えたら、ブラウザで実画面を確認する。

## 参考にしたREADME方針

このREADMEは、最初に概要を示し、必要なもの、導入、起動、動作確認、トラブルシューティングの順に読めるように整理しています。詳細な背景や設計判断は `docs/` を参照してください。

## 謝辞と外部モジュールについて

Sword Agent System は、AITuber Kit、MediaPipe、Home Assistant、VOICEVOX、TouchDesigner など、複数の外部モジュールやアプリケーションの力を借りて動いています。control plane はそれらを一つのsystem cellとして接続・管理するためのrepoです。

利用時は、各プロジェクトのライセンス、利用規約、配布条件を確認してください。アバターやSDKなど再配布に注意が必要な資材は、system cell 側の `external/` や各organ repoの案内に従って扱います。
