from __future__ import annotations

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "home-control-launcher"
SERVER = LAUNCHER / "server.js"
RUNTIME = LAUNCHER / "launcher-supervisor-runtime.js"
PRIVATE_PLAN = LAUNCHER / "launcher-private-service-plan.js"
README = LAUNCHER / "README.md"
SYSTEM = ROOT / "ops" / "scripts" / "system.ps1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


class LauncherSupervisorRuntimeContractTest(TestCase):
    def test_preflight_is_persisted_before_first_worker_exchange(self) -> None:
        runtime = read(RUNTIME)
        start = between(runtime, "  async start ({ profileId, options, configIdentity })", "  async stop ({ profileId })")

        identity_index = start.index("validatedConfigIdentity = deriveEffectiveConfigIdentity({")
        inflight_index = start.index("if (this.inflight)")
        compile_index = start.index(
            "compiled = this.compile(profileId, options, validatedConfigIdentity)"
        )
        store_index = start.index("this.store.startAndPersist(")
        preflight_started_index = start.index("this.apply('preflight_started')")
        preflight_passed_index = start.index("this.apply('preflight_passed')")
        client_index = start.index("this.ensureClient(compiled)")
        exchange_index = start.index("await this.exchange(")

        self.assertLess(identity_index, inflight_index)
        self.assertLess(inflight_index, compile_index)
        self.assertLess(compile_index, store_index)
        self.assertLess(store_index, preflight_started_index)
        self.assertLess(preflight_started_index, preflight_passed_index)
        self.assertLess(preflight_passed_index, client_index)
        self.assertLess(client_index, exchange_index)

    def test_node_owns_external_probe_rollback_and_finalization(self) -> None:
        runtime = read(RUNTIME)
        start = between(runtime, "  async start ({ profileId, options, configIdentity })", "  async stop ({ profileId })")
        external = between(
            start,
            "if (spec.ownership === 'external')",
            "} else if (spec.requirement === 'optional'",
        )
        stop_owned = between(runtime, "  async stopOwnedServices", "  async rollback")

        self.assertIn("await this.exchange(serviceId, 'probe')", external)
        self.assertNotIn("'start'", external)
        self.assertNotIn("'stop'", external)
        self.assertIn("this.apply('readiness_timeout', serviceId", external)
        self.assertIn("if (this.current.phase === reducer.PHASE.ROLLING_BACK) await this.rollback(compiled)", start)
        self.assertIn("const cleanupClear = await this.closeClientAndPlan()", stop_owned)
        self.assertLess(
            stop_owned.index("const cleanupClear = await this.closeClientAndPlan()"),
            stop_owned.index("this.privatePlanCleanup('clear')"),
        )
        self.assertIn("this.apply(workerEvent.event_type, serviceId", stop_owned)
        self.assertIn("this.cleanupUnattempted(unattemptedServiceId)", stop_owned)

    def test_recovery_records_private_plan_truth_before_terminal_events(self) -> None:
        runtime = read(RUNTIME)
        recovery = between(runtime, "  async recover ()", "  async start ({ profileId, options, configIdentity })")

        close_index = recovery.index("const planClear = await this.closeClientAndPlan()")
        plan_event_index = recovery.index("this.privatePlanCleanup(")
        gate_index = recovery.index("if (!planClear || this.current.phase !== reducer.PHASE.RECOVERING) return false")
        started_index = recovery.index("this.apply('recovery_started')")
        completed_index = recovery.index("this.apply('recovery_completed')")
        self.assertLess(close_index, plan_event_index)
        self.assertLess(plan_event_index, gate_index)
        self.assertLess(gate_index, started_index)
        self.assertLess(started_index, completed_index)

        public_projection = between(runtime, "const hasPartialTerminalCleanupProof", "const publicOperation")
        self.assertIn("return !reducer.hasTerminalPrivatePlanProof(operation)", public_projection)

    def test_launcher_routes_cannot_reach_legacy_supervisor_or_independent_kill(self) -> None:
        server = read(SERVER)
        start = between(server, "const startStack", "const runScriptAndCollect")
        stop = between(server, "const stopStack", "const reclaimManagedPortsFromLauncher")
        reclaim = between(server, "const reclaimManagedPortsFromLauncher", "const isProcessAlive")
        status_route = between(
            server,
            "requestUrl.pathname === '/api/status-script'",
            "requestUrl.pathname === '/api/shutdown'",
        )
        shutdown_route = between(
            server,
            "requestUrl.pathname === '/api/shutdown'",
            "sendJson(response, 404",
        )

        self.assertIn("await launcherRuntime.start", start)
        self.assertIn("launcherRuntime.stop", stop)
        for route in (start, stop, reclaim, status_route):
            self.assertNotIn("runScriptAndCollect", route)
            self.assertNotIn("stopProcessById", route)
            self.assertNotIn("reclaimManagedPortResidue", route)
        self.assertIn("result_class: 'independent_reclaim_retired'", reclaim)
        self.assertIn("kill_authority: false", reclaim)
        self.assertIn("status_script_execution: false", status_route)
        self.assertIn("const result = await stopStack({})", shutdown_route)
        self.assertIn("shutdown_scheduled: false", shutdown_route)
        self.assertLess(
            shutdown_route.index("const result = await stopStack({})"),
            shutdown_route.index("process.exit(0)"),
        )
        self.assertIn("const finalizeSignalShutdown = async () =>", server)
        self.assertIn("process.on('SIGINT', () => { void finalizeSignalShutdown() })", server)
        self.assertIn("process.on('SIGTERM', () => { void finalizeSignalShutdown() })", server)
        self.assertNotIn("SYSTEM_SCRIPT", server)

    def test_deterministic_worker_uses_the_strict_v2_stop_proof_shape(self) -> None:
        server = read(SERVER)
        worker = between(server, "const deterministicTestWorker", "const DETERMINISTIC_TEST_PROBE_CONFIG_SHA256")

        self.assertIn("termination_class: 'not_applicable'", worker)
        self.assertIn("job_query_class: 'not_applicable'", worker)
        self.assertIn("active_count_after: null", worker)
        self.assertIn("post_stop_listener_class: 'not_applicable'", worker)
        self.assertIn("termination_class: 'forced_only'", worker)
        self.assertIn("job_query_class: 'trusted'", worker)
        self.assertIn("active_count_after: 0", worker)

    def test_active_stop_uses_durable_operation_and_persisted_plan_only(self) -> None:
        runtime = read(RUNTIME)
        server = read(SERVER)
        stop = between(runtime, "  async stop ({ profileId })", "module.exports")
        stop_stack = between(server, "const stopStack", "const reclaimManagedPortsFromLauncher")
        stop_route = between(
            server,
            "requestUrl.pathname === '/api/stop'",
            "requestUrl.pathname === '/api/reclaim-managed-ports'",
        )

        self.assertIn("compiled = this.readPersistedPlan(this.current)", stop)
        self.assertNotIn("this.compile(", stop)
        self.assertNotIn("options", stop)
        self.assertIn("const activeOperation = operationState()", stop_stack)
        self.assertLess(
            stop_stack.index("const activeOperation = operationState()"),
            stop_stack.index("const config = readLauncherConfig()"),
        )
        self.assertIn("if (activeProfileId)", stop_stack)
        self.assertIn("return launcherRuntime.stop({ profileId })", stop_stack)
        self.assertNotIn("readLauncherConfig", stop_route)
        self.assertNotIn("requireSupervisorProfile", stop_route)

    def test_system_facade_is_loopback_only_and_has_no_legacy_fallback(self) -> None:
        system = read(SYSTEM)

        self.assertIn("Invoke-RestMethod", system)
        self.assertIn('"http://127.0.0.1:$LauncherPort"', system)
        self.assertIn("SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE", system)
        self.assertIn("launcher_api_unavailable", system)
        self.assertIn('$baseUri.Scheme -cne "http"', system)
        for retired_name in (
            "start-home-control-stack.ps1",
            "status-home-control-stack.ps1",
            "stop-home-control-stack.ps1",
        ):
            self.assertNotIn(retired_name, system)

    def test_private_plan_and_public_projection_keep_private_values_separate(self) -> None:
        runtime = read(RUNTIME)
        private_plan = read(PRIVATE_PLAN)
        public_projection = between(runtime, "const publicOperation", "const resultClassFor")

        for private_name in (
            "file_path",
            "working_directory",
            "environment",
            "arguments",
            "powershell_path",
            "private_plan",
            "private_plan_sha256",
            "worker_executable_class",
            "worker_executable_sha256",
            "pid",
        ):
            self.assertNotIn(private_name, public_projection)
        self.assertIn("raw_private_publication_flags: false", public_projection)
        self.assertIn("compilePrivateServicePlan", private_plan)
        self.assertIn("verifyTrustedWindowsWorkerExecutable", private_plan)
        self.assertIn("serializePrivateServicePlan", private_plan)
        self.assertIn("planIdentity: {", runtime)
        self.assertIn("requireCanonicalOptions", private_plan)
        self.assertIn("const fixedPorts =", private_plan)
        self.assertIn("HomeAssistantBridgeHost", private_plan)
        self.assertNotIn("service_id: 'voicevox'", private_plan)

    def test_fake_api_worker_requires_two_explicit_test_gates(self) -> None:
        server = read(SERVER)
        gate = between(server, "const TEST_FAKE_SUPERVISOR", "const deterministicTestWorker")
        worker = between(
            server,
            "const deterministicTestWorker",
            "const DETERMINISTIC_TEST_PROBE_CONFIG_SHA256",
        )
        probe = between(
            server,
            "const DETERMINISTIC_TEST_PROBE_CONFIG_SHA256",
            "const launcherRuntimeOptions",
        )
        fake_options = between(
            server,
            "if (TEST_FAKE_SUPERVISOR)",
            "const launcherRuntime =",
        )

        self.assertIn("process.env.NODE_ENV === 'test'", gate)
        self.assertIn(
            "process.env.HOME_CONTROL_LAUNCHER_TEST_FAKE_SUPERVISOR === 'deterministic_v1'",
            gate,
        )
        self.assertIn("!ALLOW_REMOTE", gate)
        self.assertIn("isTemporaryTestPath(WORKSPACE_ROOT)", gate)
        self.assertIn("isTemporaryTestPath(STATE_DIR)", gate)
        self.assertIn("schema_version: 'launcher_worker.v2'", worker)
        for field in (
            "supervisor_generation",
            "authority_lease_proof",
            "dispatch_id",
        ):
            self.assertIn(f"{field}: request.{field}", worker)
        self.assertNotIn("launcher_worker.v1", server)
        self.assertIn("configSha256: DETERMINISTIC_TEST_PROBE_CONFIG_SHA256", probe)
        self.assertIn("...expected", probe)
        self.assertIn("descriptor.success_semantic_classes[0]", probe)
        self.assertIn("ready: true", probe)
        self.assertIn("probeExecutor: deterministicTestProbeExecutor", fake_options)
        self.assertIn("probeExecutorFactory: null", fake_options)

    def test_server_injects_probe_context_from_the_runtime_compiled_plan(self) -> None:
        server = read(SERVER)
        runtime_options = between(
            server,
            "const launcherRuntimeOptions",
            "if (TEST_FAKE_SUPERVISOR)",
        )

        self.assertIn("LauncherProbeRuntimeContext", server)
        self.assertIn("probeExecutorFactory: (contextOptions) =>", runtime_options)
        self.assertIn(
            "new LauncherProbeRuntimeContext(contextOptions)",
            runtime_options,
        )
        self.assertNotIn("compilePrivateServicePlan", runtime_options)

    def test_readme_records_n2_authority_and_proof_ceiling(self) -> None:
        readme = read(README)

        self.assertIn("## Launcher Supervisor Node N2 atomic cutover", readme)
        self.assertIn("single side-effect coordinator", readme)
        self.assertIn("kill_authority: false", readme)
        self.assertIn("status_script_execution: false", readme)
        self.assertIn("remain unreachable reference until N3 removal", readme)
        self.assertIn("runtime proof gates", readme)
        self.assertIn("launcher-worker.v2", readme)
        self.assertNotIn("launcher-worker.v1", readme)


if __name__ == "__main__":
    import unittest

    unittest.main()
