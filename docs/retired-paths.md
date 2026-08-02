# Retired Paths

この文書は、残っているが通常の統合導線では使わないものの索引です。詳しい履歴や検討理由はここに戻さず、必要なときだけ `archives/` を確認します。

| Path | Status |
| --- | --- |
| `<cell>\services\thought-core\` | 空の local placeholder が残っている場合は退役扱い。正規実装は `<cell>\control-plane\core\services\thought-core\`。 |
| `mediapipe-sword-sign\apps\publish_udp.py` | 旧 UDP gesture 連携。通常は Camera Hub WebSocket topic を使う。 |
| Camera Hub Python JPEG topic | Python 画像 transport の検証用。通常映像は MediaMTX を使う。 |
| AITuberKit external WebSocket mode | 評価済み互換導線。通常 speech path は `POST /api/messages?...type=direct_send`。 |
| AITuberKit renderer API proposal | 設計案。実装済み接続契約ではない。 |
| `tts-service` status-file source | 互換・切り分け用。通常は HTTP source と streaming chunk endpoint を使う。 |
| Home Control `aircon_on` / `aircon_off` | `legacy-delete-candidate`。現在の製品経路に直接 action ID を指定する consumer はなく、自然言語の「つける／消す」は HA state を追跡できる `aircon_cool` / `aircon_hvac_off` へ移行済み。削除条件は、非archiveの direct-ID reference が引き続き 0、Home Control側の同名actionも同時に撤去、置換先のon/off・already-state・restore回帰がgreenであること。条件成立まではcurrent-facingに戻さない。暖房用 tracked action がない間は「暖房をつける」を別モードへ推測しない。 |
| pre-S2 `launcher_operation.v2` without `cleanup_attempts` | 読み取り互換のため field omission は残すが、旧 row の `stopped/clear` は cleanup proof として使用しない。現行 reducer/store は fail closed、bounded public projection は `stop_failed/unknown` として扱う。新しい v1 path、fallback、migration service は作らない。 |
| pre-S2 terminal clear based on any historical private-plan clear row | LEGACY。`stopped/clear` と非preflight `failed/clear` は `sequence` 上の最終 private-plan row が `clear/none` の場合だけ有効。後続 failed/unattempted、欠落、recovery event より後付けの proof は clear authority に使わず、public も unknown に落とす。厳密な no-side-effect `preflight_failed` だけは空 ledger を許す。 |
| pre-S3A positive `joined_existing` Start | LEGACY/disabled。現行S3Aは同一identityでも重複・並行・Ready Startをconflict/mutation0/dispatch0として扱い、public `joined_existing=false`。full prospective identityによるpositive joinは後続sliceまで実行経路に戻さない。 |
| pre-S3C unconditional `already_stopped` from persisted `stopped/clear` | LEGACY/disabled。現行runtimeはexact private-plan artifactをmetadata-onlyで再観測する。`absent`だけが`already_stopped`を維持し、`present|invalid|unavailable`はpersisted bytes/revisionとside effectsを変えず`terminal_unknown`、public cleanup unknownへ落とす。artifact content/path/hashは公開しない。 |
| Launcher browser/request `HomeControlConfigPath` override | LEGACY/disabled。private saved/default pathはNode Launcher server/compilerだけが所有する。browser custom-path input/binding/serializationとrequest overrideは0。public state/status/preview/save/logはopaque config identity/revisionと固定state classだけを出し、raw path/command/private identifierを公開しない。 |

`archives/legacy-md/2026-05-07-doc-rebuild/` には剪定前のメモやレビューを残しています。通常の実装判断では読まなくても大丈夫です。

退役済みでも、起動スクリプトや診断ツールが互換 fallback として参照している path は、
参照を外してから `archives/` へ移します。空の placeholder は履歴価値がないため、
重複実装を誘う場合は削除してかまいません。
