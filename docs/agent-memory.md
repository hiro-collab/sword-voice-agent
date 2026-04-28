# Agent Memory

このファイルは、将来の作業者が知っているべき長期記憶だけを残す場所です。
恒久ルールは `AGENTS.md`、state/ID の authority は `docs/state_authority.md`、一時メモは `.cache/codex/shared-note.md` に書きます。

## Project Facts

- このリポジトリは `mediapipe-sword-sign` と `ai_talk_core` を直接混ぜず、protocol と adapter でつなぐ統合アプリ。
- 作業ルートはこのリポジトリの内側ディレクトリ。外側の workspace 直下には `src` がない。
- 兄弟リポジトリの `ai_talk_core` と `mediapipe_test` は統合確認対象であり、明示依頼なしに編集しない。
- `.cache/sword_voice_agent/` はアプリ実行時の status projection と event log 用。
- `.cache/codex/` は Codex 作業者同士の一時共有メモと作業履歴用。

## Decisions

- 2026-04-28: まず `application` 層、`turn_id`、`StatusStore`、`events.jsonl` を優先する。Pydantic、FastAPI、Typer、MQTT、NATS は後回し。
- 2026-04-28: `adapters/gesture_gateway.py` は互換 wrapper として残し、ユースケース処理は `application/gesture_pipeline.py` に寄せる。
- 2026-04-28: `turn_id` の authority は `VoiceTurnController`。handoff/Dify との厳密連携は `ai_talk_core` 側更新が必要なため、最初は時刻と最新ファイルで緩く紐づける。
- 2026-04-28: `StatusStore` と console は projection であり、制御状態の authority にはしない。

## Update Rules

- 新しい判断を足す時は、日付付きで 1 行から数行に抑える。
- `AGENTS.md` や `docs/state_authority.md` と同じ内容を長く複製しない。
- 完了済み作業の詳細ログはここに書かず、必要なら `.cache/codex/agent-runs.jsonl` に残す。
