# Sword System Launcher

Local web launcher for the Sword Agent OS system cell.

It serves a browser UI for:

- choosing launch profiles
- editing common startup options and ports
- previewing the exact PowerShell command
- starting and stopping through the ops lifecycle facade
- keeping reference URLs visible after logs scroll

The primary profile is `System Cell (Thought Core)`. Normal operation should
use the Thought Core profile.
For timed local demonstrations, `Fast visible demo` starts the minimal
Projection Visual plus no-provider Thought Core path.
For timed demonstrations that must reach one bounded appliance handoff,
`Fast action demo` keeps that minimal local display/audio path but also starts
the Home Assistant bridge while still skipping environment state, camera,
vision, and TouchDesigner services.

The fast demo profiles lower the VOICEVOX readiness wait budget with
`VoicevoxReadyTimeoutSeconds`. The Launcher keeps the default at 45 seconds for
normal profiles, while `Fast visible demo` and `Fast action demo` use an
8-second wait so missing speech readiness does not consume the entire
first-response timing budget.

Normal profiles keep a longer `MediapipeReadyTimeoutSeconds` budget. Camera Hub
startup can be close to 35 seconds on the local webcam path, so the Launcher
passes a 90-second default to avoid treating a nearly-ready MediaPipe stack as a
failed startup.
The Startup timing panel shows each service's measured startup time next to its
maximum ready wait. Services with an explicit wait budget, such as VOICEVOX and
MediaPipe, can be edited in that panel and saved with the normal Launcher
configuration.

The Launcher exposes source/static diagnostic readiness and startup timing
summaries for later reviewed measurement routes:

- `GET /api/startup-timing` returns `launcher_startup_timing.v0` with expected
  service IDs, first-ready elapsed milliseconds, waiting elapsed milliseconds,
  timeline events, and the current critical-path service ID.
- `GET /api/diagnostic-surfaces` returns the no-live diagnostic surface map for
  audio awareness, Self Mirror temporal motion, Projection Visual display/TTS,
  Projection Visual response binding, and OS display/window prompt summaries.
- `GET /api/demo-timed-action-readiness` returns the current `demo-fast-action`
  first-feedback/first-action readiness summary, including required local
  service IDs, target milliseconds, remaining milliseconds to the first-action
  target, Projection Visual URL, Action bridge operator URL, next operator steps,
  and the reviewed `aircon_cool` / `aircon_hvac_off` action IDs for the
  first-action route. These fields are route guidance only, not command
  authority.

For read-only timing collection during a reviewed runtime route, run:

```powershell
node .\tools\home-control-launcher\scripts\collect-demo-timing.mjs --timeout-ms 30000
```

The collector polls only the Launcher summary endpoints above. It does not
start Chrome, start services, send UI input, submit Home Control preview/dry-run
or execute requests, or capture microphone/system/browser audio or screen
content.

When the Home Assistant bridge is enabled, Quick Links includes the local
`/operator` console as `Action bridge operator`. This is a visible/selectable
operator-surface shortcut only; opening the link is not command authority and
does not submit preview, dry-run, execute, or confirm-execute requests.

These endpoints publish class/count/timing summaries only. They do not perform
microphone, system-audio, browser-audio, screen, camera, or Home Control
capture/operation, and they do not publish raw screenshots, video, audio,
transcripts, browser storage, Home Assistant payloads, tokens, or private
paths.

The Launcher API is the only lifecycle entry point. `server.js` owns the
operation store/reducer, private plan compiler, correlated worker client,
dependency order, deadlines, rollback, finalization, and public status.
`ops/scripts/system.ps1` is a fail-closed loopback HTTP compatibility client
for that already-running Launcher API. It does not execute or fall back to the
legacy start/status/stop scripts. Those scripts remain tracked as unreachable
reference until their separately reviewed retirement.

## Launcher Supervisor Node N0

The repository now also contains the dependency-free Node contract/reducer
boundary for the future single Launcher lifecycle authority:

- `launcher-supervisor-contract.js` loads and validates the versioned service
  graph, operation schema, worker schema, immutable reducer vectors, generated
  binding, and current legacy drift surfaces before operation-store access.
- `launcher-supervisor-reducer.js` is the pure lifecycle reducer. It has no OS
  or process I/O and keeps the adopted planned/preflight/prepared/start/ready,
  rollback, recovery, stop, residue, optional-camera, and external-VOICEVOX
  semantics.
