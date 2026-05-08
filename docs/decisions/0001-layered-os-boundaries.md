# 0001 Layered OS-Style Boundaries

Status: accepted

## Context

The workspace is growing into several independent capabilities: voice input,
gesture/reflex input, one-turn thought, environment observation, home action,
expression, memory, runtime state, and operations. Without explicit boundaries,
`thought-core` or `ai-talk-core` can become catch-all modules.

## Decision

Use an OS-style mental model for architecture discussions and future migrations:

| OS analogy | System area |
|---|---|
| Application process | `apps/` and sibling UI/runtime modules |
| System call boundary | `contracts/` |
| Kernel service | `services/` logical cores |
| Device driver | `adapters/` |
| Process table and init scripts | `ops/` |
| `/var/log`, `/run`, cache | `runtime/` |
| ADR and design notes | `docs/` |

Contracts remain outside individual services so implementations can move without
changing the public boundary. Adapters isolate external systems and sibling
module protocols. Docs record design decisions and migration intent before large
physical moves.

## Consequences

- New logs, events, and status records should identify their logical layer when
  useful.
- Runtime files are operational evidence, not contracts.
- Moving a module should first preserve its contracts, then update ops and docs.
- Large sibling repositories should remain physically separate until a deliberate
  split-repo or meta-repo migration is chosen.
