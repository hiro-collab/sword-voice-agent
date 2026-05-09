# Tests

`tests/` は、control plane と Thought Core の契約、権限、メモリ、起動manifest、mock統合シナリオを確認する場所です。

このシステムは単一アプリではなく、複数のorganを束ねるsystem cellなので、テストも「関数が動くか」だけでなく、境界が混ざっていないかを重視します。

## よく使うコマンド

```powershell
cd C:\Users\kawai\works\sword-agent-system\sword-control-plane
uv run python -m unittest discover -s tests
```

短く確認する場合です。

```powershell
uv run python -m unittest tests.test_contract_schemas tests.test_ops_manifests
```

## 見ていること

| 種類 | 内容 |
|---|---|
| contract | `contracts/` のschemaが壊れていないか |
| policy | capability、memory scope、approval が意図通りか |
| ops | profile と service manifest が整合しているか |
| memory/access | event journal、state ownership、memory candidate が混ざらないか |
| thought-core | turn、tool、再観測、応答生成が契約通りか |
| mock integration | 実機に依存せず、家電操作やretryの流れを検査できるか |

## 追加方針

- 実機の Home Assistant、MediaPipe、VOICEVOX に依存するテストは避ける。
- runtime や local の本物データを汚さない。
- 秘密値を fixture に入れない。
- 新しい境界を増やしたら、まず contract / policy / manifest の検査を足す。
- UIの見た目確認はブラウザで行い、unit testだけで完了扱いにしない。
