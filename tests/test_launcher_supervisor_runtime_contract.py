from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "home-control-launcher"
SERVER = LAUNCHER / "server.js"
RUNTIME = LAUNCHER / "launcher-supervisor-runtime.js"
PRIVATE_PLAN = LAUNCHER / "launcher-private-service-plan.js"
README = LAUNCHER / "README.md"
SYSTEM = ROOT / "ops" / "scripts" / "system.ps1"
PROFILE_ID = "thought-core-v0"
VALID_CONFIG_SHA256 = "0123456789abcdef" * 4
PRIVATE_OPTION_SENTINEL = r"C:\qa-private\camera-option-value-sentinel"
RAW_SAVE_RESPONSE_SENTINEL = "raw-save-response-must-not-be-printed"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


class LauncherSupervisorRuntimeContractTest(TestCase):
    def run_system_start(
        self, save_response: object
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
        powershell = shutil.which("pwsh")
        self.assertIsNotNone(powershell, "pwsh_not_found")
        requests: list[dict[str, object]] = []
        request_lock = threading.Lock()

        class LauncherStubHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                body = json.loads(raw_body.decode("utf-8"))
                with request_lock:
                    requests.append(
                        {"path": self.path, "body": body, "raw_body": raw_body}
                    )
                if self.path == "/api/save-config":
                    response_body = save_response
                    status = 200
                elif self.path == "/api/start":
                    response_body = {"ok": True, "result": "test_stub_started"}
                    status = 200
                else:
                    response_body = {"ok": False, "error": "not_found"}
                    status = 404
                encoded = json.dumps(response_body, separators=(",", ":")).encode(
                    "utf-8"
                )
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), LauncherStubHandler)
        server.daemon_threads = True
        server.timeout = 1
        listener_host, listener_port = server.server_address
        self.assertEqual(listener_host, "127.0.0.1")
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        process: subprocess.Popen[str] | None = None
        stdout = ""
        stderr = ""
        returncode = -1
        command: list[str] = []
        try:
            listener.start()
            command = [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SYSTEM),
                "start",
                "-Profile",
                PROFILE_ID,
                "-LauncherUrl",
                f"http://127.0.0.1:{listener_port}",
                "-SkipTouchDesignerGui",
                "-MediapipeCameraName",
                PRIVATE_OPTION_SENTINEL,
            ]
            environment = os.environ.copy()
            environment.pop("SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE", None)
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                self.fail("system_ps1_timeout")
            returncode = process.returncode
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            listener.join(timeout=5)

        self.assertFalse(listener.is_alive(), "loopback_listener_thread_residue")
        if process is not None:
            self.assertIsNotNone(process.poll(), "system_ps1_process_residue")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
            rebound.bind(("127.0.0.1", listener_port))

        completed = subprocess.CompletedProcess(command, returncode, stdout, stderr)
        with request_lock:
            recorded_requests = list(requests)
        return completed, recorded_requests

    @staticmethod
    def valid_save_response() -> dict[str, object]:
        return {
            "ok": True,
            "profileId": PROFILE_ID,
            "configIdentity": {
                "effective_config_sha256": VALID_CONFIG_SHA256,
            },
            "qaPrivateResponseSentinel": RAW_SAVE_RESPONSE_SENTINEL,
        }

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
            stop_owned.index("if (held) this.apply(held.event_type, held.service_id"),
        )

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
        self.assertIn('"$baseUrl/api/save-config"', system)
        self.assertIn("expectedConfigSha256 = $expectedConfigSha256", system)
        self.assertIn("saved_config_identity_invalid", system)
        self.assertIn("SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE", system)
        self.assertIn("launcher_api_unavailable", system)
        self.assertIn('$baseUri.Scheme -cne "http"', system)
        for retired_name in (
            "start-home-control-stack.ps1",
            "status-home-control-stack.ps1",
            "stop-home-control-stack.ps1",
        ):
            self.assertNotIn(retired_name, system)

    def test_system_start_saves_then_starts_with_byte_identical_identity(self) -> None:
        completed, requests = self.run_system_start(self.valid_save_response())
        combined = completed.stdout + completed.stderr

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            [request["path"] for request in requests],
            ["/api/save-config", "/api/start"],
        )
        self.assertEqual(len(requests), 2)
        save_body = requests[0]["body"]
        start_body = requests[1]["body"]
        self.assertIsInstance(save_body, dict)
        self.assertIsInstance(start_body, dict)
        self.assertEqual(set(save_body), {"profileId", "options"})
        self.assertEqual(save_body["profileId"], PROFILE_ID)
        self.assertTrue(save_body["options"]["SkipTouchDesignerGui"])
        self.assertEqual(
            save_body["options"]["MediapipeCameraName"], PRIVATE_OPTION_SENTINEL
        )
        self.assertEqual(
            set(start_body), {"profileId", "expectedConfigSha256"}
        )
        self.assertEqual(start_body["profileId"], PROFILE_ID)
        self.assertEqual(start_body["expectedConfigSha256"], VALID_CONFIG_SHA256)
        self.assertEqual(
            start_body["expectedConfigSha256"].encode("utf-8"),
            VALID_CONFIG_SHA256.encode("utf-8"),
        )
        self.assertIn(VALID_CONFIG_SHA256.encode("utf-8"), requests[1]["raw_body"])
        self.assertNotIn("options", start_body)
        self.assertNotIn(PRIVATE_OPTION_SENTINEL, combined)
        self.assertNotIn(RAW_SAVE_RESPONSE_SENTINEL, combined)
        self.assertNotIn("MediapipeCameraName", combined)
        self.assertNotIn(subprocess.list2cmdline(completed.args), combined)
        self.assertNotIn(
            json.dumps(save_body, separators=(",", ":")), combined
        )

    def test_system_start_rejects_invalid_saved_config_identity_before_start(self) -> None:
        missing = object()

        def response(
            *,
            ok: object = True,
            profile_id: object = PROFILE_ID,
            config_identity: object = missing,
        ) -> dict[str, object]:
            payload: dict[str, object] = {
                "qaPrivateResponseSentinel": RAW_SAVE_RESPONSE_SENTINEL
            }
            if ok is not missing:
                payload["ok"] = ok
            if profile_id is not missing:
                payload["profileId"] = profile_id
            if config_identity is missing:
                config_identity = {
                    "effective_config_sha256": VALID_CONFIG_SHA256
                }
            if config_identity is not None:
                payload["configIdentity"] = config_identity
            return payload

        invalid_rows = (
            ("ok_missing", response(ok=missing)),
            ("ok_false", response(ok=False)),
            ("ok_string", response(ok="true")),
            ("identity_missing", response(config_identity=None)),
            ("identity_non_object", response(config_identity="not-an-object")),
            ("hash_missing", response(config_identity={})),
            (
                "hash_non_string",
                response(config_identity={"effective_config_sha256": 64}),
            ),
            (
                "hash_uppercase",
                response(
                    config_identity={
                        "effective_config_sha256": VALID_CONFIG_SHA256.upper()
                    }
                ),
            ),
            (
                "hash_leading_whitespace",
                response(
                    config_identity={
                        "effective_config_sha256": f" {VALID_CONFIG_SHA256}"
                    }
                ),
            ),
            (
                "hash_trailing_whitespace",
                response(
                    config_identity={
                        "effective_config_sha256": f"{VALID_CONFIG_SHA256} "
                    }
                ),
            ),
            (
                "hash_wrong_length",
                response(
                    config_identity={
                        "effective_config_sha256": VALID_CONFIG_SHA256[:-1]
                    }
                ),
            ),
            (
                "hash_lowercase_non_hex",
                response(config_identity={"effective_config_sha256": "g" * 64}),
            ),
            ("profile_missing", response(profile_id=missing)),
            ("profile_non_string", response(profile_id=7)),
            ("profile_mismatch", response(profile_id="aituber-only")),
            ("profile_case_mismatch", response(profile_id="Thought-core-v0")),
        )

        for case_name, save_response in invalid_rows:
            with self.subTest(case=case_name):
                completed, requests = self.run_system_start(save_response)
                combined = completed.stdout + completed.stderr
                paths = [request["path"] for request in requests]

                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(paths.count("/api/save-config"), 1)
                self.assertEqual(paths.count("/api/start"), 0)
                self.assertEqual(paths, ["/api/save-config"])
                self.assertIn("saved_config_identity_invalid", combined)
                self.assertNotIn("launcher_api_unavailable", combined)
                self.assertNotIn(PRIVATE_OPTION_SENTINEL, combined)
                self.assertNotIn(RAW_SAVE_RESPONSE_SENTINEL, combined)
                self.assertNotIn("MediapipeCameraName", combined)
                self.assertNotIn(subprocess.list2cmdline(completed.args), combined)
                self.assertNotIn(
                    json.dumps(requests[0]["body"], separators=(",", ":")),
                    combined,
                )

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
