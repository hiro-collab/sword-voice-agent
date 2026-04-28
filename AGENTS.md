# Sword Voice Agent - AI Development Guide

このファイルは、このリポジトリで AI コーディングエージェントが毎回守るプロジェクトルールです。

## Working Directory

- 作業ルートは `<repo_root>`、つまりこのリポジトリのルート。
- 外側の `<workspace>\sword-voice-agent` には `src` がないため、コマンド実行場所を間違えない。
- 兄弟リポジトリの `ai_talk_core` と `mediapipe_test` は統合確認対象。明示依頼がない限り、このリポジトリから編集しない。

## Architecture Rules

- `src/sword_voice_agent/core/`: 状態判定と制御ロジックだけを書く。HTTP、UDP、Dify、ファイルシステム、Web UI を直接知らない。
- `src/sword_voice_agent/protocol/`: モジュール間 JSON 契約の中心。既存フィールドを壊す変更はテストと README の更新を伴う。
- `src/sword_voice_agent/application/`: gesture入力、input gate更新、録音制御、status store更新などのユースケースをまとめる。外部 I/O の具体処理は adapters に残す。
- `src/sword_voice_agent/adapters/`: Dify、ai_talk_core、UDP、HTTP、ファイルなど具体 I/O を置く。
- `src/sword_voice_agent/apps/`: CLI やサーバー起動など、core/protocol/adapters を組み合わせる薄い実行層にする。
- `src/sword_voice_agent/web/`: ローカル統合コンソールの静的 UI。API 形状を変える場合は `adapters/console_status.py` とテストも確認する。

## Implementation Rules

- Dify API key、外部サービスの secret、個人環境の token はコード、fixture、README 例に直書きしない。環境変数で扱う。
- 既存の JSON message type、reason、action 名を変える場合は後方互換性を意識し、影響範囲を明記する。
- flag、state、ID の authority を変える場合は `docs/state_authority.md` も更新する。
- 挙動を変えたら、近い単体テストを追加または更新する。
- 統合手順やコマンド引数を変えたら README も更新する。
- 日本語の README と UI 文言は、利用者がローカル統合確認で迷わない具体性を優先する。

## Standard Checks

PowerShell で次を実行する。

```powershell
.\scripts\check.ps1
```

中身は次の確認を行う。

- `src` と `tests` 配下の Python 構文チェック
- `PYTHONPATH=src` での unittest 全件実行

直接実行する場合:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

## Agent Memory, Notes, and Logs

- 恒久ルールはこの `AGENTS.md` にだけ書く。
- 将来の作業者が知るべき判断や前提は `docs/agent-memory.md` に短く残す。
- flag、state、ID の authority は `docs/state_authority.md` に書く。`agent-memory.md` に同じ表を重複させない。
- 進行中タスクの引き継ぎメモは `.cache/codex/shared-note.md` に書く。作業完了後は最新状態だけに短く保つ。
- 詳細な作業履歴は `.cache/codex/agent-runs.jsonl` に JSON Lines で追記する。
- アプリ実行イベントは `.cache/sword_voice_agent/events.jsonl` に残す。Codex作業ログと混ぜない。

## Review Focus

- `core` が外部 I/O に依存していないか。
- protocol の JSON 契約が既存 consumer を壊していないか。
- UDP/HTTP receiver の入力検証とエラー処理が十分か。
- Dify/ai_talk_core 連携で secret やローカルパスをログに出しすぎていないか。
- 実機統合が必要な変更では、単体テストだけで確認済みと断言していないか。
