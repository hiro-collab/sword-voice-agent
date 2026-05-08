# Tests

The test tree is intentionally conservative while the repository is being
reorganized.

- Root-level `test_*.py` files cover the current application modules and
  legacy-compatible entry points.
- `system/` contains tests for the local memory/access kernel helpers:
  capability policy, event journal, state ownership, memory candidates, and
  mock system scenarios.
- `fixtures/` contains small static payloads that are safe to commit.

Avoid adding new top-level category directories until a package or service has
enough tests to justify a stable boundary. Prefer `tests/system/` for
cross-cutting OS-style subsystem tests.
