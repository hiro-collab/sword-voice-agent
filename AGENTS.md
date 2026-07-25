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

- `src/sword_voice_agent/core/`: 状態判定と制御ロジックだけを書く。HTTP、UDP、Thought Core の具体 provider、ファイルシステム、Web UI を直接知らない。
- `src/sword_voice_agent/protocol/`: モジュール間 JSON 契約の中心。既存フィールドを壊す変更はテストと契約文書の更新を伴う。
- `src/sword_voice_agent/application/`: gesture 入力、input gate 更新、録音制御、status store 更新などのユースケースをまとめる。
- `src/sword_voice_agent/adapters/`: ai-talk-core、Thought Core API、UDP、HTTP、ファイルなど具体 I/O を置く。
- `src/sword_voice_agent/apps/`: CLI やサーバー起動など、core/protocol/adapters を組み合わせる薄い実行層にする。
- `src/sword_voice_agent/web/`: ローカル統合コンソールの静的 UI。API 形状を変える場合は `adapters/console_status.py` とテストも確認する。

## Product Invariant: Agentic Intent And Response

この要件は Thought Core の通常会話、家電操作、表現・魔法操作に適用する。

- 通常の自然言語入力では、Thought Core に接続された AI agent が会話文脈、利用可能な能力、
  Environment State、必要に応じた Self Mirror を見て、意味理解、tool/API 選択、引数案、
  応答を決める。
- 決定的なコードは、schema 検証、allowlist、範囲制限、policy、実行、receipt、cleanup を
  担当する。通常発話の意味を固定語彙表や完全一致 parser だけで決めてはならない。
- AI を介さない決定的経路を許すのは、Emergency Stop、明示的 Reset、低遅延 reflex、
  または明示された degraded/compatibility mode に限る。
- provider 未接続時の定型 fallback は degraded evidence であり、通常運用、撮影準備、
  product acceptance の成功条件にしてはならない。
- 自然な応答のテストで本文の完全一致を要求しない。agent 境界を mock し、選んだ能力、
  構造化引数、安全性、実行結果との整合、`used_llm` provenance を検証する。
- テストを安定させる目的で production の意図判断や返答を固定語彙・固定文へ戻す変更は
  禁止する。この invariant を弱める変更には、ユーザーの明示承認と ADR 更新が必要である。

## Implementation Rules

- 外部サービスの API key、secret、個人環境の token はコード、fixture、README 例に直書きしない。環境変数で扱う。
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
- Thought Core、ai-talk-core、AITuberKit 連携で secret やローカルパスをログに出しすぎていないか。
- 実機統合が必要な変更で、単体テストだけを根拠にしていないか。
- 固定 parser や exact-response assertion が、AI agent の意味理解・tool 選択・自由な応答を
  production 経路から置き換えていないか。
