# Ops Manifests

These manifests describe the named profiles and service identities used by the
`ops` control plane. The current supervisor implementation is inherited under
`ops/scripts/home-control-stack/`, while `ops/scripts/system.ps1` translates a
profile into the matching start/status/stop arguments.

## Directories

| Path | Purpose |
|---|---|
| `services/` | Stable service records, current script owner, layer, contracts, adapter edges, memory layers, health, stop strategy, dependencies. |
| `profiles/` | Named service sets accepted by `ops/scripts/system.ps1 -Profile`. |

The `service_id` values intentionally match current PID registry names where
possible, so `system.ps1 status` can compare manifests against
`.cache/home-control-stack/pids.json`.

`contracts` and `adapters` are descriptive metadata. They do not make the
current inherited supervisor manifest-native yet; they keep status output and
future physical moves aligned with the architecture map.

`memory` describes which M0-M6 layers a running service reads, writes, or
proposes candidates for. `system.ps1 status -ManifestOnly` prints this summary
so memory exposure is visible without starting services.

Profiles may use `alias_for` for compatibility names. Alias profiles should not
carry their own service list; `ops/scripts/system.ps1` resolves them to the
canonical profile before translating services into supervisor arguments.

## Held reduced-route candidate

The non-selected `core-rehearsal-text-bubble-v0` contract is split across:

- `profiles/core-rehearsal-text-bubble-v0.json`
- `launcher-service-graph.core-rehearsal-text-bubble.v1.json`
- `launcher-probe-descriptors.core-rehearsal-text-bubble.v1.json`
- `../../contracts/launcher/generated/launcher-service-graph.core-rehearsal-text-bubble.v2.binding.json`

These files bind the exact Parent candidate profile hash to one ordered
four-service graph. They do not add a registry entry or select the profile.
Thought is conversation-only/action0; watcher admission and all output adapters
are held; AITuber HTTP is reachability-only. Missing receiver/store/bubble/pixel
proof remains unknown. No runtime Ready, presentation, Stop/residue0, Parent
selection, full-route, or user-acceptance claim follows from these manifests.
