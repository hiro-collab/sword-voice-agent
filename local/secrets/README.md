# Local Secrets

Prefer `.env`, the OS secret store, or the owning external service's secret
management. Use this directory only for local-only secret material that cannot
fit those mechanisms.

Secret values must not be committed, logged, summarized, or copied into memory
candidates.
