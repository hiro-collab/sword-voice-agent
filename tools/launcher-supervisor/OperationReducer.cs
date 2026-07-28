using System.Text.Json;
using System.Text.Json.Serialization;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Runtime.Versioning;

namespace Sword.LauncherSupervisor;

internal static class OperationPhase
{
    public const string Planned = "planned";
    public const string Preflight = "preflight";
    public const string Prepared = "prepared";
    public const string Starting = "starting";
    public const string WaitingReady = "waiting_ready";
    public const string Ready = "ready";
    public const string RollingBack = "rolling_back";
    public const string Failed = "failed";
    public const string Stopping = "stopping";
    public const string Stopped = "stopped";
    public const string Recovering = "recovering";
    public const string Residue = "residue";
}

internal static class OperationReason
{
    public const string None = "none";
    public const string PreflightFailed = "preflight_failed";
    public const string SpawnFailed = "spawn_failed";
    public const string EarlyExit = "early_exit";
    public const string ListenerMismatch = "listener_mismatch";
    public const string ReadinessTimeout = "readiness_timeout";
    public const string RollbackFailed = "rollback_failed";
    public const string StopFailed = "stop_failed";
    public const string SupervisorCrash = "supervisor_crash";
    public const string ResiduePresent = "residue_present";
    public const string InvalidEvent = "invalid_event";
}

internal static class CleanupClass
{
    public const string NotStarted = "not_started";
    public const string InProgress = "in_progress";
    public const string Clear = "clear";
    public const string Residue = "residue";
    public const string Unknown = "unknown";
}

internal static class ServiceState
{
    public const string Pending = "pending";
    public const string Starting = "starting";
    public const string Ready = "ready";
    public const string OptionalAbsent = "optional_absent";
    public const string ExternalReady = "external_ready";
    public const string StopRequested = "stop_requested";
    public const string Stopped = "stopped";
    public const string Failed = "failed";
    public const string Residue = "residue";
    public const string Unknown = "unknown";
}

internal sealed record ServiceOperationState(string ServiceId, string State);

internal sealed record LauncherOperation(
    string SchemaVersion,
    string GraphSha256,
    string BindingSha256,
    string OperationId,
    string Intent,
    string Phase,
    string Reason,
    string Cleanup,
    int Revision,
    bool JoinedExisting,
    bool RollbackRequired,
    bool RecoveryRequired,
    IReadOnlyList<ServiceOperationState> Services,
    IReadOnlyList<string> ResidueServiceIds);

internal sealed record SupervisorEvent(string EventType, string OperationId, string? ServiceId = null);
internal sealed record StartDecision(LauncherOperation Operation, bool JoinedExisting);

internal static class OperationCoordinator
{
    public static StartDecision Start(LauncherOperation? active, string operationId, string graphSha256, string bindingSha256, ServiceGraph graph)
    {
        ValidateInputs(operationId, graphSha256, bindingSha256);
        if (graphSha256 != graph.Sha256) throw new ContractException("operation_graph_identity_mismatch");
        if (active is not null && (active.GraphSha256 != graphSha256 || active.BindingSha256 != bindingSha256))
            throw new ContractException("operation_active_identity_mismatch");
        if (active is not null && active.Phase is not (OperationPhase.Stopped or OperationPhase.Failed))
        {
            var joined = active with
            {
                JoinedExisting = true,
                Revision = checked(active.Revision + 1),
            };
            return new StartDecision(joined, true);
        }

        var operation = new LauncherOperation(
            "launcher_operation.v1",
            graphSha256,
            bindingSha256,
            operationId,
            "start",
            OperationPhase.Planned,
            OperationReason.None,
            CleanupClass.NotStarted,
            0,
            false,
            false,
            false,
            graph.Services.Select(item => new ServiceOperationState(item.ServiceId, ServiceState.Pending)).ToArray(),
            Array.Empty<string>());
        return new StartDecision(operation, false);
    }

