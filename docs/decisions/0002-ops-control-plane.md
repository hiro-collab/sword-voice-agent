# 0002 Ops Control Plane For Lifecycle

Status: accepted

## Context

The workspace currently starts from root `.bat` shortcuts and repository-local
PowerShell scripts. The browser launcher wraps those scripts. This works, but as
more sibling repositories become part of the system, lifecycle rules can drift
between scripts, launcher UI, status checks, and docs.

## Decision

Introduce an `ops` control-plane concept before moving scripts. The current
`ops/scripts/home-control-stack/` holds the inherited supervisor engine, and
`ops/scripts/system.ps1` is the profile-aware facade for start/status/stop.
Root shortcuts call the `ops` scripts directly; the former
`scripts/home-control-stack/` forwarding layer has been retired.

## Consequences

- Root shortcuts remain compatibility aliases.
- The launcher should eventually call the same ops facade as CLI users.
- Profiles in `ops/manifests/profiles/` define which current stack components
  are selected; the facade translates that into the current `-Skip...` and
  `-Enable...` arguments.
- Process records should include `service_id`, `layer`, command, cwd, PID, log
  paths, and ownership.
- Stop logic must use owned process records first and avoid killing unrelated
  user processes.
- Script movement completed with root shortcuts, launcher, tests, and docs
  updated together.
