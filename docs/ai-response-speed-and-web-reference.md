# AI Response Speed and Web Reference Plan

このメモは、`sword-voice-agent` のAI応答を速くするための優先順と、将来ネット上の情報を参照して回答する場合の導入方針をまとめる。

## Current Path

現在の標準導線は次の形。

```text
ai-talk-core handoff
  -> sword_voice_agent.apps.watch_handoff_to_thought_core
  -> Thought Core API /turns/stream
  -> AITuberKit direct_send and/or tts-service chunk endpoint
```

速度面では Thought Core の stream event を最初の発話単位で処理することが重要です。全文完了を待たずに、AITuberKit の `direct_send` または `tts-service` の `/api/tts/chunk` へ渡します。

## Immediate Speed Measures

1. Thought Core watcher は streaming path を使う。
2. 表示・発話側は sentence/chunk 単位で先行処理する。
   - AITuberKit: `AITUBER_MESSAGE_URL=http://127.0.0.1:3000/api/messages?clientId=...&type=direct_send`
   - tts-service: `TTS_HTTP_CHUNK_URL=http://127.0.0.1:8765/api/tts/chunk`
3. `AITUBER_SPEECH_MAX_CHARS` / `TTS_HTTP_CHUNK_MAX_CHARS` は 40-80 の範囲で調整する。
   - 小さいほど初動は速いが、発話が細切れになりやすい。
   - 大きいほど自然だが、最初の発話が遅くなる。
4. Thought Core 側は、毎ターン不要な外部 provider call や重い状態取得を通さない。
   - 家電操作や状態照会では、短い応答と必要最小限の再観測を優先する。
   - ネット検索や重い retrieval は、必要な発話だけに限定する。
5. 実測は status event を見る。
   - `thought_core.first_message`: 最初の表示/発話候補が出た時刻。
   - `thought_core.completed`: Thought Core streaming 完了。
   - `aituber.forward_error` / `tts.forward_error`: ローカル転送詰まりの検知。

実装上の注意: streaming event の受信中にローカル HTTP POST を同期実行すると、AITuberKit/TTS の応答待ちが Thought Core event 読み取りを止めます。watcher 側の local forward はバックグラウンド送信し、終了時に未送信分だけ待つ設計にします。

## Web Reference Options

ネット参照は Thought Core 内部の provider adapter または tool adapter として扱います。外側の起動、監視、UI、導入ドキュメントは Thought Core を主語にします。

推奨する形:

1. まず軽い分類で `needs_web=true/false` を決める。
2. `true` のときだけ検索 tool/provider adapter を呼ぶ。
3. 検索結果は本文・タイトル・URL・取得時刻に正規化する。
4. 最終回答には「参照元URL」と「いつ時点の情報か」を含める。
5. タイムアウト時はネット参照なしで回答するか、確認不能と明示する。

速度を守るため、検索対象ドメインの allowlist、短い timeout、検索結果キャッシュを入れます。家電操作などローカル状態が主役の発話では、ネット参照は既定で無効にします。

## Decision

短期の主経路は Thought Core API と Thought Core watcher です。外部推論 provider は Thought Core 内部の実装候補に限定し、system cell の起動系・監視系・UI 導線としては扱いません。
