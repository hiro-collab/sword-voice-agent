# Local Data

`local/` は、このcontrol plane repoで扱うローカル専用データの置き場です。

runtime と違い、local には「消えると困る可能性がある」設定、記憶、秘密情報が入ります。  
実データは原則Git管理しません。

| パス | Memory層 | 用途 |
|---|---|---|
| `memory/` | M4 | memory candidate、確定事実、episode、summary |
| `config/` | M5 | このPC固有のユーザー設定、デバイス設定 |
| `secrets/` | M6 | `.env` やOS secret storeだけでは足りない秘密情報 |

設計、schema、policy は `docs/`, `contracts/`, `policies/` に置きます。
