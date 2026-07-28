using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.AccessControl;

namespace Sword.LauncherSupervisor;

internal static class SelfTests
{
    private const string ZeroSha = "0000000000000000000000000000000000000000000000000000000000000000";

    public static int Run(string repositoryRoot)
    {
        var graphPath = Paths.Graph(repositoryRoot);
        var graph = GraphLoader.Load(graphPath);
        var tests = new (string Name, Action Test)[]
        {
            ("graph_and_legacy_drift", () => DriftValidator.Validate(repositoryRoot, graph)),
            ("binding_deterministic", () => Assert(BindingGenerator.Render(repositoryRoot, graph) == BindingGenerator.Render(repositoryRoot, graph), "binding_nondeterministic")),
            ("planned_preflight_prepared_lifecycle", () => TestLifecycle(graph)),
            ("operation_identity_rejected", () => TestOperationIdentity(graph)),
            ("atomic_operation_record", () => TestAtomicOperationRecord(graph)),
            ("persisted_preflight_failure_zero_resource", () => TestPersistedPreflightFailure(graph)),
            ("concurrent_start_single_authority", () => TestConcurrentStart(graph)),
            ("operation_store_boundary_rejected", () => TestOperationStoreBoundary(graph)),
            ("corrupt_operation_snapshot_rejected", () => TestCorruptOperationSnapshots(graph)),
            ("corrupt_operation_dependency_snapshot_rejected", () => TestCorruptOperationDependencySnapshots(graph)),
            ("corrupt_active_reason_snapshot_rejected", () => TestCorruptActiveReasonSnapshot(graph)),
            ("operation_store_fixed_error_surface", () => TestOperationStoreFixedErrorSurface(repositoryRoot)),
            ("same_start_joins_active", () => TestJoin(graph)),
            ("standard_port_authority", () => TestPortAuthority(graph)),
            ("spawn_failure_rollback", () => TestFailure(graph, "spawn_failed", OperationReason.SpawnFailed)),
            ("early_exit_rollback", () => TestFailure(graph, "early_exit", OperationReason.EarlyExit)),
            ("listener_mismatch_rollback", () => TestFailure(graph, "listener_mismatch", OperationReason.ListenerMismatch)),
            ("readiness_timeout_rollback", () => TestFailure(graph, "readiness_timeout", OperationReason.ReadinessTimeout)),
            ("partial_rollback_residue", () => TestPartialRollback(graph)),
            ("rollback_cleanup_requires_stopped", () => TestRollbackCleanup(graph)),
            ("wrong_phase_rejected", () => TestWrongPhase(graph)),
            ("stale_operation_rejected", () => TestStaleOperation(graph)),
            ("dependency_ready_enforced", () => TestDependencyReady(graph)),
            ("external_spawn_rejected", () => TestExternalSpawn(graph)),
            ("spawn_success_requires_request", () => TestSpawnSuccessRequiresRequest(graph)),
            ("stop_failure_residue", () => TestStopFailure(graph)),
            ("supervisor_crash_recovery", () => TestCrashRecovery(graph)),
            ("clean_crash_recovery_completed", () => TestCleanCrashRecovery(graph)),
            ("repeated_stop_idempotent", () => TestRepeatedStop(graph)),
            ("optional_camera_absent", () => TestOptionalCamera(graph)),
            ("external_voicevox", () => TestExternalVoicevox(graph)),
            ("ten_start_stop_cycles", () => TestTenCycles(graph)),
            ("unknown_field_rejected", () => TestMutatedGraph(graphPath, root => root["unknown"] = true, "graph_unknown_or_missing_field")),
            ("duplicate_service_rejected", () => TestMutatedGraph(graphPath, root => root["services"]!.AsArray().Add(root["services"]![0]!.DeepClone()), "graph_service_duplicate")),
            ("cycle_rejected", () => TestMutatedGraph(graphPath, root => root["services"]![0]!["dependencies"] = new JsonArray("thought_core_api"), "graph_dependency_cycle")),
            ("required_semantics_rejected", () => TestMutatedService(graphPath, "home_assistant_bridge", service => service["start"]!["absent_behavior"] = "optional_absent", "graph_required_semantics_invalid")),
            ("optional_semantics_rejected", () => TestMutatedService(graphPath, "mediapipe_camera_hub_stack", service => service["readiness"]!["degraded_allowed"] = false, "graph_optional_semantics_invalid")),
            ("external_semantics_rejected", () => TestMutatedService(graphPath, "voicevox", service => service["stop"]!["adapter_id"] = "legacy_owned_pid", "graph_external_semantics_invalid")),
            ("owned_semantics_rejected", () => TestMutatedService(graphPath, "home_assistant_bridge", service => service["stop"]!["adapter_id"] = "external_noop", "graph_owned_semantics_invalid")),
            ("readiness_port_semantics_rejected", () => TestMutatedService(graphPath, "thought_core_watcher", service => service["readiness"]!["success"] = "owned_identity_and_probe", "graph_readiness_port_semantics_invalid")),
            ("drift_rejected", () => TestDrift(repositoryRoot, graphPath)),
            ("port_drift_rejected", () => TestPortDrift(repositoryRoot, graphPath)),
            ("privacy_safe_snapshot", () => TestPrivacy(graph)),
        };
        var passed = 0;
        foreach (var (name, test) in tests)
        {
            try
            {
                test();
                Console.WriteLine("PASS " + name);
                passed++;
            }
            catch (ContractException exception)
            {
                Console.WriteLine("FAIL " + name + " " + exception.Code);
            }
            catch
            {
                Console.WriteLine("FAIL " + name + " internal_failure");
            }
        }
        Console.WriteLine($"SELF_TEST {passed}/{tests.Length}");
        return passed == tests.Length ? 0 : 1;
    }

