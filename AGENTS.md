# Sword Voice Agent - Agent Rules

このファイルは、このリポジトリで AI コーディングエージェントが毎回守る恒久ルールです。

## Working Directory

- 作業ルートはこのリポジトリの内側ディレクトリです。
- 兄弟リポジトリは統合確認対象です。明示依頼がない限り、兄弟リポジトリの実装や文書を編集しません。
- `archives/` は履歴退避先です。通常の実装判断では検索しなくても大丈夫です。必要になった場合は履歴確認として読みます。

## Project Documents

- `README.md`: 起動と最短確認の入口。
- `docs/system-requirements.md`: 要求仕様、成功条件、前提。
- `docs/integration-contract.md`: モジュール間の接続契約。
- `docs/module-responsibilities.md`: 各モジュールの責務境界。
- `docs/state_authority.md`: state、flag、ID の authority。
- `docs/retired-paths.md`: 互換、保留、検証専用の導線。

## Architecture Rules

- `src/sword_voice_agent/core/`: 状態判定と制御ロジックだけを書く。HTTP、UDP、Dify、ファイルシステム、Web UI を直接知らない。
- `src/sword_voice_agent/protocol/`: モジュール間 JSON 契約の中心。既存フィールドを壊す変更はテストと契約文書の更新を伴う。
- `src/sword_voice_agent/application/`: gesture 入力、input gate 更新、録音制御、status store 更新などのユースケースをまとめる。
- `src/sword_voice_agent/adapters/`: Dify、ai-talk-core、UDP、HTTP、ファイルなど具体 I/O を置く。
- `src/sword_voice_agent/apps/`: CLI やサーバー起動など、core/protocol/adapters を組み合わせる薄い実行層にする。
- `src/sword_voice_agent/web/`: ローカル統合コンソールの静的 UI。API 形状を変える場合は `adapters/console_status.py` とテストも確認する。

## Implementation Rules

- Dify API key、外部サービスの secret、個人環境の token はコード、fixture、README 例に直書きしない。環境変数で扱う。
- JSON message type、reason、action 名を変える場合は後方互換性を意識し、影響範囲を明記する。
- flag、state、ID の authority を変える場合は `docs/state_authority.md` も更新する。
- 接続先、ポート、payload 形状を変える場合は `docs/integration-contract.md` も更新する。
- モジュール責務を変える場合は `docs/module-responsibilities.md` も更新する。
- 互換、検証専用、保留導線を残す場合は `docs/retired-paths.md` に短く書く。
- 挙動を変えたら、近い単体テストを追加または更新する。

## Standard Checks

PowerShell で次を実行する。

```powershell
.\scripts\check.ps1
```

直接実行する場合:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

## Review Focus

- `core` が外部 I/O に依存していないか。
- protocol の JSON 契約が既存 consumer を壊していないか。
- UDP/HTTP receiver の入力検証とエラー処理が十分か。
- Dify、ai-talk-core、AITuberKit 連携で secret やローカルパスをログに出しすぎていないか。
- 実機統合が必要な変更で、単体テストだけを根拠にしていないか。
