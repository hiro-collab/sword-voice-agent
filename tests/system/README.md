# System Tests

These tests exercise OS-style subsystem boundaries rather than a single app
feature.

Current coverage:

- access policy allow/deny decisions
- memory scope and capability consistency
- append-only event journal behavior and redaction
- own-state-only state writes
- memory candidate, commit, retrieve, dedup, and confirmation behavior
- mock integration scenarios such as thought-owned retry loops

All runtime, log, and memory files created here must use `TemporaryDirectory`.
The tests must not write to real `runtime/`, `local/`, or `.cache/` data.