    private static LauncherOperation Create(ServiceGraph graph, string id = "lop_00000001") =>
        OperationCoordinator.Start(null, id, graph.Sha256, ZeroSha, graph).Operation;

    private static LauncherOperation Preflight(ServiceGraph graph)
    {
        var operation = Create(graph);
        operation = OperationReducer.Reduce(operation, new("preflight_started", operation.OperationId), graph);
        operation = OperationReducer.Reduce(operation, new("preflight_passed", operation.OperationId), graph);
        return OperationReducer.Reduce(operation, new("start_requested", operation.OperationId), graph);
    }

    private static void TestLifecycle(ServiceGraph graph)
    {
        var operation = Create(graph);
        Assert(operation.Phase == OperationPhase.Planned && operation.Revision == 0 && operation.Cleanup == CleanupClass.NotStarted, "operation_not_preflight_safe");
        operation = OperationReducer.Reduce(operation, new("preflight_started", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Preflight, "preflight_phase_missing");
        operation = OperationReducer.Reduce(operation, new("preflight_passed", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Prepared, "prepared_phase_missing");
        operation = OperationReducer.Reduce(operation, new("start_requested", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Starting, "starting_phase_missing");
    }

    private static void TestOperationIdentity(ServiceGraph graph)
    {
        ExpectCode(() => OperationCoordinator.Start(null, "PRIVATE_SENTINEL", graph.Sha256, ZeroSha, graph), "operation_id_invalid");
        ExpectCode(() => OperationCoordinator.Start(null, "lop_00000001", "PRIVATE_SENTINEL", ZeroSha, graph), "operation_graph_sha256_invalid");
        ExpectCode(() => OperationCoordinator.Start(null, "lop_00000001", graph.Sha256, "PRIVATE_SENTINEL", graph), "operation_binding_sha256_invalid");
        ExpectCode(() => OperationCoordinator.Start(null, "lop_00000001", ZeroSha, ZeroSha, graph), "operation_graph_identity_mismatch");
    }

    private static void TestAtomicOperationRecord(ServiceGraph graph)
    {
        var root = Path.Combine(Path.GetTempPath(), "launcher-operation-" + Guid.NewGuid().ToString("N"));
        var storeRoot = Path.Combine(root, "launcher-operation.v1");
        var path = Path.Combine(storeRoot, "launcher-operation.v1.json");
        try
        {
            Directory.CreateDirectory(root);
            var sentinelPath = Path.Combine(root, "parent-sentinel.txt");
            File.WriteAllText(sentinelPath, "parent-unchanged");
            var parentSecurity = SecurityFingerprint(root);
            var started = OperationStore.StartAndPersist("lop_10000001", graph.Sha256, ZeroSha, graph, root);
            var persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(persisted.Phase == OperationPhase.Planned && persisted.Revision == 0, "operation_not_persisted_before_preflight");
            Assert(File.ReadAllText(sentinelPath) == "parent-unchanged" && SecurityFingerprint(root) == parentSecurity,
                "operation_store_parent_mutated");
            var publicBytes = File.ReadAllText(path);
            Assert(!publicBytes.Contains(root, StringComparison.OrdinalIgnoreCase), "operation_store_private_path_leaked");

            var joined = OperationStore.StartAndPersist("lop_10000002", graph.Sha256, ZeroSha, graph, root);
            persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(started.Operation.OperationId == persisted.OperationId && joined.JoinedExisting && persisted.JoinedExisting, "operation_join_not_persisted");

            persisted = OperationStore.ReduceAndPersist(persisted, new("preflight_started", persisted.OperationId), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("preflight_passed", persisted.OperationId), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("start_requested", persisted.OperationId), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("spawn_requested", persisted.OperationId, "home_assistant_bridge"), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("spawn_succeeded", persisted.OperationId, "home_assistant_bridge"), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("early_exit", persisted.OperationId, "home_assistant_bridge"), graph, root);
            persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(persisted.Reason == OperationReason.EarlyExit && persisted.Phase == OperationPhase.RollingBack, "first_failure_not_persisted");
            persisted = OperationStore.ReduceAndPersist(persisted, new("service_stopped", persisted.OperationId, "home_assistant_bridge"), graph, root);
            persisted = OperationStore.ReduceAndPersist(persisted, new("rollback_completed", persisted.OperationId), graph, root);
            persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(persisted.Phase == OperationPhase.Failed && persisted.Cleanup == CleanupClass.Clear && persisted.Reason == OperationReason.EarlyExit, "restart_read_not_finalized");

            var beforeStale = File.ReadAllBytes(path);
            var beforeCreated = File.GetCreationTimeUtc(path);
            var beforeWritten = File.GetLastWriteTimeUtc(path);
            var beforeRootWritten = Directory.GetLastWriteTimeUtc(root);
            var beforeStoreWritten = Directory.GetLastWriteTimeUtc(storeRoot);
            var revision = persisted.Revision;
            persisted = OperationStore.ReduceAndPersist(persisted, new("stop_requested", "lop_99999999"), graph, root);
            Assert(persisted.Revision == revision && File.ReadAllBytes(path).SequenceEqual(beforeStale) &&
                File.GetCreationTimeUtc(path) == beforeCreated && File.GetLastWriteTimeUtc(path) == beforeWritten &&
                Directory.GetLastWriteTimeUtc(root) == beforeRootWritten && Directory.GetLastWriteTimeUtc(storeRoot) == beforeStoreWritten,
                "stale_operation_persisted_mutation");
            var nonexistentRoot = Path.Combine(Path.GetTempPath(), "foreign-operation-" + Guid.NewGuid().ToString("N"));
            var foreign = OperationStore.ReduceAndPersist(persisted, new("stop_requested", "lop_99999998"), graph, nonexistentRoot);
            Assert(ReferenceEquals(foreign, persisted) && !Directory.Exists(nonexistentRoot), "foreign_operation_touched_store");

            persisted = OperationStore.StartAndPersist("lop_10000003", graph.Sha256, ZeroSha, graph, root).Operation;
            persisted = OperationStore.Read(root, graph, ZeroSha);
            persisted = OperationStore.ReduceAndPersist(persisted, new("supervisor_crashed", persisted.OperationId), graph, root);
            persisted = OperationStore.Read(root, graph, ZeroSha);
            persisted = OperationStore.ReduceAndPersist(persisted, new("recovery_completed", persisted.OperationId), graph, root);
            persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(persisted.Phase == OperationPhase.Failed && persisted.Cleanup == CleanupClass.Clear, "restart_recovery_not_closed");
            Assert(Directory.GetFiles(storeRoot, "*.tmp", SearchOption.TopDirectoryOnly).Length == 0, "operation_store_temp_residue");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void TestConcurrentStart(ServiceGraph graph)
    {
        var root = Path.Combine(Path.GetTempPath(), "launcher-concurrent-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(root);
            using var gate = new ManualResetEventSlim(false);
            var first = Task.Run(() => { gate.Wait(); return OperationStore.StartAndPersist("lop_20000001", graph.Sha256, ZeroSha, graph, root); });
            var second = Task.Run(() => { gate.Wait(); return OperationStore.StartAndPersist("lop_20000002", graph.Sha256, ZeroSha, graph, root); });
            gate.Set();
            Task.WaitAll(first, second);
            var persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(first.Result.Operation.OperationId == second.Result.Operation.OperationId && persisted.OperationId == first.Result.Operation.OperationId,
                "concurrent_start_split_authority");
            Assert(new[] { first.Result.JoinedExisting, second.Result.JoinedExisting }.Count(item => item) == 1 && persisted.JoinedExisting,
                "concurrent_start_join_missing");
            var storeRoot = Path.Combine(root, "launcher-operation.v1");
            Assert(Directory.GetFiles(storeRoot, "launcher-operation.v1.json", SearchOption.TopDirectoryOnly).Length == 1, "concurrent_start_record_count");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void TestPersistedPreflightFailure(ServiceGraph graph)
    {
        var root = Path.Combine(Path.GetTempPath(), "launcher-preflight-failure-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(root);
            var operation = OperationStore.StartAndPersist("lop_25000001", graph.Sha256, ZeroSha, graph, root).Operation;
            operation = OperationStore.ReduceAndPersist(operation, new("preflight_started", operation.OperationId), graph, root);
            operation = OperationStore.ReduceAndPersist(operation, new("preflight_failed", operation.OperationId), graph, root);
            var persisted = OperationStore.Read(root, graph, ZeroSha);
            Assert(persisted.Phase == OperationPhase.Failed && persisted.Reason == OperationReason.PreflightFailed &&
                persisted.Cleanup == CleanupClass.Clear && persisted.ResidueServiceIds.Count == 0 &&
                persisted.Services.Where(item => graph.Services.Single(spec => spec.ServiceId == item.ServiceId).Ownership == "owned")
                    .All(item => item.State == ServiceState.Stopped), "preflight_failure_not_zero_resource_clear");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void TestOperationStoreBoundary(ServiceGraph graph)
    {
        var collisionRoot = Path.Combine(Path.GetTempPath(), "launcher-collision-" + Guid.NewGuid().ToString("N"));
        var foreignRoot = Path.Combine(Path.GetTempPath(), "launcher-foreign-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(collisionRoot);
            File.WriteAllText(Path.Combine(collisionRoot, "launcher-operation.v1"), "foreign");
            ExpectCode(() => OperationStore.StartAndPersist("lop_30000001", graph.Sha256, ZeroSha, graph, collisionRoot), "operation_store_collision");

            Directory.CreateDirectory(foreignRoot);
            var child = Path.Combine(foreignRoot, "launcher-operation.v1");
            Directory.CreateDirectory(child);
            var foreign = Path.Combine(child, "foreign.txt");
            File.WriteAllText(foreign, "foreign-unchanged");
            var security = SecurityFingerprint(child);
            ExpectCode(() => OperationStore.StartAndPersist("lop_30000002", graph.Sha256, ZeroSha, graph, foreignRoot), "operation_store_foreign_content");
            Assert(File.ReadAllText(foreign) == "foreign-unchanged" && SecurityFingerprint(child) == security,
                "operation_store_foreign_content_mutated");
        }
        finally
        {
            if (Directory.Exists(collisionRoot)) Directory.Delete(collisionRoot, recursive: true);
            if (Directory.Exists(foreignRoot)) Directory.Delete(foreignRoot, recursive: true);
        }
    }

    private static void TestCorruptOperationSnapshots(ServiceGraph graph)
    {
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Failed;
            root["reason"] = OperationReason.PreflightFailed;
            root["cleanup"] = CleanupClass.Clear;
            ServiceNode(root, "home_assistant_bridge")["state"] = ServiceState.Ready;
        });
        ExpectCorruptSnapshot(graph, root => ServiceNode(root, "home_assistant_bridge")["state"] = ServiceState.OptionalAbsent);
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Residue;
            root["reason"] = OperationReason.ResiduePresent;
            root["cleanup"] = CleanupClass.Residue;
            root["recovery_required"] = true;
            ServiceNode(root, "voicevox")["state"] = ServiceState.Residue;
            root["residue_service_ids"] = new JsonArray("voicevox");
        });
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Ready;
            root["cleanup"] = CleanupClass.NotStarted;
        });
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Residue;
            root["reason"] = OperationReason.ResiduePresent;
            root["cleanup"] = CleanupClass.Residue;
            root["recovery_required"] = true;
        });
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Residue;
            root["reason"] = OperationReason.ResiduePresent;
            root["cleanup"] = CleanupClass.Residue;
            root["recovery_required"] = true;
            ServiceNode(root, "home_assistant_bridge")["state"] = ServiceState.Residue;
            ServiceNode(root, "aituber_kit")["state"] = ServiceState.Ready;
            root["residue_service_ids"] = new JsonArray("home_assistant_bridge");
        });
    }

    private static void ExpectCorruptSnapshot(ServiceGraph graph, Action<JsonObject> mutation)
    {
        var root = Path.Combine(Path.GetTempPath(), "launcher-corrupt-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(root);
            OperationStore.StartAndPersist("lop_40000001", graph.Sha256, ZeroSha, graph, root);
            var recordPath = Path.Combine(root, "launcher-operation.v1", "launcher-operation.v1.json");
            var record = JsonNode.Parse(File.ReadAllText(recordPath))!.AsObject();
            mutation(record);
            File.WriteAllText(recordPath, record.ToJsonString());
            ExpectCode(() => OperationStore.Read(root, graph, ZeroSha), "operation_store_record_invalid");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void TestCorruptOperationDependencySnapshots(ServiceGraph graph)
    {
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Starting;
            ServiceNode(root, "thought_core_api")["state"] = ServiceState.Ready;
        });
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.WaitingReady;
            ServiceNode(root, "vision_snapshot_processor")["state"] = ServiceState.Ready;
        });
    }

    private static void TestCorruptActiveReasonSnapshot(ServiceGraph graph)
    {
        ExpectCorruptSnapshot(graph, root =>
        {
            root["phase"] = OperationPhase.Starting;
            root["reason"] = OperationReason.SpawnFailed;
        });
    }

    private static JsonNode ServiceNode(JsonObject operation, string serviceId) =>
        operation["services"]!.AsArray().Single(item => item!["service_id"]!.GetValue<string>() == serviceId)!;

    private static string SecurityFingerprint(string path)
    {
        if (OperatingSystem.IsWindows())
            return new DirectoryInfo(path).GetAccessControl(AccessControlSections.Owner | AccessControlSections.Access)
                .GetSecurityDescriptorSddlForm(AccessControlSections.Owner | AccessControlSections.Access);
        return File.GetUnixFileMode(path).ToString();
    }

    private static void TestOperationStoreFixedErrorSurface(string repositoryRoot)
    {
        var source = File.ReadAllText(Path.Combine(repositoryRoot, "tools", "launcher-supervisor", "OperationReducer.cs"));
        Assert(!source.Contains("operation_store_access_invalid_", StringComparison.Ordinal) &&
            !source.Contains("exception.GetType().Name", StringComparison.Ordinal), "operation_store_dynamic_error_surface");
    }

    private static void TestJoin(ServiceGraph graph)
    {
        var active = Preflight(graph);
        var joined = OperationCoordinator.Start(active, "lop_00000002", graph.Sha256, ZeroSha, graph);
        Assert(joined.JoinedExisting && joined.Operation.OperationId == active.OperationId && joined.Operation.JoinedExisting && joined.Operation.Revision == active.Revision + 1, "start_not_joined");
    }

    private static void TestPortAuthority(ServiceGraph graph)
    {
        var expected = new Dictionary<string, (string Mode, string Transport, int? Port)>(StringComparer.Ordinal)
        {
            ["aituber_kit"] = ("manifest_default", "http", 3000),
            ["environment_state_server"] = ("manifest_default", "http", 8790),
            ["home_assistant_bridge"] = ("manifest_default", "http", 8787),
            ["mediapipe_camera_hub_stack"] = ("manifest_default", "websocket", 8765),
            ["openai_provider_broker"] = ("manifest_default", "http", 18786),
            ["thought_core_api"] = ("manifest_default", "http", 18787),
            ["thought_core_watcher"] = ("none", "none", null),
            ["touchdesigner_control_gui"] = ("manifest_default", "http", 8788),
            ["vision_snapshot_processor"] = ("manifest_default", "websocket", 8776),
            ["voicevox"] = ("launcher_default", "http", 50021),
        };
        foreach (var service in graph.Services)
        {
            var port = expected[service.ServiceId];
            Assert(service.Port.PortMode == port.Mode && service.Port.Transport == port.Transport && service.Port.LoopbackPort == port.Port, "port_authority_invalid");
        }
    }

    private static void TestFailure(ServiceGraph graph, string eventType, string reason)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new(eventType, operation.OperationId, "home_assistant_bridge"), graph);
        Assert(operation.Phase == OperationPhase.RollingBack && operation.Reason == reason && operation.RollbackRequired, "failure_not_rollback");
    }

    private static void TestPartialRollback(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new("spawn_failed", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new("rollback_failed", operation.OperationId, "aituber_kit"), graph);
        Assert(operation.Phase == OperationPhase.Residue && operation.Reason == OperationReason.SpawnFailed &&
            operation.ResidueServiceIds.SequenceEqual(new[] { "aituber_kit", "home_assistant_bridge" }), "rollback_residue_missing");
    }

    private static void TestRollbackCleanup(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new("spawn_succeeded", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new("early_exit", operation.OperationId, "home_assistant_bridge"), graph);
        operation = OperationReducer.Reduce(operation, new("rollback_completed", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Residue && operation.Cleanup == CleanupClass.Unknown && operation.Reason == OperationReason.EarlyExit && operation.ResidueServiceIds.SequenceEqual(new[] { "home_assistant_bridge" }), "rollback_cleanup_overstated");
        operation = OperationReducer.Reduce(operation, new("residue_cleared", operation.OperationId, "home_assistant_bridge"), graph);
        Assert(operation.Phase == OperationPhase.Failed && operation.Cleanup == CleanupClass.Clear && operation.Reason == OperationReason.EarlyExit, "rollback_cleanup_not_finalized");
    }

    private static void TestWrongPhase(ServiceGraph graph)
    {
        var operation = Create(graph);
        var invalid = OperationReducer.Reduce(operation, new("service_ready", operation.OperationId, "thought_core_api"), graph);
        Assert(invalid.Phase == OperationPhase.Planned && invalid.Reason == OperationReason.InvalidEvent, "wrong_phase_accepted");
    }

    private static void TestStaleOperation(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        var invalid = OperationReducer.Reduce(operation, new("spawn_requested", "lop_99999999", "thought_core_api"), graph);
        Assert(ReferenceEquals(invalid, operation) && invalid.Revision == operation.Revision && invalid.Reason == operation.Reason, "stale_operation_mutated");
    }

    private static void TestDependencyReady(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        var invalid = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, "thought_core_api"), graph);
        Assert(invalid.Phase == OperationPhase.Starting && invalid.Reason == OperationReason.InvalidEvent && invalid.Services.Single(item => item.ServiceId == "thought_core_api").State == ServiceState.Pending, "dependency_not_enforced");
    }

    private static void TestExternalSpawn(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        var invalid = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, "voicevox"), graph);
        Assert(invalid.Reason == OperationReason.InvalidEvent && invalid.Services.Single(item => item.ServiceId == "voicevox").State == ServiceState.Pending, "external_spawn_accepted");
    }

    private static void TestSpawnSuccessRequiresRequest(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        var invalid = OperationReducer.Reduce(operation, new("spawn_succeeded", operation.OperationId, "home_assistant_bridge"), graph);
        Assert(invalid.Reason == OperationReason.InvalidEvent && invalid.Services.Single(item => item.ServiceId == "home_assistant_bridge").State == ServiceState.Pending, "spawn_success_without_request");
    }

    private static void TestStopFailure(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("stop_requested", operation.OperationId), graph);
        operation = OperationReducer.Reduce(operation, new("stop_failed", operation.OperationId, "thought_core_api"), graph);
        Assert(operation.Phase == OperationPhase.Residue && operation.Cleanup == CleanupClass.Residue && operation.Reason == OperationReason.StopFailed, "stop_failure_not_retained");
        operation = OperationReducer.Reduce(operation, new("residue_cleared", operation.OperationId, "thought_core_api"), graph);
        Assert(operation.Phase == OperationPhase.Residue && operation.Cleanup == CleanupClass.Unknown && operation.ResidueServiceIds.Count > 0, "stop_cleanup_overstated");
    }

    private static void TestCrashRecovery(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("supervisor_crashed", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Recovering && operation.RecoveryRequired && operation.Cleanup == CleanupClass.Unknown, "crash_not_recoverable");
        operation = OperationReducer.Reduce(operation, new("residue_observed", operation.OperationId, "aituber_kit"), graph);
        operation = OperationReducer.Reduce(operation, new("residue_cleared", operation.OperationId, "aituber_kit"), graph);
        Assert(operation.Phase == OperationPhase.Failed && operation.Reason == OperationReason.SupervisorCrash && operation.Cleanup == CleanupClass.Clear && !operation.RecoveryRequired, "recovery_not_finalized");
    }

    private static void TestCleanCrashRecovery(ServiceGraph graph)
    {
        var operation = Create(graph);
        operation = OperationReducer.Reduce(operation, new("supervisor_crashed", operation.OperationId), graph);
        operation = OperationReducer.Reduce(operation, new("recovery_completed", operation.OperationId), graph);
        Assert(operation.Phase == OperationPhase.Failed && operation.Cleanup == CleanupClass.Clear && operation.Reason == OperationReason.SupervisorCrash && !operation.RecoveryRequired, "clean_recovery_not_closed");
    }

    private static void TestRepeatedStop(ServiceGraph graph)
    {
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("stop_requested", operation.OperationId), graph);
        foreach (var service in graph.Services.Where(item => item.Ownership == "owned"))
            operation = OperationReducer.Reduce(operation, new("service_stopped", operation.OperationId, service.ServiceId), graph);
        Assert(operation.Phase == OperationPhase.Stopped, "stop_not_complete");
        var repeated = OperationReducer.Reduce(operation, new("stop_requested", operation.OperationId), graph);
        Assert(ReferenceEquals(operation, repeated), "stop_not_idempotent");
    }

    private static void TestOptionalCamera(ServiceGraph graph)
    {
        var camera = graph.Services.Single(item => item.ServiceId == "mediapipe_camera_hub_stack");
        Assert(camera.Requirement == "optional" && camera.Start.AbsentBehavior == "optional_absent", "camera_not_optional");
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("optional_absent", operation.OperationId, camera.ServiceId), graph);
        Assert(operation.Services.Single(item => item.ServiceId == camera.ServiceId).State == ServiceState.OptionalAbsent, "camera_absent_not_retained");
    }

    private static void TestExternalVoicevox(ServiceGraph graph)
    {
        var voicevox = graph.Services.Single(item => item.ServiceId == "voicevox");
        Assert(voicevox.Requirement == "external" && voicevox.Ownership == "external" && voicevox.Stop.Escalation == "none", "voicevox_external_invalid");
        var operation = Preflight(graph);
        operation = OperationReducer.Reduce(operation, new("external_ready", operation.OperationId, voicevox.ServiceId), graph);
        Assert(operation.Services.Single(item => item.ServiceId == voicevox.ServiceId).State == ServiceState.ExternalReady, "voicevox_probe_not_input");
    }

    private static void TestTenCycles(ServiceGraph graph)
    {
        LauncherOperation? previous = null;
        for (var index = 1; index <= 10; index++)
        {
            var start = OperationCoordinator.Start(previous, $"lop_{index:00000000}", graph.Sha256, ZeroSha, graph);
            var operation = start.Operation;
            operation = OperationReducer.Reduce(operation, new("preflight_started", operation.OperationId), graph);
            operation = OperationReducer.Reduce(operation, new("preflight_passed", operation.OperationId), graph);
            operation = OperationReducer.Reduce(operation, new("start_requested", operation.OperationId), graph);
            foreach (var serviceId in GraphLoader.TopologicalOrder(graph.Services))
            {
                var service = graph.Services.Single(item => item.ServiceId == serviceId);
                if (service.Requirement == "external")
                {
                    operation = OperationReducer.Reduce(operation, new("external_ready", operation.OperationId, serviceId), graph);
                }
                else if (service.Requirement == "optional")
                {
                    operation = OperationReducer.Reduce(operation, new("optional_absent", operation.OperationId, serviceId), graph);
                }
                else
                {
                    operation = OperationReducer.Reduce(operation, new("spawn_requested", operation.OperationId, serviceId), graph);
                    operation = OperationReducer.Reduce(operation, new("spawn_succeeded", operation.OperationId, serviceId), graph);
                    operation = OperationReducer.Reduce(operation, new("service_ready", operation.OperationId, serviceId), graph);
                }
            }
            Assert(operation.Phase == OperationPhase.Ready, "cycle_not_ready");
            operation = OperationReducer.Reduce(operation, new("stop_requested", operation.OperationId), graph);
            foreach (var service in graph.Services.Where(item => item.Ownership == "owned" && item.Requirement != "optional"))
                operation = OperationReducer.Reduce(operation, new("service_stopped", operation.OperationId, service.ServiceId), graph);
            Assert(operation.Phase == OperationPhase.Stopped && operation.Cleanup == CleanupClass.Clear && operation.ResidueServiceIds.Count == 0, "cycle_not_clean");
            previous = operation;
        }
    }

    private static void TestMutatedGraph(string graphPath, Action<JsonObject> mutation, string expectedCode)
    {
        var root = JsonNode.Parse(File.ReadAllText(graphPath))!.AsObject();
        mutation(root);
        WithTemporaryJson(root, path => ExpectCode(() => GraphLoader.Load(path), expectedCode));
    }

    private static void TestMutatedService(string graphPath, string serviceId, Action<JsonNode> mutation, string expectedCode)
    {
        var root = JsonNode.Parse(File.ReadAllText(graphPath))!.AsObject();
        var service = root["services"]!.AsArray().Single(item => item!["service_id"]!.GetValue<string>() == serviceId)!;
        mutation(service);
        WithTemporaryJson(root, path => ExpectCode(() => GraphLoader.Load(path), expectedCode));
    }

    private static void TestDrift(string repositoryRoot, string graphPath)
    {
        var root = JsonNode.Parse(File.ReadAllText(graphPath))!.AsObject();
        var voicevox = root["services"]!.AsArray().Single(item => item!["service_id"]!.GetValue<string>() == "voicevox")!;
        voicevox["public_readiness_id"] = "voicevox_drift";
        WithTemporaryJson(root, path =>
        {
            var changed = GraphLoader.Load(path);
            ExpectCode(() => DriftValidator.Validate(repositoryRoot, changed), "drift_ordinary_route_readiness");
        });
    }

    private static void TestPortDrift(string repositoryRoot, string graphPath)
    {
        var root = JsonNode.Parse(File.ReadAllText(graphPath))!.AsObject();
        var home = root["services"]!.AsArray().Single(item => item!["service_id"]!.GetValue<string>() == "home_assistant_bridge")!;
        home["port"]!["loopback_port"] = 8786;
        WithTemporaryJson(root, path =>
        {
            var changed = GraphLoader.Load(path);
            ExpectCode(() => DriftValidator.Validate(repositoryRoot, changed), "drift_service_port");
        });
    }

    private static void TestPrivacy(ServiceGraph graph)
    {
        var operation = Create(graph);
        var json = JsonSerializer.Serialize(operation, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
        foreach (var prohibited in new[] { "stdout", "stderr", "exception", "command", "args", "env", "token", "secret", "path", "url", "pid", "process" })
            Assert(!json.Contains('"' + prohibited + '"', StringComparison.OrdinalIgnoreCase), "privacy_field_present");
        Assert(!json.Contains("PRIVATE_SENTINEL", StringComparison.Ordinal), "privacy_sentinel_present");
    }

    private static void WithTemporaryJson(JsonObject root, Action<string> action)
    {
        var path = Path.Combine(Path.GetTempPath(), "launcher-graph-" + Guid.NewGuid().ToString("N") + ".json");
        try
        {
            File.WriteAllText(path, root.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            action(path);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }

    private static void ExpectCode(Action action, string expected)
    {
        try { action(); }
        catch (ContractException exception) when (exception.Code == expected) { return; }
        throw new ContractException("expected_failure_missing");
    }

    private static void Assert(bool condition, string code)
    {
        if (!condition) throw new ContractException(code);
    }
}
