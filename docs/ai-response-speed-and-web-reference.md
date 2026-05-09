# AI Response Speed and Web Reference Plan

このメモは、`sword-voice-agent` のAI応答を速くするための優先順と、将来ネット上の情報を参照して回答する場合の導入方針をまとめる。

## Current Path

現在の標準導線は次の形。

```text
ai-talk-core handoff
  -> sword_voice_agent.apps.watch_handoff_to_dify
  -> Dify Chat App API /chat-messages
  -> AITuberKit direct_send and/or tts-service chunk endpoint
```

速度面では `DIFY_RESPONSE_MODE=streaming` が最重要。Dify からの answer delta を受けたら、全文完了を待たずに AITuberKit の `direct_send` または `tts-service` の `/api/tts/chunk` へ渡す。

## Immediate Speed Measures

1. `DIFY_RESPONSE_MODE=streaming` を維持する。
2. 表示・発話側は sentence/chunk 単位で先行処理する。
   - AITuberKit: `AITUBER_MESSAGE_URL=http://127.0.0.1:3000/api/messages?clientId=...&type=direct_send`
   - tts-service: `TTS_HTTP_CHUNK_URL=http://127.0.0.1:8765/api/tts/chunk`
3. `AITUBER_SPEECH_MAX_CHARS` / `TTS_HTTP_CHUNK_MAX_CHARS` は 40-80 の範囲で調整する。
   - 小さいほど初動は速いが、発話が細切れになりやすい。
   - 大きいほど自然だが、最初の発話が遅くなる。
4. Dify workflow 側は、毎ターン不要な HTTP node や長い推論分岐を通さない。
   - 家電操作や状態照会では、回答を短くする指示を優先する。
   - ネット検索や重い状態取得は、必要な発話だけに限定する。
5. 実測は status event を見る。
   - `dify.first_token`: Dify が最初の answer delta を返した時刻。
   - `dify.done`: Dify streaming 完了。
   - `aituber.forward_error` / `tts.forward_error`: ローカル転送詰まりの検知。

実装上の注意: streaming delta の受信中にローカル HTTP POST を同期実行すると、AITuberKit/TTS の応答待ちが Dify SSE 読み取りを止める。`watch_handoff_to_dify` では local forward をバックグラウンド送信し、終了時に未送信分だけ待つ。

## Web Reference Options

### Option A: Keep Dify as the Orchestrator

Dify workflow に「ネット参照が必要か」を判定する分岐を置き、必要な場合だけ検索 API を呼ぶ。Dify 公式 docs の HTTP Request node は外部 API や web service への接続用途として説明されており、GET/POST などの標準 HTTP method を扱える。

推奨する形:

1. まず軽い分類で `needs_web=true/false` を決める。
2. `true` のときだけ HTTP Request node で検索 API を呼ぶ。
3. 検索結果は本文・タイトル・URL・取得時刻に正規化する。
4. Dify の最終回答には「参照元URL」と「いつ時点の情報か」を含める。
5. タイムアウト時はネット参照なしで回答するか、確認不能と明示する。

速度を守るため、検索対象ドメインの allowlist、短い timeout、検索結果キャッシュを入れる。家電操作などローカル状態が主役の発話では、ネット参照は既定で無効にする。

### Option B: Add an OpenAI Responses API Path

Dify とは別に OpenAI Responses API adapter を追加する場合、OpenAI の Web search tool を使える。公式 docs では、Responses API の `tools` に `web_search` を入れると、モデルが必要に応じて最新情報を検索し、回答に引用 annotation を付けられる。ドメイン allowlist や live access の制御もある。

推奨する形:

1. `provider=dify|openai_responses` を adapter 境界で切り替える。
2. fast path は web search なしにする。
3. web-needed path のみ `tools=[{"type":"web_search"}]` を有効化する。
4. `allowed_domains` で参照範囲を絞る。
5. UI/発話ログには引用 URL を保持する。画面表示ではクリック可能にする。

OpenAI docs は、LLM latency 対策として「生成トークンを減らす」「リクエスト数を減らす」「並列化」「streaming/chunking」を挙げている。ネット参照は追加の外部 I/O なので、体感速度を守るには「必要なときだけ使う」設計にする。

## Decision

短期は Dify 継続で、今ある streaming/direct_send 経路を磨く。ネット参照は Dify workflow に search step を追加する案を先に検証する。OpenAI Responses API path は、Dify workflow の外で引用制御やドメイン制御を強く持ちたい場合の次の選択肢にする。

## References

- OpenAI Web search tool: https://developers.openai.com/api/docs/guides/tools-web-search
- OpenAI Latency optimization: https://developers.openai.com/api/docs/guides/latency-optimization
- Dify HTTP Request node: https://docs.dify.ai/en/use-dify/nodes/http-request
