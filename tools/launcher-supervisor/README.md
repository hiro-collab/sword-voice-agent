# Launcher Supervisor M0/M1 domain

This directory contains an OS-neutral, source/static supervisor domain only.
It does not spawn, stop, probe, or own a product process.

- `launcher_service_graph.v1` is the declarative standard service graph.
- `launcher_operation.v1` is the bounded public-safe operation snapshot.
- `OperationReducer` accepts fixed observation events and is pure.
- `OperationStore` atomically persists the bounded operation snapshot before
  preflight and after each accepted/rejected event. It accepts an authorized
  task-owned parent, derives one fixed `launcher-operation.v1` child, and
  applies owner-private access only to that child and its fixed record/lock;
  neither the parent path nor any private path enters the public record.
- The start lifecycle is `planned -> preflight -> prepared -> starting ->
  waiting_ready -> ready`, with fixed failure, rollback, recovery, stop, and
  residue states. Cleanup becomes `clear` only after every applicable owned
  service is stopped or explicitly optional-absent.
- A rejected same-operation observation records only `invalid_event` while an
  active start phase continues; persisted active snapshots reject unrelated
  failure reasons, which belong to rollback, recovery, or terminal phases.
- `DriftValidator` compares the graph with existing service/profile manifests,
  the ordinary-route readiness list, the Launcher readiness list, and the
  current PowerShell service-spec list without modifying them.
- `BindingGenerator` includes raw-byte SHA-256 identities for both schemas and
  the graph. A stale checked binding aborts validation.

No command, exception text, raw log, private path, token, secret, PID, URL, or
payload belongs in the graph, operation state, event, binding, or test report.

## Offline checks

```powershell
dotnet restore --configfile .\NuGet.Config
dotnet build --no-restore
dotnet run --no-build -- self-test <repository-root>
dotnet run --no-build -- validate <repository-root>
```

`NuGet.Config` clears every package source. The project uses the installed
.NET 8 SDK/shared framework only.

## Proof ceiling

M0/M1 source/static contract and deterministic reducer only. Windows Job
Objects, cgroup v2, process launch, listener ownership, public Launcher UI,
cutover, migration, and live startup remain M2 or later.