- `launcher-operation-store.js` owns the fixed private
  `launcher-operation.v2` child record, exclusive lock, revision compare-and-
  swap, duplicate-Start join, restart recovery record, reparse/path rejection,
  and atomic bounded state persistence.

`launcher-operation.v1.schema.json#/$defs/service_id` is the sole service-ID
pattern authority consumed by the graph, worker protocol, reducer, and store.
Operation and worker revisions share the JavaScript safe-integer ceiling.
Validated authority documents are defensive-copied and recursively frozen;
contract and operation-record reads use strict UTF-8 and fixed byte/count
ceilings. The private store can reclaim only a structurally valid stale owned
lock and can promote or discard only a fully validated fixed-name crash temp;
unknown or foreign bytes are retained untouched and fail closed.
Stale-lock recovery records the owner PID only inside the private lock file and
requires two positive `absent` observations from the injected bounded liveness
observer. A live or reused PID, denied/unknown observation, malformed result,
or observer failure cannot rename or remove the lock. The observer never
signals or stops a process. If an operation and lock release both fail, the
bounded error retains the primary fixed code plus a separate fixed cleanup
code; neither code carries raw process, path, or exception data.
The N0 default liveness observer is deliberately self-only and performs no OS
process inspection: it reports the current process as `alive` and every
non-self PID as `unknown`, so it never reclaims a non-self stale lock. Actual
stale recovery requires a bounded injected observer that positively confirms
owner absence twice. Until N1 supplies that observer, a crash lock encountered
through the default path remains fail-closed and requires manual diagnosis.

N0 was adopted as source/static preparation only. It did not start, stop,
probe, or spawn services or replace the Launcher lifecycle facade. The
standard graph pins the Windows
worker adapter classes as `job_worker_service` and
`job_worker_job_close`; external services remain `external_probe_only` and
`external_noop`. Worker results expose only bounded ownership/listener/
descendant classifications. Raw commands, process IDs, private paths, output,
and environment values are not part of the public operation record.
N0 verifies fixed-child containment, reparse rejection, owner-only file modes,
and private-field exclusion. The Windows worker cutover must additionally prove
the authorized parent ACL and inherited child ACL in a normal-user runtime;
that live ACL proof is intentionally not claimed by this source/static slice.

All graph/schema/vector identities use the same `utf8_lf_v1` text hash rule,
so CRLF and LF checkouts bind to one authority. The generated binding includes
those hashes and fails closed on stale or partial files before operation-store
I/O.

The reducer and contracts are OS-neutral. The N2 Windows cutover attaches the
Job Object worker, while future Ubuntu support can attach an owned process-
group/cgroup worker without changing graph, reducer, operation record, or UI
contracts. No Ubuntu adapter is enabled by N2.

## Launcher Supervisor Node N1 Windows worker

N1 adds a bounded Windows worker behind the adopted N0 authority without
cutting the current Launcher over to it:

- `launcher-job-worker-client.js` validates every request against the loaded
  graph/binding authority, permits one in-flight JSON-line exchange, validates
  and correlates the bounded result, and discards worker stderr. It also
  provides the no-signal PID/creation-time observer that N0 can inject for
  stale-lock classification. The adapter requires an explicit existing,
  absolute, non-reparse PowerShell executable path; it never resolves a worker
  executable from the repository working directory or ambient `PATH`.
- `launcher-service-plan.psm1` reads one private, identity-bound service-plan
  document. The public request carries only `service_id`; executable paths,
  arguments, environment values, and working directories never enter the
  public worker result. Service IDs and executable families are allowlisted,
  required plans must be present, optional camera plans may be absent, and
  VOICEVOX remains external probe-only. Owned children receive only the
  per-service environment explicitly present in that private plan; broad
  inheritance from the Launcher process is rejected.
- `launcher-job-worker.ps1` is a dumb Windows OS adapter. It creates each owned
  process suspended, assigns it to a per-service Job Object configured with
  `KILL_ON_JOB_CLOSE`, and only then resumes the process. Listener readiness is
  accepted only when the listener belongs to that exact job. Stop closes only
  the retained owned job after PID/creation-time and listener revalidation;
  PID reuse, foreign listeners, and unverifiable identity fail closed.

The worker retains Job handles only in its private process. Closing stdin,
worker exit, or an exception reaches `finally`; Windows also closes the handles
on a worker crash, so `KILL_ON_JOB_CLOSE` cleans the exact owned descendants.
Repeated Start/Stop is idempotent, external Stop is a no-op, and public JSON is
restricted to the existing `launcher-worker.v2` enums and correlation fields.

