# thought-core turn service v0

このディレクトリは、現行 workspace での `thought-core` 正規 service root です。
現在は v0 実装として、Dify workflow と並走しながら turn 境界を固めています。

目的は、system cell の外側ランタイムから見える API 契約を小さく固定し、内部の実装を Dify、
OpenAI Agents SDK、LangGraph、または将来の別基盤へ差し替えやすくすることです。

## 境界仕様

外側に見せる主契約は turn/event stream です。内部の LLM 応答は
`thought-core.turn_responder.v0` という小さな port に分けています。

- `TurnInput` を受け取る
- `speech` / `display` / `status` を返す
- tool 実行や Home Assistant の状態捏造はしない
- 実装は `openai_compatible_chat` / LangGraph / OpenAI Agents SDK / Dify などの adapter に差し替える

最初の adapter は依存なしの OpenAI-compatible HTTP です。`.env` またはプロセス環境で
`THOUGHT_CORE_LLM_BASE_URL`, `THOUGHT_CORE_LLM_API_KEY`, `THOUGHT_CORE_LLM_MODEL`
を指定できます。未設定時は local fallback が短い応答を返します。

応答口調は `THOUGHT_CORE_PERSONA` で切り替えます。通常の home-control stack 起動では
`cheerful_ossan` が入り、感情タグ `[happy]` などと必要な `[motion:...]` を付けた
砕けたホームアシスト口調に整形します。未設定または `plain` の場合は、本文を変えずに返します。

## 役割分担

system cell の外側ランタイムは、次を担当します。

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

`home.preview` / `home.execute` は `thought-core.tool_adapter.v0` の境界です。
既定は依存なしの mock adapter で、スタック起動時に Home Assistant bridge が有効な場合だけ
`THOUGHT_CORE_TOOLS_ADAPTER=home_control` と bridge URL / token がプロセス環境から渡されます。
この adapter は bridge の allowlist 上の `action_id` だけを呼び、Home Assistant の
service 名や entity 名は Thought Core 側では生成しません。

`home.execute` は retry を隠しません。1回だけコマンド実行を試み、その結果を返します。
再観測、成功評価、再試行、ユーザー確認、終了判断は `thought-core` の loop 側が担当します。

### メモリと段階的な思考

turn 開始時に `memory.retrieve` を呼び、取得した short_memory / selected M4 memory を
Environment State と同じ観測入力として扱います。取得メモリはその turn の working memory
にだけ展開し、action / target / review の判断材料に使います。

- `short_memory` は retry budget、観測試行、直近 turn の失敗・反省などの短期材料
- `failure_patterns` は失敗時に `memory.write` へ candidate として出す長期候補
- `user_preferences` / `device_aliases` は必要に応じて読み、発話や対象解釈の補助に使う

既定のローカル保存先は `local/memory/` です。実行時に次の環境変数で変更できます。

- `THOUGHT_CORE_MEMORY_ROOT`
- `THOUGHT_CORE_MEMORY_POLICY_ROOT`
- `THOUGHT_CORE_MEMORY_RETRIEVE_LIMIT`

LLM を有効にした場合も、1回の巨大 prompt で全部を決めません。
`THOUGHT_CORE_ACTION_LLM_ENABLED=1` のとき、Action Reasoner は次の小さな境界に分けて
OpenAI-compatible adapter へ問い合わせます。

1. prompt + Environment State + memory から Target State を作る
2. Target State と現在状態の差分から、allowlist 済み action の中で実行内容を選ぶ
3. 実行後の Environment State と Target State を比べて成否を判定する

LLM は言葉、理由、判定補助を柔軟にできますが、`home.preview` にない command や
Home Assistant service/entity を勝手に生成することはできません。

### Dify YAML から移植した環境認識

元の Home Control Assistant YAML では、`state_queries.room_light` を Home Assistant の
スイッチ状態とは別の「映像由来の部屋の明るさ推定」として扱っていました。Thought Core でも
この境界を維持します。

- 「電気ついてる？」「照明消えてる？」「部屋の明るさどう？」は家電操作ではなく
  `environment.observe` による状態照会として扱う
- `environment.actions` がある場合は aliases / target_label / verb / noop を見て分類し、
  noop の操作は `home.execute` に進めず `action.skipped` で完了する
- Dify YAML と同じ action_id 群のうち、`light_*`, `fan_*`, `aircon_*`, `door_*`,
  `vacuum_*` は Thought Core 側でも bridge allowlist へ渡せる
- `room_light.authority=vision_snapshot_processor` はカメラ推定として返し、HA の実スイッチ状態と混ぜない
- `light_on` / `light_off` の実行後は `ENVIRONMENT_STATE_URL` に
  `wait_for=room_light&after=<issued_at>&timeout_ms=1500` を付けて再観測する
- 操作後の映像推定が不一致、unknown、または low confidence の場合は
  `state_query.feedback_pending` event を出し、ユーザー確認に回せる pending JSON を残す

待機時間は `THOUGHT_CORE_ROOM_LIGHT_WAIT_TIMEOUT_MS` で調整できます。

## 起動方法

現在の service root は次の layout です。

