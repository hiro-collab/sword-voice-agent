# 0002 Ops Control Plane For Lifecycle

Status: proposed

## Context

The workspace currently starts from root `.bat` shortcuts and repository-local
PowerShell scripts. The browser launcher wraps those scripts. This works, but as
more sibling repositories become part of the system, lifecycle rules can drift
between scripts, launcher UI, status checks, and docs.

## Decision

Introduce an `ops` control-plane concept before moving scripts. The current
`scripts/home-control-stack/` files remain authoritative for now, but future
start/status/stop behavior should converge on manifests and a single ops facade.

## Consequences

- Root shortcuts remain compatibility aliases.
- The launcher should eventually call the same ops facade as CLI users.
- Process records should include `service_id`, `layer`, command, cwd, PID, log
  paths, and ownership.
- Stop logic must use owned process records first and avoid killing unrelated
  user processes.
- Script movement waits until wrappers, launcher, tests, and docs are updated
  together.