The current worker Stop producer is `forced_only`; the reserved `graceful`
class is not emitted. An owned service is clear only when the result proves an
exact `forced_only` or `already_clear` termination, a trusted Job query with
`active_count_after=0`, and a post-stop listener result of `clear` or exact
`not_applicable`. Failed/unknown Job queries, nonzero descendants, and
foreign/unknown listeners remain failed or unknown even if an external census
finds the port free.

`launcher_operation.v2.cleanup_attempts` records bounded attempts in execution
order. A service-local failure does not hide later safe reverse cleanup through
the same trusted worker. Loss of that worker transport records every remaining
owned candidate as `unattempted_transport_unavailable`; it never creates a
replacement cleanup worker. Every owned service that is finally `stopped` and
has `attempt_sequence>0` must have a final service cleanup row with the complete
clear tuple above; an earlier clear row cannot override a later failed,
unattempted, incomplete, or missing final row. `optional_absent` and genuinely
never-started `stopped` rows with `attempt_sequence=0` are exempt.
`stopped/clear` and non-preflight `failed/clear` also require the final
private-plan cleanup row, selected by `sequence`, to be `clear/none`; that
artifact row cannot substitute for service proof, and an earlier clear cannot
mask a later failed or unattempted row. The only row-free terminal-clear case
is exact `preflight_failed` with `action_certainty=not_attempted`, every service
`attempt_sequence=0`, and an empty cleanup ledger. Recovery persists the
private-plan completed/failed/unattempted fact before `recovery_started` or
`recovery_completed`; failed/unavailable cleanup remains residue/unknown. A
private-plan removal failure after every owned service is already stopped is
artifact residue with no fabricated service residue IDs. Pre-S2 v2 records may
omit `cleanup_attempts` for compatibility, but an omitted proof cannot retain
or publish terminal cleanup-clear authority.

N1 was reviewed and adopted at a source/static and synthetic-only proof
ceiling. N2 binds those exact worker bytes to the Node lifecycle authority;
normal-user ACL inheritance and real listener/job ownership remain distinct
runtime proof gates and are not claimed by source or deterministic fake-worker
tests.

## Launcher Supervisor Node N2 atomic cutover

`launcher-supervisor-runtime.js` is the single side-effect coordinator. It
validates the frozen graph, binding, selected profile, configuration, and
private service plan before it creates an operation or exchanges a worker
message. It then persists `planned`, `preflight_started`, and
`preflight_passed` before the first worker exchange. Every worker request is
derived from the frozen authority, correlated by operation/revision/nonce, and
reduced into the private bounded operation record.

S3A keeps positive Start joining disabled. Repeated, concurrent, in-flight, or
already-Ready Start requests return the existing bounded conflict result with
`joined_existing=false`, revision mutation zero, and dispatch zero. A worker
transport timeout while a Start dispatch is outstanding is not a readiness
timeout: it persists `start_dispatch_unknown`, reports `terminal_unknown` with
`may_have_occurred`, retries zero times, and fences every later child dispatch.
Only the already-held trusted worker/client lineage may run S2 rollback.

Stop can preempt an in-flight Start only inside the same runtime when the
cached operation, supervisor generation, lease proof, and client still match.
The in-memory fence is installed before another child dispatch; never-attempted
owned rows become stopped without a worker call, while attempted rows use the
existing S2 cleanup route. Missing or uncertain authority returns unknown and
creates no replacement worker. This is not orphan takeover or full positive
join support.

`launcher-private-service-plan.js` compiles the canonical
`thought-core-v0` primary profile independently from the legacy start script.
The plan is written only below the private Launcher runtime directory. Raw
commands, arguments, environment values, working directories, executable
paths, plan paths, and process IDs are never copied into the public operation
projection. The plan is removed only after the worker closes. The ordered
private-plan cleanup fact is persisted before a bounded recovery terminal
event, and the final stop/rollback/recovery state is persisted only after that
proof exists.

VOICEVOX is the only external service in this graph. The runtime sends it only
`probe`; it never sends external `start` or `stop`, never fabricates
`external_ready`, and reduces an honest readiness timeout/unavailable result
through rollback to a bounded failure record. Optional camera services reduce
to `optional_absent` when they are not selected.

