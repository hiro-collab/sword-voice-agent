# Ops

`ops/` は、Sword Agent System の起動、停止、状態確認、manifest を扱う場所です。

system cell 直下の `.bat` は押しやすい入口です。実際の起動定義とprofile解決は、この `ops/` が担当します。

## 現在の入口

| 用途 | パス |
|---|---|
| profile対応の start/status/stop | `ops/scripts/system.ps1` |
| Home Control Stack supervisor | `ops/scripts/home-control-stack/` |
| profile定義 | `ops/manifests/profiles/` |
| service定義 | `ops/manifests/services/` |
| rootショートカット生成 | `ops/scripts/home-control-stack/install-root-shortcuts.ps1` |
| 互換wrapper | `scripts/home-control-stack/` |
| Launcher server | `tools/home-control-launcher/` |

新しい起動管理の作業は `ops/scripts/` と `ops/manifests/` に追加します。互換wrapperは、外部参照が残る間だけ維持します。

## よく使うコマンド

system cell 直下から使う場合です。

```powershell
cd <workspace>\sword-agent-os
.\start-home-control-stack.bat -Profile thought-core-v0
.\status-home-control-stack.bat -Profile thought-core-v0
.\stop-home-control-stack.bat -Profile thought-core-v0 -Force
```

control plane repo から詳細を見る場合です。

```powershell
cd <workspace>\sword-agent-os\control-plane\core
.\ops\scripts\system.ps1 start  -Profile thought-core-v0 -DryRun
.\ops\scripts\system.ps1 status -Profile thought-core-v0 -ManifestOnly
.\ops\scripts\system.ps1 stop   -Profile thought-core-v0 -DryRun
```

## Profiles

| Profile | 用途 |
|---|---|
| `thought-core-v0` | 現在の主経路。Thought Core API と watcher を使う。 |
| `camera-debug` | Camera Hub と Vision Snapshot Processor だけを確認する。 |
| `aituber-only` | AITuber Kit 表示だけを確認する。 |

## 別ポートでdry-runする例

既存スタックを止めずに起動引数だけ確認したいときに使います。

```powershell
.\ops\scripts\system.ps1 start `
  -Profile thought-core-v0 `
  -DryRun `
  -SkipVoicevoxCheck `
  -HomeAssistantBridgePort 18887 `
  -EnvironmentStatePort 18890 `
  -MediapipePort 18865 `
  -MediapipeBrowserMonitorPort 18870 `
  -VisionSnapshotProcessorPort 18876 `
  -AituberPort 13000 `
  -TouchDesignerGuiPort 18889 `
  -ThoughtCorePort 18888 `
  -StackStateDir .cache\home-control-stack-system-test
```

## 変更ルール

- 新しいサービスを起動対象にする場合は `ops/manifests/services/` に追加する。
- profileへの参加は `ops/manifests/profiles/` で管理する。
- layer、logical、memory_scope などの説明をmanifestに残す。
- rootの `.bat` は薄いショートカットに保つ。
- 起動に失敗したときの確認手順は、system cell の `README.md` に近い場所へ置く。