```text
services/thought-core/
  README.md
  flows/
  src/
    thought_core/
```

この v0 実装は Python 標準ライブラリだけで動きます。

リポジトリ本体のルートから実行します。

```powershell
$env:PYTHONPATH="services/thought-core/src"
uv run python -m thought_core --host 127.0.0.1 --port 18787
```

`python` が別アプリ同梱の Python を指している環境があるため、リポジトリの Python 環境を
使う `uv run python` を推奨します。

`127.0.0.1:18787` は thought-core の API です。
ブラウザで見る監視画面は `sword-console` 側なので、通常は `127.0.0.1:8790` を開きます。

API index:

```http
GET /
```

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

`GET /turn/stream` は、将来ブラウザの `EventSource` で読む形を試すための軽い demo/compatibility 入口です。
現時点の主契約は、turn payload を送れる `POST /turn` です。
SSE は turn 全体の完了を待たず、loop が event を生成した順に `assistant.speech_delta`、
`thought.stage`、`tool.started` などを逐次 flush します。

## control plane client

control plane 側からは `ThoughtCoreClient` で `POST /turn?stream=true` を読みます。

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
$env:AI_TALK_CORE_ROOT="..\organs\voice\ai-talk-core"
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

mock実装で retry 成功を確認する場合:

```powershell
uv run sword-thought-core-handoff --text "電気つけて" --session-id living_room_main --turn-id turn_retry_demo --context-ref mock_initial_light_state=off --context-ref mock_execute_failures_before_success=1 --print-events
```

mock実装で retry しても確認できず、`feedback.requested` へ進む流れを確認する場合:

```powershell
uv run sword-thought-core-handoff --text "電気つけて" --session-id living_room_main --turn-id turn_feedback_demo --context-ref mock_initial_light_state=off --context-ref mock_execute_failures_before_success=3 --print-events
```

`mock_` で始まる `context_refs` は、依存なしの demo mock だけが読む制御です。
実ツールでは、Environment / Home Assistant の事実を source of truth として扱います。

ai_talk_core の handoff 更新を監視して thought-core に流す場合:

```powershell
$env:AI_TALK_CORE_ROOT="..\organs\voice\ai-talk-core"
$env:THOUGHT_CORE_BASE_URL="http://127.0.0.1:18787"
uv run sword-thought-core-watch --skip-existing --print-events
```

`--skip-existing` は、起動時点で既にある handoff を処理せず、次に保存される handoff を待ちます。
まだ `.cache/codex/web_latest.json` が無い場合も、そのまま監視し続けます。

thought-core の応答を外側ランタイムへ流す場合は、必要な出力先だけ指定します。
`assistant.speech_delta` は TTS chunk API へ、`assistant.message` は AITuberKit direct_send へ送ります。

```powershell
uv run sword-thought-core-watch --ai-talk-core-root ..\organs\voice\ai-talk-core --skip-existing --print-events --tts-chunk-url http://127.0.0.1:8765/api/tts/chunk --aituber-message-url "http://127.0.0.1:3000/api/messages?clientId=sword&type=direct_send"
```

AITuberKit への短い先行相づちを止める場合は `--local-ack-mode off` を指定します。

現在の handoff を1回だけ処理する場合:

```powershell
uv run sword-thought-core-watch --ai-talk-core-root ..\organs\voice\ai-talk-core --once --print-events
```

watcher は既定で `.cache/sword_voice_agent/latest_thought_core_response.json` と
`.cache/sword_voice_agent/events.jsonl` に thought-core の進行を書きます。
status 出力が不要な場合は `--status-dir ""` を指定します。

手入力CLIやwatcherを実行した後、console status の `thought_core` セクションでも確認できます。
`sword-console` は既定で `http://127.0.0.1:18787` の thought-core API 到達性も Modules に表示します。
別ポートで動かす場合は `--thought-core-base-url` を指定します。

```powershell
uv run python -c "import json; from pathlib import Path; from sword_voice_agent.adapters.console_status import ConsoleStatusConfig, build_console_status; s=build_console_status(ConsoleStatusConfig(ai_talk_core_root=Path('..')/'ai-talk-core')); print(json.dumps(s['thought_core'], ensure_ascii=False, indent=2))"
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
機械検証用の schema は `contracts/events/event.schema.json` と
`contracts/turn/turn-response-events.schema.json` にあります。

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
- `thought.stage`
- `memory.retrieved`
- `target_state.imagined`
- `command.planned`
- `action.proposed`
- `action.reviewed`
- `tool.started`
- `tool.result`
- `observation.received`
- `short_memory.updated`
- `memory.candidate_recorded`
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
input.acknowledged
assistant.speech_delta / assistant.message
memory.retrieve
environment.observe
target_state.imagined
home.preview
command.planned
action.proposed
assistant.speech_delta
assistant.message
home.execute
environment.observe
observation.received
action.reviewed
short_memory.updated
assistant.speech_delta
assistant.message
turn.completed
```

実行結果を検証できない場合、retry は `home.execute` ではなく `ThoughtLoop` が行います。
retry しても確認できなければ、`feedback.requested` を出し、`needs_feedback` として完了します。
