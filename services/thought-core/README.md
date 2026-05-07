# thought-core 実験実装

このディレクトリは、将来の `thought-core` サービス境界を試すための実験実装です。
現行の Dify workflow を置き換えるものではありません。

目的は、`sword-voice-agent` から見える API 契約を小さく固定し、内部の実装を Dify、
OpenAI Agents SDK、LangGraph、または将来の別基盤へ差し替えやすくすることです。

## 役割分担

`sword-voice-agent` は外側のランタイムとして、次を担当します。

- gesture gate
- STT
- TTS
- AITuberKit / display
- status / log
- thought-core の turn stream への接続

`thought-core` は思考体として、次を担当します。

- turn API
- 将来の system prompt / policy
- planning / tool selection
- observation / execution / evaluation / retry loop
- response shaping
- event stream 出力

tool は単発能力として扱います。

- `environment.observe`
- `home.preview`
- `home.execute`
- `memory.retrieve`
- `memory.write`
- `web.search`

`home.execute` は retry を隠しません。1回だけコマンド実行を試み、その結果を返します。
再観測、成功評価、再試行、ユーザー確認、終了判断は `thought-core` の loop 側が担当します。

## 起動方法

この初期実装は Python 標準ライブラリだけで動きます。

リポジトリ本体のルートから実行します。

```powershell
$env:PYTHONPATH="services/thought-core"
uv run python -m thought_core --host 127.0.0.1 --port 18787
```

`python` が別アプリ同梱の Python を指している環境があるため、リポジトリの Python 環境を
使う `uv run python` を推奨します。

health check:

```http
GET /health
```

JSON で turn を実行:

```http
POST /turn
Content-Type: application/json
```

SSE で turn を実行:

```http
POST /turn?stream=true
Accept: text/event-stream
Content-Type: application/json
```

`POST /turn/stream` も同じく SSE を返します。

`GET /turn/stream` は、将来ブラウザの `EventSource` で読む形を試すための軽い入口です。
現時点の主契約は、turn payload を送れる `POST /turn` です。

## sword-voice-agent client

`sword-voice-agent` 側からは `ThoughtCoreClient` で `POST /turn?stream=true` を読みます。

```powershell
$env:THOUGHT_CORE_BASE_URL="http://127.0.0.1:18787"
```

```python
from sword_voice_agent.adapters.thought_core import ThoughtCoreClient
from sword_voice_agent.protocol.messages import AgentRequest

client = ThoughtCoreClient.from_env()
events = []
response = client.send_agent_request_streaming(
    AgentRequest(
        text="電気つけて",
        context={
            "turn_id": "turn_001",
            "session_id": "living_room_main",
            "locale": "ja-JP",
            "context_refs": {"voice_turn": "voice_789"},
        },
    ),
    on_event=events.append,
)

print(response.text)
```

`on_event` には `assistant.speech_delta`、`tool.started`、`observation.received`、
`turn.completed` などの event が順番に渡ります。TTS や画面表示へつなぐ層は、
`assistant.speech_delta` または `assistant.message` を使います。

ai_talk_core の handoff JSON から動作確認する場合は、別の PowerShell で次を実行します。

```powershell
$env:THOUGHT_CORE_BASE_URL="http://127.0.0.1:18787"
uv run sword-thought-core-handoff --handoff-json tests/fixtures/handoff.json --print-events
```

実際の ai_talk_core キャッシュを読む場合は、`AI_TALK_CORE_ROOT` を設定してから実行します。

```powershell
$env:AI_TALK_CORE_ROOT="..\ai-talk-core"
$env:THOUGHT_CORE_BASE_URL="http://127.0.0.1:18787"
uv run sword-thought-core-handoff --field command --print-events
```

送信前の payload だけ確認したい場合:

```powershell
uv run sword-thought-core-handoff --handoff-json tests/fixtures/handoff.json --dry-run
```

ai_talk_core を経由せず、手入力で thought-core の最小デモを確認する場合:

```powershell
uv run sword-thought-core-handoff --text "電気つけて" --session-id living_room_main --turn-id turn_manual_001 --print-events
```

ai_talk_core の handoff 更新を監視して thought-core に流す場合:

```powershell
$env:AI_TALK_CORE_ROOT="..\ai-talk-core"
$env:THOUGHT_CORE_BASE_URL="http://127.0.0.1:18787"
uv run sword-thought-core-watch --skip-existing --print-events
```

`--skip-existing` は、起動時点で既にある handoff を処理せず、次に保存される handoff を待ちます。
まだ `.cache/codex/web_latest.json` が無い場合も、そのまま監視し続けます。

現在の handoff を1回だけ処理する場合:

```powershell
uv run sword-thought-core-watch --ai-talk-core-root ..\ai-talk-core --once --print-events
```

## turn input

```json
{
  "text": "電気つけて",
  "turn_id": "turn_001",
  "session_id": "living_room_main",
  "locale": "ja-JP",
  "context_refs": {
    "environment_snapshot": "env_abc123",
    "voice_turn": "voice_789"
  }
}
```

## event schema

すべての event は共通メタデータを持ちます。

```json
{
  "schema_version": "thought-core.event.v0",
  "event_id": "evt_...",
  "turn_id": "turn_001",
  "session_id": "living_room_main",
  "seq": 1,
  "timestamp": "2026-05-07T00:00:00.000Z",
  "source": "thought-core",
  "type": "assistant.message",
  "data": {}
}
```

tool 系 event には `tool_call_id` を入れ、`tool.started` と `tool.result` を対応づけます。

最小 event type:

- `assistant.message`
- `assistant.speech_delta`
- `action.proposed`
- `tool.started`
- `tool.result`
- `observation.received`
- `feedback.requested`
- `turn.completed`
- `turn.error`

`assistant.speech_delta` はストリーム途中出力です。
`assistant.message` は、speech / display / emotion / motion / priority を含む完成した発話単位です。

## 事実と表現の分離

`observation.received` は Environment / Home Assistant から来る事実 packet です。
構造化された事実として扱い、状態判断の source of truth にします。

`assistant.message` は、人間に向けた表現です。
将来 LLM が文面生成を担当しても、Environment / Home Assistant の事実を勝手に作ったり
書き換えたりしない設計にします。

例:

```text
environment.observe
  -> observation.received にライト状態の facts が載る
  -> assistant.message がその事実を自然な言葉で表現する
```

## 最小デモ loop

`電気つけて` を受けると、mock loop は概ね次の流れで event を出します。

```text
environment.observe
home.preview
action.proposed
assistant.speech_delta
assistant.message
home.execute
environment.observe
observation.received
assistant.speech_delta
assistant.message
turn.completed
```

実行結果を検証できない場合、retry は `home.execute` ではなく `ThoughtLoop` が行います。
retry しても確認できなければ、`feedback.requested` を出し、`needs_feedback` として完了します。
