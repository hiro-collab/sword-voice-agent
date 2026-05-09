# 0005 Memory, State, Policy, And Secret Boundaries

Status: accepted

## Context

The system now behaves more like a small local AI body OS than a single app.
Gesture input, environment observation, home action, thought, expression,
runtime status, and launch supervision all need memory-like data, but they do
not need the same storage speed, retention, authority, or permissions.

If all of this is called "memory", `memory-core` would become another catch-all
and services would over-read logs, raw signals, config, or secrets.

## Decision

Use M0-M6 as the shared memory vocabulary:

| Layer | Meaning |
|---|---|
| `M0` | Raw signal buffer. |
| `M1` | Module state snapshot. |
| `M2` | Core working memory. |
| `M3` | Append-only event journal. |
| `M4` | Semantic / episodic long-term memory. |
| `M5` | Config and policy. |
| `M6` | Secrets. |

`memory-core` owns M4 commit authority only. It does not own raw signals,
module state, runtime logs, config, policy, or secrets. `thought-core` and
`deep-core` may write memory candidates, but durable memory commits must pass
through `memory-core` and policy.

Secrets remain outside memory contracts. Policies may describe which adapter or
ops layer can use a secret, but memory services must not read or summarize
secret values.

## Consequences

- Service manifests carry memory read/write metadata so ops status can show
  which M0-M6 layers a profile touches.
- Access policies are capability-based and separate from long-term memory.
- Event journals are append-only M3 evidence; useful patterns must be promoted
  through memory candidates instead of being treated as memory directly.
- Docs should absorb memory guidance by responsibility rather than creating a
  large parallel memory manual.
