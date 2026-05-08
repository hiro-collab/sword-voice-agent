# Local Data

`local/` is the future local-only root for data that should not be committed.
It is separate from `runtime/` because it may contain durable user/system
knowledge or machine-local configuration rather than generated operational
evidence.

Tracked files in this directory should be README-style guidance only. Real data
is ignored by `.gitignore`.

| Path | Layer | Purpose |
|---|---|---|
| `memory/` | `M4` | Memory candidates, committed facts, episodes, summaries. |
| `config/` | `M5` | Local user/device/service configuration. |
| `secrets/` | `M6` | Local-only secret material, if `.env` or OS secret store is not enough. |