`POST /api/reclaim-managed-ports` is retained only as a fixed fail-closed
compatibility response with `kill_authority: false`. It cannot signal a
process. `POST /api/status-script` returns the Node supervisor projection and
sets `status_script_execution: false`; it does not execute the legacy status
supervisor. The old start/status/stop and independent PID/port reclamation
implementations remain unreachable reference until N3 removal.

Runtime state is written under `.cache/home-control-stack/` by default:

```text
.cache/home-control-stack/
  launcher-config.json
  launcher-state.json
  demo-safe-settings.json
  logs/launcher-stack.log
```

`demo-safe-settings.json` stores local operator choices for the Launcher Demo
settings drawer. It is local state, not tracked source; fresh clones use the
tracked defaults from `manifests/demo-safe-settings/defaults.json` and start
with demo-safe candidates disabled.

Tracked defaults may include all-appliance command-stimulus route metadata such
as `action_ids`, proof ceiling, and configured wait estimates. The Launcher
shows that metadata for planning only. Enabling a row does not call Home
Assistant, submit a Home Control action, publish raw evidence, or upgrade proof
claims; a later reviewed runtime route still owns command submission, timing
measurement, feedback wording, and cleanup.

To test an alternate compatible state directory, set
`HOME_CONTROL_STACK_STATE_DIR` before starting the launcher or pass
`-StackStateDir` to the ops lifecycle command. Relative paths are resolved from
the workspace root. The launcher passes the resolved state directory to
start/status/stop child processes so they read the same `pids.json`.

`launcher-stack.log` is rotated by the launcher server. The active log is
kept to 5 MB by default, with 3 backup files:

```text
logs/launcher-stack.log
logs/launcher-stack.log.1
logs/launcher-stack.log.2
logs/launcher-stack.log.3
```

The runtime may append allowlisted JSONL diagnostics to this same bounded log.
Records contain only fixed owner/boundary, operation reference, generation,
revision, phase, reason, terminal-proof, side-effect, cleanup, and retry
classes. `private_plan_adapter` is an allowed attribution class, but private
plan bytes, hashes, paths, payloads, raw commands, PID, and port are excluded.
Diagnostic sink failure never changes lifecycle state. Source and deterministic
tests do not prove local ACL or retention behavior in a live product run.

The limits can be overridden with:

- `HOME_CONTROL_LAUNCHER_STACK_LOG_MAX_BYTES`
- `HOME_CONTROL_LAUNCHER_STACK_LOG_BACKUPS`

Start it from the workspace root:

```powershell
.\start-home-control-launcher.bat
```

If another launcher is already running on the same port, the start shortcut
requests a bounded supervisor stop before it restarts the Launcher. The
Launcher refuses shutdown when worker cleanup cannot reach a final clear
record. After restart, `Ctrl+C`, `SIGTERM`, the API shutdown route, and the
stop shortcut all run the bounded supervisor stop before the Launcher exits.

Stop the Node-owned stack, finalize its operation record, and then stop the
Launcher server from the workspace root:

```powershell
.\stop-home-control-launcher.bat
```

Stop the stack itself separately:

```powershell
.\stop-home-control-stack.bat
```

Or from this repository:

```powershell
.\ops\scripts\home-control-stack\start-home-control-launcher.ps1
```

Use `-ReuseExisting` when you only want to open or reuse the already-running
launcher instead of moving it into the current terminal.

## Reduced text/bubble candidate (held)

`core-rehearsal-text-bubble-v0` is a non-selected source/static candidate. Its
validated graph has exactly four owned services, in order:
`openai_provider_broker -> thought_core_api -> thought_core_watcher ->
aituber_kit`. The Launcher binds the exact Parent profile source hash to that
graph, probe document, generated binding, private plan, worker adapter, and
Stop class; drift is rejected before the plan is accepted.

The current candidate deliberately holds watcher turn admission. Thought uses
`conversation_only` with disabled tools, every capability unavailable, action
submission/Home/Environment calls/retries all zero, and no Mock fallback. The
compiled watcher plan forces TTS, direct-send, local acknowledgement,
auto-review, and closed-loop output off after environment hydration. Its public
contract is `GET /api/reduced-route-contract`, which returns only opaque
profile/config identity and fixed route/readiness/reason classes.

AITuber HTTP reachability is not proof of message receiver, browser store,
bubble application, or visible pixels. This candidate is not Parent-selected
and proves no provider availability, turn, browser input, semantic Ready,
presentation, Stop/residue0, `CORE_REHEARSAL_CLEAR`, standard/full route, or U1.
