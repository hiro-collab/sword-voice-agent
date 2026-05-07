# Module Maintainer Requests

この文書は、別担当モジュールへ渡す文書整理依頼です。ここに書く内容は統合仕様ではなく、担当者レビューの依頼文として扱います。

## ai-talk-core

依頼したいこと:

- `README.md` は入口として十分短いので、大きく削る必要はありません。
- `MODULE_REQUIREMENTS.md`、`docs/integration-contract.md`、`docs/module-responsibilities.md`、`docs/retired-paths.md` の役割分担を維持してください。
- `docs/retired-paths.md` は退避理由が長くなりやすいので、索引と短い status に寄せてください。
- `agent_*` と `codex_*`、`instruction/handoff` と `command_*` の関係は、互換か主導線かを短く維持してください。
- `archive/` は通常の実装判断では読まなくても大丈夫、必要なときだけ履歴確認として読む、という表現にしてください。

## home-assistant-server

依頼したいこと:

- `docs/api-usage.md` は実例が増えやすいので、代表例だけに絞ってください。詳細な試行錯誤やDify node別の長い手順は archive へ下げてください。
- `docs/security-notes.md` を security の参照先として維持し、README へ security 詳細を戻さないでください。
- UDP 通知は演出同期であり、家電操作の成否判定には使わない、という境界を維持してください。
- `action_id` allowlist 以外の操作入口を作らないでください。
- `docs/retired-paths.md` は「退避コピーや保留導線の索引」だけに留めてください。

## mediapipe-sword-sign

依頼したいこと:

- 通常導線は MediaMTX video + Camera Hub WebSocket topic のまま維持してください。
- Python JPEG topic と `JPEG Debug Preview` は検証用として扱い、通常映像経路に昇格させないでください。
- Home Control Stack の process manifest は、この supervisor が起動した子プロセスだけを記録する方針を維持してください。
- `docs/mediamtx_integration.md` と `docs/browser_gui_integration.md` は実例が増えやすいので、代表コマンドと失敗時の確認観点だけに寄せてください。
- `docs/retired-paths.md` は互換ツールの索引に留め、旧導線の詳細な使い方を戻さないでください。