    internal static void ValidateInputs(string operationId, string graphSha256, string bindingSha256)
    {
        if (!System.Text.RegularExpressions.Regex.IsMatch(operationId, "^lop_[a-z0-9]{8,64}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            throw new ContractException("operation_id_invalid");
        if (!System.Text.RegularExpressions.Regex.IsMatch(graphSha256, "^[a-f0-9]{64}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            throw new ContractException("operation_graph_sha256_invalid");
        if (!System.Text.RegularExpressions.Regex.IsMatch(bindingSha256, "^[a-f0-9]{64}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant))
            throw new ContractException("operation_binding_sha256_invalid");
    }
}

internal static class OperationReducer
{
    public static LauncherOperation Reduce(LauncherOperation current, SupervisorEvent input, ServiceGraph graph)
    {
        if (input.OperationId != current.OperationId) return current;
        if (input.EventType == "stop_requested" && current.Phase is OperationPhase.Stopping or OperationPhase.Stopped) return current;
        if (input.ServiceId is not null && graph.Services.All(item => item.ServiceId != input.ServiceId)) return Invalid(current);

        return input.EventType switch
        {
            "preflight_started" when current.Phase == OperationPhase.Planned => Next(current, phase: OperationPhase.Preflight),
            "preflight_passed" when current.Phase == OperationPhase.Preflight => Next(current, phase: OperationPhase.Prepared),
            "preflight_failed" when current.Phase == OperationPhase.Preflight => FailPreflight(current, graph),
            "start_requested" when current.Phase == OperationPhase.Prepared => Next(current, phase: OperationPhase.Starting),
            "spawn_requested" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanRequestSpawn(current, input.ServiceId, graph) => SetService(current, input.ServiceId, ServiceState.Starting, OperationPhase.Starting),
            "spawn_succeeded" when InPhase(current, OperationPhase.Starting) && CanCompleteSpawn(current, input.ServiceId, graph) => SetService(current, input.ServiceId, ServiceState.Starting, OperationPhase.WaitingReady),
            "spawn_failed" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanCompleteSpawn(current, input.ServiceId, graph) => FailAndRollback(current, input.ServiceId, OperationReason.SpawnFailed),
            "early_exit" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanFailOwnedService(current, input.ServiceId, graph) => FailAndRollback(current, input.ServiceId, OperationReason.EarlyExit),
            "listener_mismatch" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanFailOwnedService(current, input.ServiceId, graph) => FailAndRollback(current, input.ServiceId, OperationReason.ListenerMismatch),
            "readiness_timeout" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanFailOwnedService(current, input.ServiceId, graph) => FailAndRollback(current, input.ServiceId, OperationReason.ReadinessTimeout),
            "service_ready" when InPhase(current, OperationPhase.WaitingReady) && CanCompleteSpawn(current, input.ServiceId, graph) => Ready(current, input.ServiceId, ServiceState.Ready, graph),
            "optional_absent" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanMarkOptionalAbsent(current, input.ServiceId, graph) => OptionalAbsent(current, input.ServiceId, graph),
            "external_ready" when InPhase(current, OperationPhase.Starting, OperationPhase.WaitingReady) && CanMarkExternalReady(current, input.ServiceId, graph) => ExternalReady(current, input.ServiceId, graph),
            "rollback_started" when current.RollbackRequired && InPhase(current, OperationPhase.RollingBack) => Next(current, phase: OperationPhase.RollingBack, cleanup: CleanupClass.InProgress),
            "rollback_completed" when current.RollbackRequired && InPhase(current, OperationPhase.RollingBack) => CompleteRollback(current, graph),
            "rollback_failed" when current.RollbackRequired && InPhase(current, OperationPhase.RollingBack) => Residue(current, input.ServiceId, OperationReason.RollbackFailed, graph),
            "stop_requested" when InPhase(current, OperationPhase.Planned, OperationPhase.Preflight, OperationPhase.Prepared, OperationPhase.Starting, OperationPhase.WaitingReady, OperationPhase.Ready, OperationPhase.RollingBack, OperationPhase.Failed, OperationPhase.Recovering, OperationPhase.Residue) => Stop(current, graph),
            "service_stopped" when InPhase(current, OperationPhase.RollingBack) => RollbackServiceStopped(current, input.ServiceId, graph),
            "service_stopped" when InPhase(current, OperationPhase.Stopping) => ServiceStopped(current, input.ServiceId, graph),
            "service_stopped" when InPhase(current, OperationPhase.Recovering) => RecoveryServiceStopped(current, input.ServiceId, graph),
            "service_stopped" when InPhase(current, OperationPhase.Residue) => ResidueServiceStopped(current, input.ServiceId, graph),
            "stop_failed" when InPhase(current, OperationPhase.Stopping, OperationPhase.Residue) => Residue(current, input.ServiceId, OperationReason.StopFailed, graph),
            "supervisor_crashed" when InPhase(current, OperationPhase.Planned, OperationPhase.Preflight, OperationPhase.Prepared, OperationPhase.Starting, OperationPhase.WaitingReady, OperationPhase.Ready, OperationPhase.RollingBack, OperationPhase.Stopping) => Next(current, phase: OperationPhase.Recovering, reason: FirstFailure(current, OperationReason.SupervisorCrash), cleanup: CleanupClass.Unknown, rollbackRequired: false, recoveryRequired: true),
            "recovery_started" when current.RecoveryRequired && InPhase(current, OperationPhase.Recovering) => Next(current, phase: OperationPhase.Recovering, cleanup: CleanupClass.InProgress),
            "recovery_completed" when current.RecoveryRequired && InPhase(current, OperationPhase.Recovering) => CompleteRecovery(current, graph),
            "residue_observed" when InPhase(current, OperationPhase.Recovering, OperationPhase.Residue) => Residue(current, input.ServiceId, OperationReason.ResiduePresent, graph),
            "residue_cleared" when InPhase(current, OperationPhase.Residue) => ResidueCleared(current, input.ServiceId, graph),
            _ => Invalid(current),
        };
    }

    private static LauncherOperation Ready(LauncherOperation current, string? serviceId, string state, ServiceGraph graph)
    {
        var changed = SetService(current, serviceId, state, OperationPhase.WaitingReady);
        return AllReady(changed, graph) ? Next(changed, phase: OperationPhase.Ready) : changed;
    }

    private static LauncherOperation OptionalAbsent(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = RequireService(serviceId, graph);
        if (spec.Requirement != "optional") return Invalid(current);
        return Ready(current, serviceId, ServiceState.OptionalAbsent, graph);
    }

    private static LauncherOperation ExternalReady(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = RequireService(serviceId, graph);
        if (spec.Requirement != "external") return Invalid(current);
        return Ready(current, serviceId, ServiceState.ExternalReady, graph);
    }

    private static bool AllReady(LauncherOperation operation, ServiceGraph graph)
    {
        var states = operation.Services.ToDictionary(item => item.ServiceId, item => item.State, StringComparer.Ordinal);
        return graph.Services.All(service => service.Requirement switch
        {
            "required" => states[service.ServiceId] == ServiceState.Ready,
            "optional" => states[service.ServiceId] is ServiceState.Ready or ServiceState.OptionalAbsent,
            "external" => states[service.ServiceId] == ServiceState.ExternalReady,
            _ => false,
        });
    }

    private static LauncherOperation Stop(LauncherOperation current, ServiceGraph graph)
    {
        if (current.Phase == OperationPhase.Stopped) return current;
        var external = graph.Services.Where(item => item.Ownership == "external").Select(item => item.ServiceId).ToHashSet(StringComparer.Ordinal);
        var services = current.Services.Select(item => external.Contains(item.ServiceId) || item.State == ServiceState.OptionalAbsent
            ? item
            : item with { State = ServiceState.StopRequested }).ToArray();
        return Next(current with { Services = services, Intent = "stop", ResidueServiceIds = Array.Empty<string>() },
            phase: OperationPhase.Stopping,
            cleanup: CleanupClass.InProgress,
            rollbackRequired: false,
            recoveryRequired: false);
    }

    private static LauncherOperation ServiceStopped(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var changed = SetService(current, serviceId, ServiceState.Stopped, OperationPhase.Stopping);
        var external = graph.Services.Where(item => item.Ownership == "external").Select(item => item.ServiceId).ToHashSet(StringComparer.Ordinal);
        var ownedComplete = changed.Services.Where(item => !external.Contains(item.ServiceId)).All(item => item.State is ServiceState.Stopped or ServiceState.OptionalAbsent);
        return ownedComplete ? Next(changed, phase: OperationPhase.Stopped, cleanup: CleanupClass.Clear, recoveryRequired: false) : changed;
    }

    private static LauncherOperation CompleteRollback(LauncherOperation current, ServiceGraph graph)
    {
        var outstanding = OutstandingOwnedServiceIds(current, graph);
        if (outstanding.Count == 0 && current.ResidueServiceIds.Count == 0)
            return Next(NormalizePendingOwnedAsStopped(current, graph), phase: OperationPhase.Failed, cleanup: CleanupClass.Clear, rollbackRequired: false, recoveryRequired: false);
        return RetainUnknownResidue(current, outstanding);
    }

    private static LauncherOperation FailPreflight(LauncherOperation current, ServiceGraph graph) =>
        Next(NormalizePendingOwnedAsStopped(current, graph), phase: OperationPhase.Failed, reason: OperationReason.PreflightFailed, cleanup: CleanupClass.Clear);

    private static LauncherOperation Residue(LauncherOperation current, string? serviceId, string reason, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        if (serviceId is null || spec is null || spec.Ownership != "owned") return Invalid(current);
        var changed = SetService(current, serviceId, ServiceState.Residue, OperationPhase.Residue);
        var outstanding = OutstandingOwnedServiceIds(changed, graph);
        var residue = current.ResidueServiceIds.Concat(outstanding).Append(serviceId).Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal).ToArray();
        var residueSet = residue.ToHashSet(StringComparer.Ordinal);
        var services = changed.Services.Select(item => item.ServiceId == serviceId
            ? item
            : residueSet.Contains(item.ServiceId) && item.State != ServiceState.Residue ? item with { State = ServiceState.Unknown } : item).ToArray();
        return Next(changed with { Services = services, ResidueServiceIds = residue }, phase: OperationPhase.Residue, reason: FirstFailure(current, reason), cleanup: CleanupClass.Residue, rollbackRequired: false, recoveryRequired: true);
    }

    private static LauncherOperation ResidueCleared(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        if (serviceId is null || !current.ResidueServiceIds.Contains(serviceId, StringComparer.Ordinal)) return Invalid(current);
        var residue = current.ResidueServiceIds.Where(item => item != serviceId).ToArray();
        var services = current.Services.Select(item => item.ServiceId == serviceId ? item with { State = ServiceState.Stopped } : item).ToArray();
        var changed = current with { Services = services, ResidueServiceIds = residue };
        var outstanding = OutstandingOwnedServiceIds(changed, graph);
        if (residue.Length > 0 || outstanding.Count > 0) return RetainUnknownResidue(changed, outstanding);
        return Next(NormalizePendingOwnedAsStopped(changed, graph),
            phase: current.Intent == "stop" ? OperationPhase.Stopped : OperationPhase.Failed,
            cleanup: CleanupClass.Clear,
            recoveryRequired: false);
    }

    private static LauncherOperation RollbackServiceStopped(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = RequireService(serviceId, graph);
        if (spec.Ownership != "owned") return Invalid(current);
        return SetService(current, serviceId, ServiceState.Stopped, OperationPhase.RollingBack);
    }

    private static LauncherOperation ResidueServiceStopped(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = RequireService(serviceId, graph);
        if (spec.Ownership != "owned") return Invalid(current);
        return SetService(current, serviceId, ServiceState.Stopped, OperationPhase.Residue);
    }

    private static LauncherOperation RecoveryServiceStopped(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = RequireService(serviceId, graph);
        if (spec.Ownership != "owned") return Invalid(current);
        return SetService(current, serviceId, ServiceState.Stopped, OperationPhase.Recovering);
    }

    private static LauncherOperation CompleteRecovery(LauncherOperation current, ServiceGraph graph)
    {
        var outstanding = OutstandingOwnedServiceIds(current, graph);
        if (outstanding.Count > 0 || current.ResidueServiceIds.Count > 0) return RetainUnknownResidue(current, outstanding);
        return Next(NormalizePendingOwnedAsStopped(current, graph),
            phase: current.Intent == "stop" ? OperationPhase.Stopped : OperationPhase.Failed,
            cleanup: CleanupClass.Clear,
            recoveryRequired: false);
    }

    private static IReadOnlyList<string> OutstandingOwnedServiceIds(LauncherOperation current, ServiceGraph graph)
    {
        var owned = graph.Services.Where(item => item.Ownership == "owned").Select(item => item.ServiceId).ToHashSet(StringComparer.Ordinal);
        return current.Services
            .Where(item => owned.Contains(item.ServiceId) && item.State is not (ServiceState.Pending or ServiceState.Stopped or ServiceState.OptionalAbsent))
            .Select(item => item.ServiceId)
            .OrderBy(item => item, StringComparer.Ordinal)
            .ToArray();
    }

    private static LauncherOperation NormalizePendingOwnedAsStopped(LauncherOperation current, ServiceGraph graph)
    {
        var owned = graph.Services.Where(item => item.Ownership == "owned").Select(item => item.ServiceId).ToHashSet(StringComparer.Ordinal);
        var services = current.Services.Select(item => owned.Contains(item.ServiceId) && item.State == ServiceState.Pending
            ? item with { State = ServiceState.Stopped }
            : item).ToArray();
        return current with { Services = services };
    }

    private static LauncherOperation RetainUnknownResidue(LauncherOperation current, IReadOnlyList<string> outstanding)
    {
        var unresolved = current.ResidueServiceIds.Concat(outstanding).Distinct(StringComparer.Ordinal).OrderBy(item => item, StringComparer.Ordinal).ToArray();
        var unresolvedSet = unresolved.ToHashSet(StringComparer.Ordinal);
        var services = current.Services.Select(item => unresolvedSet.Contains(item.ServiceId) && item.State != ServiceState.Residue
            ? item with { State = ServiceState.Unknown }
            : item).ToArray();
        return Next(current with { Services = services, ResidueServiceIds = unresolved },
            phase: OperationPhase.Residue,
            reason: FirstFailure(current, OperationReason.ResiduePresent),
            cleanup: CleanupClass.Unknown,
            rollbackRequired: false,
            recoveryRequired: true);
    }

    private static LauncherOperation FailAndRollback(LauncherOperation current, string? serviceId, string reason)
    {
        var changed = SetService(current, serviceId, ServiceState.Failed, OperationPhase.RollingBack);
        return Next(changed, phase: OperationPhase.RollingBack, reason: FirstFailure(current, reason), cleanup: CleanupClass.InProgress, rollbackRequired: true);
    }

    private static bool CanRequestSpawn(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        return spec is not null && spec.Ownership == "owned" && StateOf(current, spec.ServiceId) == ServiceState.Pending && DependenciesReady(current, spec, graph);
    }

    private static bool CanCompleteSpawn(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        return spec is not null && spec.Ownership == "owned" && StateOf(current, spec.ServiceId) == ServiceState.Starting;
    }

    private static bool CanFailOwnedService(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        var state = spec is null ? null : StateOf(current, spec.ServiceId);
        return spec is not null && spec.Ownership == "owned" && state is ServiceState.Starting or ServiceState.Ready;
    }

    private static bool CanMarkOptionalAbsent(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        return spec is not null && spec.Requirement == "optional" && StateOf(current, spec.ServiceId) == ServiceState.Pending && DependenciesReady(current, spec, graph);
    }

    private static bool CanMarkExternalReady(LauncherOperation current, string? serviceId, ServiceGraph graph)
    {
        var spec = graph.Services.SingleOrDefault(item => item.ServiceId == serviceId);
        return spec is not null && spec.Requirement == "external" && StateOf(current, spec.ServiceId) == ServiceState.Pending && DependenciesReady(current, spec, graph);
    }

    private static bool DependenciesReady(LauncherOperation current, ServiceSpec service, ServiceGraph graph)
    {
        foreach (var dependencyId in service.Dependencies)
        {
            var dependency = graph.Services.Single(item => item.ServiceId == dependencyId);
            var state = StateOf(current, dependencyId);
            if (dependency.Requirement == "required" && state != ServiceState.Ready) return false;
            if (dependency.Requirement == "optional" && state is not (ServiceState.Ready or ServiceState.OptionalAbsent)) return false;
            if (dependency.Requirement == "external" && state != ServiceState.ExternalReady) return false;
        }
        return true;
    }

    private static string StateOf(LauncherOperation current, string serviceId) =>
        current.Services.Single(item => item.ServiceId == serviceId).State;

    private static LauncherOperation SetService(LauncherOperation current, string? serviceId, string state, string phase)
    {
        if (serviceId is null || current.Services.All(item => item.ServiceId != serviceId)) return Invalid(current);
        var services = current.Services.Select(item => item.ServiceId == serviceId ? item with { State = state } : item).ToArray();
        return Next(current with { Services = services }, phase: phase);
    }

    private static ServiceSpec RequireService(string? serviceId, ServiceGraph graph) =>
        graph.Services.SingleOrDefault(item => item.ServiceId == serviceId) ?? throw new ContractException("event_service_unknown");

    private static bool InPhase(LauncherOperation current, params string[] phases) => phases.Contains(current.Phase, StringComparer.Ordinal);

    private static string FirstFailure(LauncherOperation current, string proposed) =>
        current.Reason == OperationReason.None ? proposed : current.Reason;

    private static LauncherOperation Invalid(LauncherOperation current) => Next(current, reason: FirstFailure(current, OperationReason.InvalidEvent));

    private static LauncherOperation Next(
        LauncherOperation current,
        string? phase = null,
        string? reason = null,
        string? cleanup = null,
        bool? rollbackRequired = null,
        bool? recoveryRequired = null) => current with
        {
            Phase = phase ?? current.Phase,
            Reason = reason ?? current.Reason,
            Cleanup = cleanup ?? current.Cleanup,
            RollbackRequired = rollbackRequired ?? current.RollbackRequired,
            RecoveryRequired = recoveryRequired ?? current.RecoveryRequired,
            Revision = checked(current.Revision + 1),
        };
}

internal static class OperationStore
{
    private const string StoreDirectoryName = "launcher-operation.v1";
    private const string RecordFileName = "launcher-operation.v1.json";
    private const string LockFileName = "launcher-operation.v1.lock";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
    };

    public static StartDecision StartAndPersist(
        string operationId,
        string graphSha256,
        string bindingSha256,
        ServiceGraph graph,
        string authorizedPrivateRuntimeRoot)
    {
        OperationCoordinator.ValidateInputs(operationId, graphSha256, bindingSha256);
        var paths = ResolveStorePaths(authorizedPrivateRuntimeRoot);
        using var storeLock = AcquireExclusiveLock(paths.LockPath);
        ValidateStoreContents(paths);
        var active = File.Exists(paths.RecordPath) ? ReadResolved(paths.RecordPath, graph, bindingSha256) : null;
        var decision = OperationCoordinator.Start(active, operationId, graphSha256, bindingSha256, graph);
        WriteAtomicResolved(paths.RecordPath, decision.Operation, graph, bindingSha256);
        return decision;
    }

    public static LauncherOperation ReduceAndPersist(
        LauncherOperation current,
        SupervisorEvent input,
        ServiceGraph graph,
        string authorizedPrivateRuntimeRoot)
    {
        if (input.OperationId != current.OperationId) return current;
        var paths = ResolveStorePaths(authorizedPrivateRuntimeRoot);
        using var storeLock = AcquireExclusiveLock(paths.LockPath);
        ValidateStoreContents(paths);
        if (!File.Exists(paths.RecordPath)) throw new ContractException("operation_store_record_missing");
        var persisted = ReadResolved(paths.RecordPath, graph, current.BindingSha256);
        if (persisted.OperationId != current.OperationId || persisted.Revision != current.Revision)
            throw new ContractException("operation_store_revision_conflict");
        var next = OperationReducer.Reduce(persisted, input, graph);
        WriteAtomicResolved(paths.RecordPath, next, graph, current.BindingSha256);
        return next;
    }

    public static LauncherOperation Read(string authorizedPrivateRuntimeRoot, ServiceGraph graph, string expectedBindingSha256)
    {
        var paths = ResolveStorePaths(authorizedPrivateRuntimeRoot);
        using var storeLock = AcquireExclusiveLock(paths.LockPath);
        ValidateStoreContents(paths);
        return ReadResolved(paths.RecordPath, graph, expectedBindingSha256);
    }

    private static LauncherOperation ReadResolved(string fullPath, ServiceGraph graph, string expectedBindingSha256)
    {
        try
        {
            var operation = JsonSerializer.Deserialize<LauncherOperation>(File.ReadAllBytes(fullPath), JsonOptions)
                ?? throw new ContractException("operation_store_record_invalid");
            ValidateSnapshot(operation, graph, expectedBindingSha256);
            return operation;
        }
        catch (ContractException)
        {
            throw;
        }
        catch
        {
            throw new ContractException("operation_store_read_failed");
        }
    }

    private static void WriteAtomicResolved(string fullPath, LauncherOperation operation, ServiceGraph graph, string expectedBindingSha256)
    {
        ValidateSnapshot(operation, graph, expectedBindingSha256);
        var directory = Path.GetDirectoryName(fullPath)!;
        var temporaryPath = Path.Combine(directory, ".launcher-operation-" + Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            var bytes = JsonSerializer.SerializeToUtf8Bytes(operation, JsonOptions);
            using (var stream = new FileStream(temporaryPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough))
            {
                stream.Write(bytes);
                stream.Flush(flushToDisk: true);
            }
            EnsureOwnerPrivateAccess(new FileInfo(temporaryPath));
            File.Move(temporaryPath, fullPath, overwrite: true);
            RejectReparsePoint(fullPath);
            EnsureOwnerPrivateAccess(new FileInfo(fullPath));
        }
        catch
        {
            try
            {
                if (File.Exists(temporaryPath)) File.Delete(temporaryPath);
            }
            catch
            {
                // The public failure is fixed; no private path or exception text escapes.
            }
            throw new ContractException("operation_store_write_failed");
        }
    }

    private sealed record StorePaths(string Root, string RecordPath, string LockPath);

    private static StorePaths ResolveStorePaths(string authorizedPrivateRuntimeRoot)
    {
        if (string.IsNullOrWhiteSpace(authorizedPrivateRuntimeRoot) || !Path.IsPathFullyQualified(authorizedPrivateRuntimeRoot))
            throw new ContractException("operation_store_root_invalid");
        try
        {
            var parent = Path.GetFullPath(authorizedPrivateRuntimeRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (!Directory.Exists(parent)) throw new ContractException("operation_store_root_invalid");
            RejectReparseTraversal(parent);
            var root = Path.GetFullPath(Path.Combine(parent, StoreDirectoryName));
            if (!string.Equals(Path.GetDirectoryName(root), parent, StringComparison.OrdinalIgnoreCase))
                throw new ContractException("operation_store_root_invalid");
            if (File.Exists(root)) throw new ContractException("operation_store_collision");
            if (!Directory.Exists(root)) Directory.CreateDirectory(root);
            RejectReparsePoint(root);
            var recordPath = Path.GetFullPath(Path.Combine(root, RecordFileName));
            var lockPath = Path.GetFullPath(Path.Combine(root, LockFileName));
            if (!string.Equals(Path.GetDirectoryName(recordPath), root, StringComparison.OrdinalIgnoreCase) || !string.Equals(Path.GetDirectoryName(lockPath), root, StringComparison.OrdinalIgnoreCase))
                throw new ContractException("operation_store_root_invalid");
            if (File.Exists(recordPath)) RejectReparsePoint(recordPath);
            if (File.Exists(lockPath)) RejectReparsePoint(lockPath);
            var paths = new StorePaths(root, recordPath, lockPath);
            ValidateStoreContents(paths);
            EnsureOwnerPrivateAccess(new DirectoryInfo(root));
            return paths;
        }
        catch (ContractException)
        {
            throw;
        }
        catch
        {
            throw new ContractException("operation_store_root_invalid");
        }
    }

    private static void ValidateStoreContents(StorePaths paths)
    {
        foreach (var entry in Directory.EnumerateFileSystemEntries(paths.Root, "*", SearchOption.TopDirectoryOnly))
        {
            var name = Path.GetFileName(entry);
            if (!string.Equals(name, RecordFileName, StringComparison.Ordinal) && !string.Equals(name, LockFileName, StringComparison.Ordinal))
                throw new ContractException("operation_store_foreign_content");
            RejectReparsePoint(entry);
        }
    }

    private static FileStream AcquireExclusiveLock(string lockPath)
    {
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (true)
        {
            try
            {
                var stream = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None, 1, FileOptions.WriteThrough);
                try
                {
                    EnsureOwnerPrivateAccess(new FileInfo(lockPath));
                    return stream;
                }
                catch
                {
                    stream.Dispose();
                    throw;
                }
            }
            catch (IOException) when (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(10);
            }
            catch (ContractException)
            {
                throw;
            }
            catch
            {
                throw new ContractException("operation_store_lock_failed");
            }
            if (DateTime.UtcNow >= deadline) throw new ContractException("operation_store_lock_unavailable");
        }
    }

    private static void RejectReparseTraversal(string fullPath)
    {
        var volumeRoot = Path.GetPathRoot(fullPath) ?? throw new ContractException("operation_store_root_invalid");
        var current = volumeRoot;
        foreach (var segment in Path.GetRelativePath(volumeRoot, fullPath).Split(Path.DirectorySeparatorChar, StringSplitOptions.RemoveEmptyEntries))
        {
            current = Path.Combine(current, segment);
            RejectReparsePoint(current);
        }
    }

    private static void RejectReparsePoint(string path)
    {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new ContractException("operation_store_reparse_rejected");
    }

    private static void EnsureOwnerPrivateAccess(FileSystemInfo info)
    {
        try
        {
            if (OperatingSystem.IsWindows())
            {
                EnsureWindowsOwnerPrivateAccess(info);
                return;
            }

            var expectedMode = info is DirectoryInfo
                ? UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute
                : UnixFileMode.UserRead | UnixFileMode.UserWrite;
            File.SetUnixFileMode(info.FullName, expectedMode);
            if (File.GetUnixFileMode(info.FullName) != expectedMode)
                throw new ContractException("operation_store_access_invalid");
        }
        catch (ContractException)
        {
            throw;
        }
        catch
        {
            throw new ContractException("operation_store_access_invalid");
        }
    }

    [SupportedOSPlatform("windows")]
    private static void EnsureWindowsOwnerPrivateAccess(FileSystemInfo info)
    {
        var owner = WindowsIdentity.GetCurrent().User ?? throw new ContractException("operation_store_access_invalid");
        var system = new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null);
        var administrators = new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null);
        FileSystemSecurity security = info switch
        {
            DirectoryInfo directory => directory.GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access),
            FileInfo file => file.GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access),
            _ => throw new ContractException("operation_store_access_invalid"),
        };
        if (security.GetOwner(typeof(SecurityIdentifier)) is not SecurityIdentifier initialOwner || !initialOwner.Equals(owner))
            throw new ContractException("operation_store_access_invalid");
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        foreach (var rule in security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>().ToArray())
            security.RemoveAccessRuleAll(rule);
        var inheritance = info is DirectoryInfo ? InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit : InheritanceFlags.None;
        security.AddAccessRule(new FileSystemAccessRule(owner, FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
        security.AddAccessRule(new FileSystemAccessRule(system, FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
        security.AddAccessRule(new FileSystemAccessRule(administrators, FileSystemRights.FullControl, inheritance, PropagationFlags.None, AccessControlType.Allow));
        switch (info)
        {
            case DirectoryInfo directory:
                directory.SetAccessControl((DirectorySecurity)security);
                break;
            case FileInfo file:
                file.SetAccessControl((FileSecurity)security);
                break;
        }
        FileSystemSecurity verified = info switch
        {
            DirectoryInfo directory => directory.GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access),
            FileInfo file => file.GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access),
            _ => throw new ContractException("operation_store_access_invalid"),
        };
        var allowed = new HashSet<SecurityIdentifier> { owner, system, administrators };
        var verifiedOwner = verified.GetOwner(typeof(SecurityIdentifier)) as SecurityIdentifier;
        if (!verified.AreAccessRulesProtected || verifiedOwner is null || !verifiedOwner.Equals(owner) ||
            verified.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>()
                .Any(rule => rule.IdentityReference is not SecurityIdentifier sid || !allowed.Contains(sid) || rule.AccessControlType != AccessControlType.Allow))
            throw new ContractException("operation_store_access_invalid");
    }

    private static void ValidateSnapshot(LauncherOperation operation, ServiceGraph graph, string expectedBindingSha256)
    {
        OperationCoordinator.ValidateInputs(operation.OperationId, operation.GraphSha256, operation.BindingSha256);
        OperationCoordinator.ValidateInputs("lop_00000000", graph.Sha256, expectedBindingSha256);
        if (operation.SchemaVersion != "launcher_operation.v1" || operation.GraphSha256 != graph.Sha256 || operation.BindingSha256 != expectedBindingSha256)
            throw new ContractException("operation_store_identity_mismatch");
        if (operation.Intent is not ("start" or "stop") || operation.Revision < 0 ||
            !AllowedPhases.Contains(operation.Phase) || !AllowedReasons.Contains(operation.Reason) || !AllowedCleanup.Contains(operation.Cleanup))
            throw new ContractException("operation_store_record_invalid");
        var expectedServices = graph.Services.Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal).ToArray();
        var actualServices = operation.Services.Select(item => item.ServiceId).OrderBy(item => item, StringComparer.Ordinal).ToArray();
        if (!actualServices.SequenceEqual(expectedServices, StringComparer.Ordinal) || actualServices.Length != actualServices.Distinct(StringComparer.Ordinal).Count() ||
            operation.Services.Any(item => !AllowedServiceStates.Contains(item.State)) ||
            operation.ResidueServiceIds.Count != operation.ResidueServiceIds.Distinct(StringComparer.Ordinal).Count() ||
            operation.ResidueServiceIds.Any(item => !expectedServices.Contains(item, StringComparer.Ordinal)))
            throw new ContractException("operation_store_record_invalid");

        var specs = graph.Services.ToDictionary(item => item.ServiceId, StringComparer.Ordinal);
        var states = operation.Services.ToDictionary(item => item.ServiceId, item => item.State, StringComparer.Ordinal);
        var residue = operation.ResidueServiceIds.ToHashSet(StringComparer.Ordinal);
        foreach (var (serviceId, state) in states)
        {
            var spec = specs[serviceId];
            if (spec.Requirement == "required" && state == ServiceState.OptionalAbsent) InvalidSnapshot();
            if (spec.Requirement != "optional" && state == ServiceState.OptionalAbsent) InvalidSnapshot();
            if (spec.Ownership == "external")
            {
                if (state is not (ServiceState.Pending or ServiceState.ExternalReady) || residue.Contains(serviceId)) InvalidSnapshot();
            }
            else if (state == ServiceState.ExternalReady)
            {
                InvalidSnapshot();
            }
            if (state is ServiceState.Residue or ServiceState.Unknown && !residue.Contains(serviceId)) InvalidSnapshot();
        }
        foreach (var serviceId in residue)
        {
            if (specs[serviceId].Ownership != "owned" || states[serviceId] is not (ServiceState.Residue or ServiceState.Unknown or ServiceState.Stopped))
                InvalidSnapshot();
        }

        var ownedStates = graph.Services.Where(item => item.Ownership == "owned").Select(item => states[item.ServiceId]).ToArray();
        var allInitial = operation.Services.All(item => item.State == ServiceState.Pending);
        var allOwnedClear = ownedStates.All(state => state is ServiceState.Stopped or ServiceState.OptionalAbsent);
        var outstandingOwned = graph.Services.Where(item => item.Ownership == "owned" && states[item.ServiceId] is not (
            ServiceState.Pending or ServiceState.Stopped or ServiceState.OptionalAbsent)).Select(item => item.ServiceId).ToArray();
        var allReady = graph.Services.All(spec => spec.Requirement switch
        {
            "required" => states[spec.ServiceId] == ServiceState.Ready,
            "optional" => states[spec.ServiceId] is ServiceState.Ready or ServiceState.OptionalAbsent,
            "external" => states[spec.ServiceId] == ServiceState.ExternalReady,
            _ => false,
        });
        var startDependenciesSatisfied = operation.Services.Where(item => item.State != ServiceState.Pending).All(item =>
            specs[item.ServiceId].Dependencies.All(dependencyId => specs[dependencyId].Requirement switch
            {
                "required" => states[dependencyId] == ServiceState.Ready,
                "optional" => states[dependencyId] is ServiceState.Ready or ServiceState.OptionalAbsent,
                "external" => states[dependencyId] == ServiceState.ExternalReady,
                _ => false,
            }));

        if (operation.Cleanup == CleanupClass.Clear &&
            (operation.Phase is not (OperationPhase.Failed or OperationPhase.Stopped) || !allOwnedClear || residue.Count != 0 || operation.RollbackRequired || operation.RecoveryRequired))
            InvalidSnapshot();
        if (!CleanupMatchesPhase(operation.Phase, operation.Cleanup)) InvalidSnapshot();
        if (operation.RollbackRequired != (operation.Phase == OperationPhase.RollingBack) ||
            operation.RecoveryRequired != (operation.Phase is OperationPhase.Recovering or OperationPhase.Residue))
            InvalidSnapshot();

        switch (operation.Phase)
        {
            case OperationPhase.Planned:
            case OperationPhase.Preflight:
            case OperationPhase.Prepared:
                if (operation.Intent != "start" || !allInitial || residue.Count != 0 || !ActiveReasonAllowed(operation.Reason)) InvalidSnapshot();
                break;
            case OperationPhase.Starting:
            case OperationPhase.WaitingReady:
                if (operation.Intent != "start" || residue.Count != 0 || operation.Services.Any(item => item.State is not (
                    ServiceState.Pending or ServiceState.Starting or ServiceState.Ready or ServiceState.OptionalAbsent or ServiceState.ExternalReady)) ||
                    !startDependenciesSatisfied || !ActiveReasonAllowed(operation.Reason)) InvalidSnapshot();
                break;
            case OperationPhase.Ready:
                if (operation.Intent != "start" || !allReady || !startDependenciesSatisfied || residue.Count != 0 || !ActiveReasonAllowed(operation.Reason)) InvalidSnapshot();
                break;
            case OperationPhase.RollingBack:
                if (operation.Intent != "start" || operation.Reason == OperationReason.None || residue.Count != 0) InvalidSnapshot();
                break;
            case OperationPhase.Failed:
                if (operation.Intent != "start" || operation.Reason == OperationReason.None) InvalidSnapshot();
                break;
            case OperationPhase.Stopping:
                if (operation.Intent != "stop" || residue.Count != 0 || ownedStates.Any(state => state is not (
                    ServiceState.StopRequested or ServiceState.Stopped or ServiceState.OptionalAbsent))) InvalidSnapshot();
                break;
            case OperationPhase.Stopped:
                if (operation.Intent != "stop") InvalidSnapshot();
                break;
            case OperationPhase.Recovering:
                if (operation.Reason == OperationReason.None || residue.Count != 0) InvalidSnapshot();
                break;
            case OperationPhase.Residue:
                if (operation.Reason == OperationReason.None || residue.Count == 0 || outstandingOwned.Any(item => !residue.Contains(item))) InvalidSnapshot();
                break;
        }
    }

    private static bool CleanupMatchesPhase(string phase, string cleanup) => cleanup switch
    {
        CleanupClass.NotStarted => phase is OperationPhase.Planned or OperationPhase.Preflight or OperationPhase.Prepared or OperationPhase.Starting or OperationPhase.WaitingReady or OperationPhase.Ready,
        CleanupClass.InProgress => phase is OperationPhase.RollingBack or OperationPhase.Stopping or OperationPhase.Recovering,
        CleanupClass.Clear => phase is OperationPhase.Failed or OperationPhase.Stopped,
        CleanupClass.Residue => phase == OperationPhase.Residue,
        CleanupClass.Unknown => phase is OperationPhase.Recovering or OperationPhase.Residue,
        _ => false,
    };

    private static bool ActiveReasonAllowed(string reason) => reason is OperationReason.None or OperationReason.InvalidEvent;

    private static void InvalidSnapshot() => throw new ContractException("operation_store_record_invalid");

    private static readonly HashSet<string> AllowedPhases = new(StringComparer.Ordinal)
    {
        OperationPhase.Planned, OperationPhase.Preflight, OperationPhase.Prepared, OperationPhase.Starting,
        OperationPhase.WaitingReady, OperationPhase.Ready, OperationPhase.RollingBack, OperationPhase.Failed,
        OperationPhase.Stopping, OperationPhase.Stopped, OperationPhase.Recovering, OperationPhase.Residue,
    };
    private static readonly HashSet<string> AllowedReasons = new(StringComparer.Ordinal)
    {
        OperationReason.None, OperationReason.PreflightFailed, OperationReason.SpawnFailed, OperationReason.EarlyExit,
        OperationReason.ListenerMismatch, OperationReason.ReadinessTimeout, OperationReason.RollbackFailed,
        OperationReason.StopFailed, OperationReason.SupervisorCrash, OperationReason.ResiduePresent, OperationReason.InvalidEvent,
    };
    private static readonly HashSet<string> AllowedCleanup = new(StringComparer.Ordinal)
    {
        CleanupClass.NotStarted, CleanupClass.InProgress, CleanupClass.Clear, CleanupClass.Residue, CleanupClass.Unknown,
    };
    private static readonly HashSet<string> AllowedServiceStates = new(StringComparer.Ordinal)
    {
        ServiceState.Pending, ServiceState.Starting, ServiceState.Ready, ServiceState.OptionalAbsent,
        ServiceState.ExternalReady, ServiceState.StopRequested, ServiceState.Stopped, ServiceState.Failed,
        ServiceState.Residue, ServiceState.Unknown,
    };
}
