# Runtime

`runtime/` は、将来の正規runtime出力置き場です。

ここには、実行中に生成されるログ、状態、PID、cache、diagnostics を置きます。  
生成データは原則Git管理しません。

```text
runtime/
  logs/
  state/
  pids/
  diagnostics/
```

memory階層では、主に M1 module state、M3 event journal、process registry、diagnostics を扱います。  
確定した M4 memory や M6 secrets はここには置きません。

現行の互換runtime path は次です。

```text
.cache/home-control-stack
```

詳細な移行対応は `docs/runtime-layout.md` を参照してください。
