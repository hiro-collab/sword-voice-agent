# Retired Paths

この文書は、残っているが通常の統合導線では使わないものの索引です。詳しい履歴や検討理由はここに戻さず、必要なときだけ `archives/` を確認します。

| Path | Status |
| --- | --- |
| `<workspace>\services\thought-core\` | 空の local placeholder が残っている場合は退役扱い。正規実装は `<workspace>\sword-voice-agent\services\thought-core\`。 |
| `scripts\start-full-stack.ps1` / `start-full-stack-supervisor.ps1` | 旧 full-stack 起動。通常は Home Control Stack を使う。 |
| `mediapipe-sword-sign\apps\publish_udp.py` | 旧 UDP gesture 連携。通常は Camera Hub WebSocket topic を使う。 |
| `mediapipe-sword-sign\apps\serve_websocket.py` | 旧 direct JSON WebSocket。通常は Camera Hub topic envelope を使う。 |
| Camera Hub Python JPEG topic | Python 画像 transport の検証用。通常映像は MediaMTX を使う。 |
| AITuberKit external WebSocket mode | 評価済み互換導線。通常 speech path は `POST /api/messages?...type=direct_send`。 |
| AITuberKit renderer API proposal | 設計案。実装済み接続契約ではない。 |
| `tts-service` status-file source | 互換・切り分け用。通常は HTTP source と streaming chunk endpoint を使う。 |

`archives/legacy-md/2026-05-07-doc-rebuild/` には剪定前のメモやレビューを残しています。通常の実装判断では読まなくても大丈夫です。

退役済みでも、起動スクリプトや診断ツールが互換 fallback として参照している path は、
参照を外してから `archives/` へ移します。空の placeholder は履歴価値がないため、
重複実装を誘う場合は削除してかまいません。
