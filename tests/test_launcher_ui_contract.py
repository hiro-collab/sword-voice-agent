import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT_CANDIDATES = (
    ROOT.parents[1],
    ROOT.parent.parent / "sword-agent-os",
)
PRODUCT_ROOT = next(
    (
        candidate
        for candidate in PRODUCT_ROOT_CANDIDATES
        if (candidate / "manifests" / "demo-safe-settings" / "defaults.json").is_file()
    ),
    PRODUCT_ROOT_CANDIDATES[0],
)
PUBLIC = ROOT / "tools" / "home-control-launcher" / "public"
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
LAUNCHER_PROFILES = ROOT / "tools" / "home-control-launcher" / "config" / "default-profiles.json"
TIMING_COLLECTOR = ROOT / "tools" / "home-control-launcher" / "scripts" / "collect-demo-timing.mjs"
DEMO_SAFE_DEFAULTS = PRODUCT_ROOT / "manifests" / "demo-safe-settings" / "defaults.json"
STACK_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
STACK_STATUS_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "status-home-control-stack.ps1"
LAUNCHER_START_SCRIPT = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-launcher.ps1"
SYSTEM_SCRIPT = ROOT / "ops" / "scripts" / "system.ps1"
THOUGHT_CORE_START_SCRIPT = ROOT / "scripts" / "start-thought-core.ps1"
THOUGHT_CORE_WATCH_START_SCRIPT = ROOT / "scripts" / "start-thought-core-watch.ps1"


def read_public(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8")


def read_launcher_server() -> str:
    return LAUNCHER_SERVER.read_text(encoding="utf-8")


def read_launcher_profiles() -> list[dict]:
    payload = json.loads(LAUNCHER_PROFILES.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return payload


def read_timing_collector() -> str:
    return TIMING_COLLECTOR.read_text(encoding="utf-8")


def read_demo_safe_defaults() -> dict:
    payload = json.loads(DEMO_SAFE_DEFAULTS.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8")


def read_stack_status_script() -> str:
    return STACK_STATUS_SCRIPT.read_text(encoding="utf-8")


def read_launcher_start_script() -> str:
    return LAUNCHER_START_SCRIPT.read_text(encoding="utf-8")


def read_system_script() -> str:
    return SYSTEM_SCRIPT.read_text(encoding="utf-8")


def read_thought_core_start_script() -> str:
    return THOUGHT_CORE_START_SCRIPT.read_text(encoding="utf-8")


def read_thought_core_watch_start_script() -> str:
    return THOUGHT_CORE_WATCH_START_SCRIPT.read_text(encoding="utf-8")


def extract_between(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


class LauncherUiContractTest(TestCase):
    def test_standard_ops_profile_activates_closed_loop_feedback_for_both_services(self) -> None:
        self.skipTest("N2 keeps legacy PowerShell supervisor assertions as unreachable N3 reference")
        system = read_system_script()
        stack = read_stack_start_script()
        watcher_start = read_thought_core_watch_start_script()
        profiles = {profile["id"]: profile for profile in read_launcher_profiles()}
        start_arguments = extract_between(
            system,
            "function New-StackStartArguments",
            "function New-StackStatusArguments",
        )
        start_dispatch = extract_between(
            system,
            '    "start" {',
            '    "stop" {',
        )
        resolver = extract_between(
            stack,
            "function Get-ClosedLoopFeedbackV1ServiceEnvironment",
            "if ([string]::IsNullOrWhiteSpace($HomeAssistantServerRoot))",
        )
        watcher_mode_enforcer = extract_between(
            watcher_start,
            "function Set-ClosedLoopFeedbackV1ModeEnvironment",
            "$repoRoot = Get-SwordRepoRoot",
        )
        thought_core_service = extract_between(
            stack,
            '-Name "thought_core_api"',
            "if (-not $SkipMediapipe)",
        )
        watcher_service = extract_between(
            stack,
            '-Name "thought_core_watcher"',
            "if (-not $SkipTouchDesignerGui)",
        )
        watcher_arguments = extract_between(
            stack,
            "if ($EnableThoughtCoreWatch) {\n    $thoughtCoreWatchArgs = @(",
            "\n\n    $specs += New-ServiceSpec",
        )

        self.assertIn(
            'Add-NamedArgument -Arguments $arguments -Name "-OpsProfile" -Value $EffectiveProfile',
            start_arguments,
        )
        self.assertIn("-EffectiveProfile $effectiveProfile", start_dispatch)
        self.assertNotIn('-Name "-OpsProfile" -Value $Profile', system)
        self.assertIn(
            '[ValidatePattern("^[a-z0-9][a-z0-9-]{0,63}$")]\n'
            '    [string]$OpsProfile = ""',
            stack,
        )
        self.assertIn("-Environment $thoughtCoreEnvironment", thought_core_service)
        self.assertIn(
            "-Environment $closedLoopFeedbackV1Environment",
            watcher_service,
        )
        self.assertNotIn("-Environment $thoughtCoreEnvironment", watcher_service)
        self.assertIn('"-ClosedLoopFeedbackV1Mode"', watcher_arguments)
        self.assertIn("$closedLoopFeedbackV1Mode", watcher_arguments)
        self.assertIn(
            'THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_URL = "$ThoughtCoreBaseUrl/feedback/closed-loop"',
            stack,
        )
        self.assertIn(
            "THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED = $closedLoopFeedbackV1Enabled",
            stack,
        )
        self.assertIn(
            "NEXT_PUBLIC_THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED = $closedLoopFeedbackV1Enabled",
            stack,
        )
        self.assertIn(
            '[ValidateSet("enabled", "disabled")]\n'
            '    [string]$ClosedLoopFeedbackV1Mode = "disabled"',
            watcher_start,
        )
        import_index = watcher_start.index("Import-SwordEnv -EnvPath $resolvedEnvPath")
        reassert_index = watcher_start.index(
            "Set-ClosedLoopFeedbackV1ModeEnvironment -Mode $ClosedLoopFeedbackV1Mode",
            import_index,
        )
        self.assertLess(import_index, reassert_index)

        ordinary = profiles["thought-core-v0"]["options"]
        self.assertTrue(ordinary["EnableThoughtCore"])
        self.assertTrue(ordinary["EnableThoughtCoreWatch"])
        self.assertEqual(ordinary["ThoughtCoreLlmProvider"], "sword-openai-broker")
        for compatibility_profile in ("demo-fast", "demo-fast-action"):
            self.assertEqual(profiles[compatibility_profile]["group"], "Compatibility")

        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is required for closed-loop activation contract tests")
        powershell_program = r'''
$ErrorActionPreference = "Stop"
$env:THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED = "inherited-sentinel"
''' + resolver + watcher_mode_enforcer + r'''
$cases = @(
    @{ name = "ordinary"; profile = "thought-core-v0" }
    @{ name = "compatibility-visible"; profile = "demo-fast" }
    @{ name = "compatibility-action"; profile = "demo-fast-action" }
    @{ name = "diagnostics"; profile = "camera-debug" }
    @{ name = "direct"; profile = "" }
)
@(
    foreach ($item in $cases) {
        $environment = Get-ClosedLoopFeedbackV1ServiceEnvironment `
            -EffectiveProfile $item.profile
        $mode = if (
            $environment["THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED"] -eq "1"
        ) {
            "enabled"
        }
        else {
            "disabled"
        }
        $env:THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED = "1"
        Set-ClosedLoopFeedbackV1ModeEnvironment -Mode $mode
        [pscustomobject]@{
            name = $item.name
            child_value = $environment["THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED"]
            watcher_value_after_import = $env:THOUGHT_CORE_CLOSED_LOOP_FEEDBACK_V1_ENABLED
        }
    }
) | ConvertTo-Json -Compress
'''
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                powershell_program,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        cases = json.loads(completed.stdout)
        self.assertEqual(cases[0]["child_value"], "1")
        self.assertEqual(cases[0]["watcher_value_after_import"], "1")
        for case in cases[1:]:
            self.assertEqual(case["child_value"], "")
            self.assertEqual(case["watcher_value_after_import"], "")

    def test_launcher_routes_lifecycle_only_through_node_supervisor(self) -> None:
        server = read_launcher_server()
        system = read_system_script()
        start_stack = extract_between(server, "const startStack", "const runScriptAndCollect")
        stop_stack = extract_between(server, "const stopStack", "const reclaimManagedPortsFromLauncher")
        status_route = extract_between(
            server,
            "requestUrl.pathname === '/api/status-script'",
            "requestUrl.pathname === '/api/shutdown'",
        )

        self.assertIn("LauncherSupervisorRuntime", server)
        self.assertIn("LauncherProbeRuntimeContext", server)
        self.assertIn("probeExecutorFactory: (contextOptions) =>", server)
        self.assertIn("schema_version: 'launcher_worker.v2'", server)
        self.assertNotIn("launcher_worker.v1", server)
        self.assertIn("await launcherRuntime.start", start_stack)
        self.assertIn("launcherRuntime.stop", stop_stack)
        self.assertNotIn("childProcess.spawn", start_stack)
        self.assertNotIn("childProcess.spawnSync", start_stack)
        self.assertIn("status_script_execution: false", status_route)
        self.assertNotIn("runScriptAndCollect", status_route)

        self.assertIn("Invoke-RestMethod", system)
        self.assertIn('"http://127.0.0.1:$LauncherPort"', system)
        self.assertIn("launcher_compatibility_recursion_rejected", system)
        for retired_name in (
            "start-home-control-stack.ps1",
            "status-home-control-stack.ps1",
            "stop-home-control-stack.ps1",
        ):
            self.assertNotIn(retired_name, system)

    def test_body_map_inspector_is_the_only_launcher_diagnostics_route(self) -> None:
        server = read_launcher_server()
        public_app = read_public("app.js")
        stack_start = read_stack_start_script()

        self.assertIn("name: 'Body map inspector'", server)
        self.assertIn("/body-map-inspector?fov=60&scale=1", server)
        self.assertIn("'Body map inspector': 'Diagnostics body map'", public_app)
        self.assertIn("'Body map inspector': '自己状態マップ'", public_app)
        self.assertIn('-Name "Body map inspector"', stack_start)
        self.assertIn("/body-map-inspector?fov=60&scale=1", stack_start)
        for source in (server, public_app, stack_start):
            self.assertNotIn("cube-vault-background", source)
            self.assertNotIn("Cube Vault", source)
            self.assertNotIn("cube vault", source)

    def test_launcher_runtime_copies_camera_state_and_fails_closed(self) -> None:
        self.skipTest("N2 status is reducer-owned and no longer copies legacy PID/manifest state")
        with tempfile.TemporaryDirectory() as temporary_root:
            state_dir = Path(temporary_root) / "state"
            manifest_path = (
                state_dir
                / "modules"
                / "mediapipe_camera_hub_stack"
                / "processes.json"
            )
            manifest_path.parent.mkdir(parents=True)
            started_at = datetime.now(timezone.utc).isoformat()

            camera_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            camera_probe.bind(("127.0.0.1", 0))
            mediapipe_port = camera_probe.getsockname()[1]
            camera_probe.close()
            camera_helper_code = (
                "import socket,sys,time;"
                "p=int(sys.argv[sys.argv.index('--port')+1]);"
                "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
                "s.bind(('127.0.0.1',p));s.listen();s.settimeout(.1);"
                "\nwhile True:\n"
                " try:\n  c,_=s.accept();c.close()\n"
                " except TimeoutError:\n  pass\n"
            )
            camera_helper = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    camera_helper_code,
                    "apps/serve_camera_hub.py",
                    "--port",
                    str(mediapipe_port),
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            helper_deadline = time.monotonic() + 5
            while time.monotonic() < helper_deadline:
                try:
                    with socket.create_connection(("127.0.0.1", mediapipe_port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                camera_helper.terminate()
                camera_helper.wait(timeout=5)
                self.fail("camera ownership helper did not start")

            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            launcher_port = probe.getsockname()[1]
            probe.close()

            (state_dir / "launcher-config.json").write_text(
                json.dumps(
                    {
                        "selectedProfileId": "camera-debug",
                        "options": {"MediapipePort": mediapipe_port},
                    }
                ),
                encoding="utf-8",
            )
            private_camera_name = "private://camera-$(expand)-raw-marker"
            (state_dir / "pids.json").write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "started_at": started_at,
                        "workspace_root": str(ROOT.parents[1]),
                        "processes": [
                            {
                                "name": "mediapipe_camera_hub_stack",
                                "module": "mediapipe-sword-sign",
                                "role": "camera_hub_stack",
                                "pid": camera_helper.pid,
                                "working_directory": str(ROOT),
                                "command": (
                                    "uv run python apps/serve_camera_hub.py "
                                    f"--camera-name {private_camera_name} "
                                    f"--port {mediapipe_port}"
                                ),
                                "started_at": started_at,
                                "allowed_process_names": ["uv", "python", "mediamtx", "ffmpeg"],
                                "child_process_file": str(manifest_path),
                                "stop_strategy": "managed_tree",
                            },
                            {
                                "name": "vision_snapshot_processor",
                                "module": "vision-snapshot-processor",
                                "role": "vision_snapshot_processor",
                                "pid": os.getpid(),
                                "working_directory": str(ROOT),
                                "command": "test-only-owner",
                                "started_at": started_at,
                                "allowed_process_names": ["python"],
                                "child_process_file": "",
                                "stop_strategy": "managed_tree",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def write_manifest(
                state_class: str,
                ready: bool,
                *,
                owner_pid: int | None = None,
                processes: list[dict] | None = None,
            ) -> None:
                now = datetime.now(timezone.utc).isoformat()
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "module": "mediapipe-sword-sign",
                            "service": "mediapipe_camera_hub_stack",
                            "owner_pid": camera_helper.pid if owner_pid is None else owner_pid,
                            "camera_state_class": state_class,
                            "ready": ready,
                            "ready_at": now if ready else None,
                            "ready_detail": "private://camera-source-must-not-leak",
                            "updated_at": now,
                            "processes": processes
                            if processes is not None
                            else [
                                {"name": "mediamtx", "running": True},
                                {"name": "camera-hub", "running": True},
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            write_manifest("unavailable", False)
            launcher = subprocess.Popen(
                [
                    "node",
                    str(LAUNCHER_SERVER),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(launcher_port),
                    "--workspace",
                    str(ROOT.parents[1]),
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                status_url = f"http://127.0.0.1:{launcher_port}/api/status"
                state_url = f"http://127.0.0.1:{launcher_port}/api/state"

                def read_status() -> dict:
                    deadline = time.monotonic() + 8
                    last_error: Exception | None = None
                    while time.monotonic() < deadline:
                        try:
                            with urllib.request.urlopen(status_url, timeout=2) as response:
                                return json.loads(response.read().decode("utf-8"))
                        except Exception as error:
                            last_error = error
                            time.sleep(0.05)
                    raise AssertionError(f"launcher status unavailable: {last_error}")

                def read_cli_status() -> str:
                    powershell = shutil.which("pwsh") or shutil.which("powershell")
                    self.assertIsNotNone(powershell)
                    completed = subprocess.run(
                        [
                            powershell,
                            "-NoProfile",
                            "-File",
                            str(STACK_STATUS_SCRIPT),
                            "-WorkspaceRoot",
                            str(ROOT.parents[1]),
                            "-StackStateDir",
                            str(state_dir),
                            "-HomeAssistantBridgePort",
                            str(mediapipe_port),
                            "-EnvironmentStatePort",
                            str(mediapipe_port),
                            "-MediapipePort",
                            str(mediapipe_port),
                            "-VisionSnapshotProcessorPort",
                            str(mediapipe_port),
                            "-AituberPort",
                            str(mediapipe_port),
                            "-TouchDesignerGuiPort",
                            str(mediapipe_port),
                            "-VoicevoxUrl",
                            f"http://127.0.0.1:{mediapipe_port}",
                        ],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=20,
                        check=True,
                    )
                    return completed.stdout

                unavailable = read_status()
                self.assertEqual(unavailable["services"]["mediapipe"]["state"], "DEGRADED")
                self.assertEqual(
                    unavailable["services"]["mediapipe"]["camera_state_class"],
                    "unavailable",
                    unavailable,
                )
                self.assertTrue(
                    unavailable["services"]["mediapipe"]["camera_state_operational"]
                )
                self.assertEqual(
                    unavailable["startupTiming"]["status_class"],
                    "startup_expected_services_operational_with_degraded",
                )
                unavailable_serialized = json.dumps(unavailable)
                self.assertNotIn(private_camera_name, unavailable_serialized)
                self.assertIn(
                    "<local-camera-selection>",
                    unavailable["services"]["mediapipe"]["command"],
                )
                with urllib.request.urlopen(state_url, timeout=2) as response:
                    launcher_state = json.loads(response.read().decode("utf-8"))
                launcher_state_serialized = json.dumps(launcher_state)
                self.assertNotIn(private_camera_name, launcher_state_serialized)
                self.assertIn(
                    "<local-camera-selection>",
                    launcher_state["status"]["services"]["mediapipe"]["command"],
                )
                self.assertNotIn("private://", unavailable_serialized)

                write_manifest("recovering", False)
                recovering = read_status()
                self.assertEqual(recovering["services"]["mediapipe"]["state"], "DEGRADED")
                self.assertEqual(
                    recovering["services"]["mediapipe"]["camera_state_class"],
                    "recovering",
                )
                self.assertIn("mediapipe", recovering["startupTiming"]["degradedServiceIds"])

                write_manifest("ready", True)
                ready = read_status()
                self.assertEqual(ready["services"]["mediapipe"]["state"], "OK")
                self.assertEqual(
                    ready["services"]["mediapipe"]["camera_state_class"],
                    "ready",
                )
                self.assertRegex(read_cli_status(), r"(?m)^\s*mediapipe\s+OK\s+")

                write_manifest(
                    "ready",
                    True,
                    processes=[
                        {"name": "mediamtx", "running": False},
                        {"name": "camera-hub", "running": True},
                    ],
                )
                nonoperational_ready = read_status()
                self.assertEqual(
                    nonoperational_ready["services"]["mediapipe"]["state"],
                    "DEGRADED",
                )
                self.assertFalse(
                    nonoperational_ready["services"]["mediapipe"][
                        "camera_state_operational"
                    ]
                )
                self.assertRegex(
                    read_cli_status(), r"(?m)^\s*mediapipe\s+DEGRADED\s+"
                )

                write_manifest("ready", False)
                malformed = read_status()
                self.assertEqual(malformed["services"]["mediapipe"]["state"], "DEGRADED")
                self.assertEqual(
                    malformed["services"]["mediapipe"]["camera_state_class"],
                    "unknown",
                )
                self.assertEqual(
                    malformed["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_manifest_ready_mismatch",
                )
                self.assertIn("mediapipe", malformed["startupTiming"]["waitingServiceIds"])
                self.assertNotIn("private://", json.dumps(malformed))

                write_manifest("unavailable", False, owner_pid=os.getpid())
                unrelated_owner = read_status()
                self.assertEqual(
                    unrelated_owner["services"]["mediapipe"]["camera_state_class"],
                    "unknown",
                )
                self.assertIn(
                    "camera_runtime_owner_lineage_invalid",
                    unrelated_owner["services"]["mediapipe"]["camera_state_validation_class"],
                )
                self.assertNotIn("private://", json.dumps(unrelated_owner))

                write_manifest("unavailable", False, owner_pid=2147483000)
                dead_owner = read_status()
                self.assertEqual(
                    dead_owner["services"]["mediapipe"]["camera_state_class"],
                    "unknown",
                )
                self.assertEqual(
                    dead_owner["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_runtime_inspection_failed",
                )

                nonlistener_helper = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import time;time.sleep(30)",
                        "apps/serve_camera_hub.py",
                        "--port",
                        str(mediapipe_port),
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    pid_state_path = state_dir / "pids.json"
                    missing_listener_state = json.loads(
                        pid_state_path.read_text(encoding="utf-8")
                    )
                    missing_listener_state["processes"][0]["pid"] = nonlistener_helper.pid
                    missing_listener_state["processes"][0]["started_at"] = (
                        datetime.now(timezone.utc).isoformat()
                    )
                    pid_state_path.write_text(
                        json.dumps(missing_listener_state), encoding="utf-8"
                    )
                    write_manifest(
                        "unavailable", False, owner_pid=nonlistener_helper.pid
                    )
                    missing_owned_listener = read_status()
                    self.assertEqual(
                        missing_owned_listener["services"]["mediapipe"][
                            "camera_state_validation_class"
                        ],
                        "camera_runtime_listener_lineage_invalid",
                    )
                finally:
                    nonlistener_helper.terminate()
                    nonlistener_helper.wait(timeout=5)
                restored_pid_state = json.loads(
                    pid_state_path.read_text(encoding="utf-8")
                )
                restored_pid_state["processes"][0]["pid"] = camera_helper.pid
                restored_pid_state["processes"][0]["started_at"] = started_at
                pid_state_path.write_text(
                    json.dumps(restored_pid_state), encoding="utf-8"
                )

                write_manifest(
                    "unavailable",
                    False,
                    processes=[
                        {"name": "mediamtx", "running": "true"},
                        {"name": "camera-hub", "running": True},
                    ],
                )
                string_running = read_status()
                self.assertEqual(
                    string_running["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_manifest_processes_invalid",
                )

                write_manifest(
                    "unavailable",
                    False,
                    processes=[
                        {"name": "mediamtx", "running": True},
                        {"name": "camera-hub", "running": True},
                        {"name": "camera-hub", "running": True},
                    ],
                )
                duplicate_process = read_status()
                self.assertEqual(
                    duplicate_process["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_manifest_processes_invalid",
                )

                pid_state = json.loads(pid_state_path.read_text(encoding="utf-8"))
                pid_state["processes"][0]["pid"] = camera_helper.pid
                pid_state["processes"][0]["started_at"] = started_at
                pid_state["processes"][0]["module"] = "wrong-camera-authority"
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                wrong_registry = read_status()
                self.assertEqual(
                    wrong_registry["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"][0]["module"] = "mediapipe-sword-sign"
                pid_state["processes"][0]["role"] = "wrong-role"
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                wrong_role = read_status()
                self.assertEqual(
                    wrong_role["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"][0]["role"] = "camera_hub_stack"
                pid_state["processes"][0]["child_process_file"] = str(
                    state_dir / "wrong-processes.json"
                )
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                wrong_path = read_status()
                self.assertEqual(
                    wrong_path["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_registry_manifest_path_invalid",
                )

                pid_state["processes"][0]["child_process_file"] = str(manifest_path)
                pid_state["processes"].append(dict(pid_state["processes"][0]))
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                duplicate_registry = read_status()
                self.assertEqual(
                    duplicate_registry["services"]["mediapipe"][
                        "camera_state_validation_class"
                    ],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"] = pid_state["processes"][:2]
                write_manifest("unavailable", False)
                pid_state["processes"][0]["pid"] = ""
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                blank_pid = read_status()
                self.assertEqual(
                    blank_pid["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"][0]["pid"] = "not-a-pid"
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                malformed_pid = read_status()
                self.assertEqual(
                    malformed_pid["services"]["mediapipe"][
                        "camera_state_validation_class"
                    ],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"][0]["pid"] = camera_helper.pid
                pid_state["processes"][0]["started_at"] = ""
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                blank_start = read_status()
                self.assertEqual(
                    blank_start["services"]["mediapipe"]["camera_state_validation_class"],
                    "camera_registry_identity_invalid",
                )

                pid_state["processes"][0]["pid"] = launcher.pid
                pid_state["processes"][0]["started_at"] = started_at
                pid_state_path.write_text(json.dumps(pid_state), encoding="utf-8")
                disallowed_root = read_status()
                self.assertEqual(
                    disallowed_root["services"]["mediapipe"][
                        "camera_state_validation_class"
                    ],
                    "camera_runtime_root_identity_invalid",
                )
            finally:
                launcher.terminate()
                launcher.wait(timeout=5)
                camera_helper.terminate()
                camera_helper.wait(timeout=5)

    def test_launcher_reuse_requires_same_workspace_launcher_owner(self) -> None:
        script = read_launcher_start_script()

        self.assertIn("Get-LauncherListeners", script)
        self.assertIn("IsLauncher", script)
        self.assertIn("WorkspaceMatches", script)
        self.assertIn("Refusing to reuse it", script)

    def test_launch_configuration_uses_progressive_disclosure(self) -> None:
        html = read_public("index.html")

        self.assertIn("Launch configuration", html)
        self.assertNotIn("<h2>System profile</h2>", html)
        self.assertIn("Launch summary", html)
        self.assertIn('id="services-summary"', html)
        self.assertIn('id="diagnostics-summary"', html)
        self.assertIn('id="runtime-summary"', html)
        self.assertIn('id="ports-summary"', html)
        self.assertIn("<summary>", html)
        self.assertIn("Core ports", html)
        self.assertIn("Core services", html)
        self.assertIn("Provider", html)
        self.assertIn("Advanced overrides", html)
        self.assertIn("Start Stack starts enabled/expected services only.", html)
        self.assertIn('id="launch-scope-enabled"', html)
        self.assertIn('id="launch-scope-skipped"', html)

    def test_launcher_selects_conversation_provider_without_rewriting_env(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()
        system = read_system_script()
        stack = read_stack_start_script()

        self.assertIn('id="ThoughtCoreLlmProvider"', html)
        for provider in ("configured", "openai-compatible", "codex-cli", "codex-cli-luna"):
            self.assertIn(f'value="{provider}"', html)
            self.assertIn(provider, server)
        self.assertIn("ThoughtCoreLlmProvider", app)
        bind_controls = extract_between(app, "const bindControls = () => {", "const showError =")
        self.assertIn("$('ThoughtCoreLlmProvider').addEventListener('change'", bind_controls)
        self.assertIn("ThoughtCoreLlmProvider", system)
        self.assertIn("ThoughtCoreLlmProvider", stack)
        self.assertIn('"gpt-5.6-terra"', stack)
        self.assertIn('"gpt-5.6-luna"', stack)
        self.assertIn('"medium"', stack)
        self.assertIn('"low"', stack)
        self.assertIn(
            '$ThoughtCoreLlmProvider -in @("codex-cli", "codex-cli-luna")',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_LLM_PROVIDER"] = $thoughtCoreRuntimeProvider',
            stack,
        )
        self.assertIn(
            'if ($ThoughtCoreLlmProvider -eq "codex-cli-luna")',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_LLM_VISIBLE_SPEECH_ENABLED"] = "1"',
            stack,
        )
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_REQUIRE_LLM_VISIBLE_SPEECH"] = "0"',
            stack,
        )
        self.assertIn('"respond"', stack)
        self.assertIn('"read-only"', stack)
        self.assertIn('"never"', stack)
        self.assertIn('"true"', stack)

    def test_launcher_routes_streamcam_capture_request_without_claiming_achieved_fps(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()
        system = read_system_script()
        stack = read_stack_start_script()

        for field in (
            "MediapipeCameraWidth",
            "MediapipeCameraHeight",
            "MediapipeCameraFps",
            "MediapipeCameraInputCodec",
        ):
            self.assertIn(f'id="{field}"', html)
            self.assertIn(field, app)
            self.assertIn(field, server)
            self.assertIn(field, system)
            self.assertIn(field, stack)

        self.assertIn('id="MediapipeCameraSelectionKey"', html)
        self.assertIn('id="MediapipeCameraNameManual"', html)
        self.assertIn("MediapipeCameraName", app)
        self.assertIn("MediapipeCameraName", server)
        self.assertIn("MediapipeCameraName", system)
        self.assertIn("MediapipeCameraName", stack)
        self.assertIn("MediapipeCameraName: ''", server)
        self.assertIn("MediapipeCameraWidth: 1920", server)
        self.assertIn("MediapipeCameraHeight: 1080", server)
        self.assertIn("MediapipeCameraFps: 30", server)
        self.assertIn("MediapipeCameraInputCodec: 'mjpeg'", server)
        self.assertIn("MediapipeCameraWidth: { min: 160, max: 3840 }", server)
        self.assertIn("MediapipeCameraHeight: { min: 120, max: 2160 }", server)
        self.assertIn("MediapipeCameraFps: { min: 1, max: 120 }", server)
        self.assertIn("numberValue >= limits.min && numberValue <= limits.max", server)
        self.assertIn("[ValidateRange(160, 3840)]", system)
        self.assertIn("[ValidateRange(120, 2160)]", system)
        self.assertIn("[ValidateRange(1, 120)]", system)
        self.assertIn("[ValidateRange(160, 3840)]", stack)
        self.assertIn("[ValidateRange(120, 2160)]", stack)
        self.assertIn("[ValidateRange(1, 120)]", stack)
        self.assertIn("runtime diagnostics remain the authority for achieved FPS", app)
        self.assertIn('"--ffmpeg-input-codec"', stack)
        self.assertIn('$MediapipeCameraInputCodec', stack)

    def test_launcher_camera_selector_is_enumerated_refreshable_and_fail_closed(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()

        self.assertIn('<select id="MediapipeCameraSelectionKey"></select>', html)
        self.assertIn('id="refresh-camera-devices"', html)
        self.assertIn('id="MediapipeCameraNameManual"', html)
        self.assertIn('maxlength="256"', html)
        self.assertIn('id="apply-manual-camera"', html)
        self.assertNotIn('id="MediapipeCameraName" type="text"', html)
        self.assertIn("const refreshVideoInputDevices = async () =>", app)
        self.assertIn("api('/api/video-input-devices')", app)
        self.assertIn("selectedKey && !selectedMatch", app)
        self.assertIn("launch.cameraSelectionMissing", app)
        self.assertIn("state.options.MediapipeCameraSelectionKey = selectionKey", app)
        self.assertIn("state.options.MediapipeCameraName = selectedDevice?.label", app)
        self.assertIn("const matchingDevices = videoInputDevices().filter", app)
        self.assertIn("matchingDevices.length === 1", app)
        self.assertIn("matchingDevices.length > 1", app)
        self.assertIn("'selected_ambiguous'", app)
        self.assertIn("'selected_unresolvable'", app)
        self.assertIn("state.options.MediapipeCameraSelectionKey = ''", app)
        self.assertIn("state.videoInputSelectionClass = 'manual_selection'", app)
        self.assertIn("setOption('MediapipeCameraName', value)", app)
        self.assertIn("const normalizeCameraSelection = (value) =>", app)
        self.assertNotIn("'MediapipeCameraName',\n  'MediapipeCameraInputCodec'", app)
        self.assertIn("-list_devices", server)
        self.assertIn("\\(video\\)", server)
        self.assertIn("device_start_count: 0", server)
        self.assertIn("capture_count: 0", server)
        self.assertIn("video_input_enumeration_unavailable", server)
        self.assertIn("normalized.MediapipeCameraName = sanitizeVideoInputDeviceName", server)
        self.assertIn("normalized.MediapipeCameraSelectionKey = sanitizeVideoInputSelectionKey", server)
        self.assertIn("resolveVideoInputSelectionForStart", server)
        self.assertIn("selected_camera_unresolvable", server)
        self.assertIn("selected_camera_ambiguous", server)
        self.assertIn("@device_(?:pnp|cm)_", server)
        self.assertIn("redactCameraSelectionInCommandText", server)

    def test_launcher_camera_enumeration_endpoint_has_no_device_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            state_dir = Path(temporary_root) / "state"
            video_input_fixture = Path(temporary_root) / "video-inputs.json"
            video_input_fixture.write_text(
                json.dumps(["camera-a", "camera-b", "camera-a"]),
                encoding="utf-8",
            )
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            launcher_port = probe.getsockname()[1]
            probe.close()
            env = os.environ.copy()
            env["NODE_ENV"] = "test"
            env["HOME_CONTROL_LAUNCHER_TEST_FAKE_SUPERVISOR"] = "deterministic_v1"
            env["HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS_FILE"] = str(
                video_input_fixture
            )
            launcher = subprocess.Popen(
                [
                    "node",
                    str(LAUNCHER_SERVER),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(launcher_port),
                    "--workspace",
                    str(temporary_root),
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                url = f"http://127.0.0.1:{launcher_port}/api/video-input-devices"
                payload = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        break
                    except Exception:
                        time.sleep(0.05)
                self.assertIsNotNone(payload)
                self.assertEqual(payload["result_class"], "video_inputs_enumerated")
                self.assertEqual(payload["count"], 2)
                self.assertEqual(len(payload["devices"]), 2)
                self.assertEqual(payload["device_start_count"], 0)
                self.assertEqual(payload["capture_count"], 0)
                self.assertEqual(payload["selection_class"], "no_selection")
                self.assertFalse((state_dir / "pids.json").exists())
                self.assertFalse((state_dir / "launcher-state.json").exists())

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/state", timeout=5
                ) as response:
                    initial_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    initial_state["config"]["options"]["MediapipeCameraName"], ""
                )
                self.assertEqual(
                    initial_state["config"]["options"]["MediapipeCameraSelectionKey"],
                    "",
                )

                camera_a = next(
                    device for device in payload["devices"] if device["label"] == "camera-a"
                )

                save_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {
                            "MediapipeCameraName": "camera-a",
                            "MediapipeCameraSelectionKey": camera_a["value"],
                        },
                    }
                ).encode("utf-8")
                save_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/save-config",
                    data=save_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(save_request, timeout=5) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(saved["options"]["MediapipeCameraName"], "camera-a")
                self.assertEqual(
                    saved["options"]["MediapipeCameraSelectionKey"],
                    camera_a["value"],
                )

                retired_mode_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeMode": "headless"},
                    }
                ).encode("utf-8")
                retired_mode_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/save-config",
                    data=retired_mode_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as retired_error:
                    urllib.request.urlopen(retired_mode_request, timeout=5)
                self.assertEqual(retired_error.exception.code, 500)
                retired_payload = json.loads(
                    retired_error.exception.read().decode("utf-8")
                )
                self.assertEqual(retired_payload["error"], "invalid_mediapipe_mode")
                self.assertFalse((state_dir / "pids.json").exists())

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/state", timeout=5
                ) as response:
                    reloaded_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    reloaded_state["config"]["options"]["MediapipeCameraName"],
                    "camera-a",
                )
                self.assertEqual(
                    reloaded_state["config"]["options"]["MediapipeCameraSelectionKey"],
                    camera_a["value"],
                )

                video_input_fixture.write_text(
                    json.dumps(["camera-b"]), encoding="utf-8"
                )
                with urllib.request.urlopen(url, timeout=5) as response:
                    missing_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    missing_payload["selection_class"], "selected_unresolvable"
                )
                self.assertFalse(missing_payload["selected_match"])
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/state", timeout=5
                ) as response:
                    missing_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    missing_state["config"]["options"]["MediapipeCameraName"],
                    "camera-a",
                )
                self.assertEqual(
                    missing_state["config"]["options"]["MediapipeCameraSelectionKey"],
                    camera_a["value"],
                )

                video_input_fixture.write_text(
                    json.dumps(["camera-b", "camera-a"]), encoding="utf-8"
                )
                with urllib.request.urlopen(url, timeout=5) as response:
                    returned_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    returned_payload["selection_class"], "selected_available"
                )
                self.assertTrue(returned_payload["selected_match"])

                saved_boundary_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "useSavedOptions": True,
                        "resolveSelection": True,
                        "expectedResolvedCameraName": "camera-a",
                    }
                ).encode("utf-8")
                saved_boundary_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/test/camera-command-boundary",
                    data=saved_boundary_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(
                    saved_boundary_request, timeout=5
                ) as response:
                    saved_boundary = json.loads(response.read().decode("utf-8"))
                self.assertTrue(saved_boundary["saved_selection_exact"])
                self.assertTrue(saved_boundary["selection_resolution_ok"])
                self.assertTrue(saved_boundary["selection_resolved_exact"])
                self.assertFalse((state_dir / "pids.json").exists())
                self.assertFalse((state_dir / "launcher-state.json").exists())

                adversarial_name = 'private://camera-$(expand)-`tick-"quote"'
                request_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": adversarial_name},
                    }
                ).encode("utf-8")
                command_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/test/camera-command-boundary",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(command_request, timeout=5) as response:
                    command_boundary = json.loads(response.read().decode("utf-8"))
                self.assertTrue(command_boundary["input_accepted"])
                self.assertTrue(command_boundary["execution_argv_exact"])
                self.assertTrue(command_boundary["review_command_redacted"])
                self.assertTrue(command_boundary["public_preview_redacted"])
                self.assertTrue(command_boundary["launcher_state_command_redacted"])
                self.assertTrue(command_boundary["log_command_redacted"])
                self.assertNotIn(adversarial_name, json.dumps(command_boundary))

                preview_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/preview",
                    data=request_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(preview_request, timeout=5) as response:
                    public_preview = json.loads(response.read().decode("utf-8"))
                public_preview_json = json.dumps(public_preview)
                self.assertNotIn(adversarial_name, public_preview_json)
                self.assertNotIn("MediapipeCameraName", public_preview["options"])
                self.assertNotIn("command", public_preview)
                self.assertNotIn("commandLine", public_preview)
                self.assertEqual(public_preview["command_class"], "node_supervisor")
                self.assertEqual(public_preview["execution_authority"], "node_supervisor")

                for invalid_camera_name in (
                    "camera\ncontrol",
                    r"@device_pnp_\\?\usb#must-not-enter-manual-state",
                ):
                    invalid_body = json.dumps(
                        {
                            "profileId": "thought-core-v0",
                            "options": {
                                "MediapipeCameraName": invalid_camera_name,
                                "MediapipeCameraSelectionKey": "",
                            },
                        }
                    ).encode("utf-8")
                    invalid_request = urllib.request.Request(
                        f"http://127.0.0.1:{launcher_port}/api/start",
                        data=invalid_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as invalid_error:
                        urllib.request.urlopen(invalid_request, timeout=5)
                    self.assertEqual(invalid_error.exception.code, 500)
                    invalid_boundary = json.loads(
                        invalid_error.exception.read().decode("utf-8")
                    )
                    self.assertEqual(invalid_boundary["error"], "invalid_camera_name")
                    self.assertNotIn(invalid_camera_name, json.dumps(invalid_boundary))
                    self.assertFalse((state_dir / "pids.json").exists())
                    self.assertFalse((state_dir / "launcher-state.json").exists())
            finally:
                launcher.terminate()
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)

    def test_launcher_camera_selector_distinguishes_same_name_without_identity_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            state_dir = Path(temporary_root) / "state"
            video_input_fixture = Path(temporary_root) / "video-inputs.txt"
            first_alternative = r"@device_pnp_\\?\usb#camera-one"
            second_alternative = r"@device_pnp_\\?\usb#camera-two"

            def write_fixture(first: str, second: str) -> None:
                video_input_fixture.write_text(
                    "\n".join(
                        (
                            '[dshow @ 0001] "Twin Camera" (video)',
                            f'[dshow @ 0001] Alternative name "{first}"',
                            '[dshow @ 0001] "Twin Camera" (video)',
                            f'[dshow @ 0001] Alternative name "{second}"',
                        )
                    ),
                    encoding="utf-8",
                )

            write_fixture(first_alternative, second_alternative)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            launcher_port = probe.getsockname()[1]
            probe.close()
            env = os.environ.copy()
            env["NODE_ENV"] = "test"
            env["HOME_CONTROL_LAUNCHER_TEST_FAKE_SUPERVISOR"] = "deterministic_v1"
            env["HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS_FILE"] = str(
                video_input_fixture
            )
            launcher = subprocess.Popen(
                [
                    "node",
                    str(LAUNCHER_SERVER),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(launcher_port),
                    "--workspace",
                    str(temporary_root),
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                devices_url = (
                    f"http://127.0.0.1:{launcher_port}/api/video-input-devices"
                )
                payload = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(devices_url, timeout=2) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        break
                    except Exception:
                        time.sleep(0.05)
                self.assertIsNotNone(payload)
                self.assertEqual(payload["count"], 2)
                self.assertEqual(len({row["value"] for row in payload["devices"]}), 2)
                self.assertTrue(
                    all(
                        row["value"].startswith("camera_")
                        for row in payload["devices"]
                    )
                )
                serialized_devices = json.dumps(payload)
                self.assertNotIn(first_alternative, serialized_devices)
                self.assertNotIn(second_alternative, serialized_devices)
                self.assertNotIn("@device_pnp_", serialized_devices)

                selected = payload["devices"][0]
                save_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {
                            "MediapipeCameraName": selected["label"],
                            "MediapipeCameraSelectionKey": selected["value"],
                        },
                    }
                ).encode("utf-8")
                save_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/save-config",
                    data=save_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(save_request, timeout=5) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    saved["options"]["MediapipeCameraSelectionKey"],
                    selected["value"],
                )
                self.assertNotIn("@device_pnp_", json.dumps(saved))

                boundary_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "useSavedOptions": True,
                        "resolveSelection": True,
                        "expectedResolvedCameraName": first_alternative,
                    }
                ).encode("utf-8")
                boundary_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/test/camera-command-boundary",
                    data=boundary_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(boundary_request, timeout=5) as response:
                    boundary = json.loads(response.read().decode("utf-8"))
                self.assertTrue(boundary["selection_resolution_ok"])
                self.assertTrue(boundary["selection_resolved_exact"])
                self.assertTrue(boundary["review_command_redacted"])
                self.assertNotIn("@device_pnp_", json.dumps(boundary))

                write_fixture(first_alternative, first_alternative)
                with urllib.request.urlopen(devices_url, timeout=5) as response:
                    ambiguous = json.loads(response.read().decode("utf-8"))
                self.assertEqual(ambiguous["selection_class"], "selected_ambiguous")
                start_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/start",
                    data=json.dumps(
                        {
                            "profileId": "thought-core-v0",
                            "options": saved["options"],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as start_error:
                    urllib.request.urlopen(start_request, timeout=5)
                self.assertEqual(start_error.exception.code, 409)
                start_payload = json.loads(
                    start_error.exception.read().decode("utf-8")
                )
                self.assertEqual(start_payload["error"], "selected_camera_ambiguous")
                self.assertEqual(start_payload["device_start_count"], 0)
                self.assertEqual(start_payload["capture_count"], 0)
                self.assertFalse((state_dir / "pids.json").exists())
                self.assertFalse((state_dir / "launcher-state.json").exists())

                video_input_fixture.write_text("[]", encoding="utf-8")
                with urllib.request.urlopen(devices_url, timeout=5) as response:
                    missing = json.loads(response.read().decode("utf-8"))
                self.assertEqual(missing["selection_class"], "selected_unresolvable")
                self.assertFalse(missing["selected_match"])

                write_fixture(first_alternative, second_alternative)
                with urllib.request.urlopen(devices_url, timeout=5) as response:
                    returned = json.loads(response.read().decode("utf-8"))
                self.assertEqual(returned["selection_class"], "selected_available")
                self.assertTrue(returned["selected_match"])
            finally:
                launcher.terminate()
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)

    def test_launcher_keeps_camera_identity_local_when_remote_access_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            state_dir = Path(temporary_root) / "state"
            log_dir = state_dir / "logs"
            log_dir.mkdir(parents=True)
            local_camera = 'private://camera-$(local)-`tick-"quote"'
            remote_camera = "remote-camera-must-not-replace"
            (state_dir / "launcher-config.json").write_text(
                json.dumps(
                    {
                        "selectedProfileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": local_camera},
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "launcher-state.json").write_text(
                json.dumps(
                    {
                        "commandLine": (
                            f"pwsh system.ps1 -MediapipeCameraName {local_camera}"
                        )
                    }
                ),
                encoding="utf-8",
            )
            (log_dir / "launcher-stack.log").write_text(
                f"camera --camera-name {local_camera}\n", encoding="utf-8"
            )

            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            launcher_port = probe.getsockname()[1]
            probe.close()
            env = os.environ.copy()
            env["NODE_ENV"] = "test"
            env["HOME_CONTROL_LAUNCHER_TEST_FAKE_SUPERVISOR"] = "deterministic_v1"
            env["HOME_CONTROL_LAUNCHER_TEST_REMOTE_ADDRESS"] = "192.0.2.10"
            env["HOME_CONTROL_LAUNCHER_TEST_VIDEO_INPUTS"] = json.dumps(
                [local_camera]
            )
            launcher = subprocess.Popen(
                [
                    "node",
                    str(LAUNCHER_SERVER),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(launcher_port),
                    "--workspace",
                    str(ROOT.parents[1]),
                    "--state-dir",
                    str(state_dir),
                    "--allow-remote",
                ],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                state_url = f"http://127.0.0.1:{launcher_port}/api/state"
                state_payload = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(state_url, timeout=2) as response:
                            state_payload = json.loads(response.read().decode("utf-8"))
                        break
                    except Exception:
                        time.sleep(0.05)
                self.assertIsNotNone(state_payload)
                state_json = json.dumps(state_payload)
                self.assertNotIn(local_camera, state_json)
                self.assertNotIn(
                    "MediapipeCameraName", state_payload["config"]["options"]
                )
                self.assertNotIn("commandLine", state_payload["launcherState"])
                self.assertNotIn("logTail", state_payload)
                self.assertEqual(
                    set(state_payload["launcherState"]),
                    {"command_class", "fixed_start_summary"},
                )

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/video-input-devices",
                    timeout=5,
                ) as response:
                    devices = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    devices["result_class"],
                    "local_video_input_enumeration_redacted",
                )
                self.assertEqual(devices["devices"], [])
                self.assertEqual(devices["count"], 0)
                self.assertNotIn(local_camera, json.dumps(devices))

                save_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": remote_camera},
                    }
                ).encode("utf-8")
                save_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/save-config",
                    data=save_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(save_request, timeout=5) as response:
                    saved_response = json.loads(response.read().decode("utf-8"))
                self.assertNotIn(
                    "MediapipeCameraName", saved_response["options"]
                )
                persisted = json.loads(
                    (state_dir / "launcher-config.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    persisted["options"]["MediapipeCameraName"], local_camera
                )

                boundary_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": remote_camera},
                        "applyRequestCameraBoundary": True,
                    }
                ).encode("utf-8")
                boundary_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/test/camera-command-boundary",
                    data=boundary_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(boundary_request, timeout=5) as response:
                    boundary = json.loads(response.read().decode("utf-8"))
                self.assertTrue(boundary["request_camera_boundary_preserved"])
                self.assertNotIn(local_camera, json.dumps(boundary))
                self.assertNotIn(remote_camera, json.dumps(boundary))

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/logs", timeout=5
                ) as response:
                    logs = json.loads(response.read().decode("utf-8"))
                self.assertNotIn(local_camera, json.dumps(logs))
                self.assertNotIn("logTail", logs)
                self.assertEqual(set(logs), {"ok", "diagnostic"})
                self.assertFalse((state_dir / "pids.json").exists())
            finally:
                launcher.terminate()
                try:
                    launcher.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launcher.wait(timeout=5)

    def test_camera_command_display_and_registry_copies_are_redacted(self) -> None:
        stack = read_stack_start_script()
        self.assertIn("function Protect-CameraSelectionCommand", stack)
        self.assertIn(
            "Protect-CameraSelectionCommand -Command (@($Spec.FilePath) + $Spec.Arguments)",
            stack,
        )
        self.assertIn("CommandLine = $commandLine", stack)
        self.assertIn('CameraSelection = [string]$MediapipeCameraName', stack)

        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("PowerShell 7 is required for the command-redaction dry run")
        with tempfile.TemporaryDirectory() as temporary_root:
            camera_name = 'private://camera-$(expand)-`tick-"quote"'
            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STACK_START_SCRIPT),
                    "-WorkspaceRoot",
                    str(PRODUCT_ROOT),
                    "-StateDir",
                    str(Path(temporary_root) / "state"),
                    "-Profile",
                    "aituber-only",
                    "-MediapipeCameraName",
                    camera_name,
                    "-DryRun",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            combined = completed.stdout + completed.stderr
            self.assertEqual(completed.returncode, 0, combined)
            self.assertNotIn(camera_name, combined)
            self.assertIn("<local-camera-selection>", combined)

            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            conflict_port = probe.getsockname()[1]
            probe.close()
            conflict_owner = subprocess.Popen(
                [
                    "node",
                    "-e",
                    (
                        "require('net').createServer()"
                        f".listen({conflict_port},'127.0.0.1')"
                    ),
                    "--",
                    "--camera-name",
                    camera_name,
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                ready = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
                        if client.connect_ex(("127.0.0.1", conflict_port)) == 0:
                            ready = True
                            break
                    time.sleep(0.05)
                self.assertTrue(ready)
                conflict = subprocess.run(
                    [
                        pwsh,
                        "-NoLogo",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(STACK_START_SCRIPT),
                        "-WorkspaceRoot",
                        str(PRODUCT_ROOT),
                        "-StateDir",
                        str(Path(temporary_root) / "conflict-state"),
                        "-Profile",
                        "aituber-only",
                        "-AituberPort",
                        str(conflict_port),
                        "-MediapipeCameraName",
                        camera_name,
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                conflict_output = conflict.stdout + conflict.stderr
                self.assertNotEqual(conflict.returncode, 0)
                self.assertIn(
                    "Processes are already using required ports:", conflict_output
                )
                self.assertNotIn(camera_name, conflict_output)
                self.assertNotIn("CommandLine", conflict_output)
            finally:
                conflict_owner.terminate()
                try:
                    conflict_owner.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    conflict_owner.kill()
                    conflict_owner.wait(timeout=5)

    def test_launcher_docs_explain_local_camera_selection_without_60_fps_claim(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Launcher の接続カメラ一覧", readme)
        self.assertIn("未選択時に特定機種へ自動代替せず", readme)
        self.assertIn("実際の解像度/FPS", readme)
        self.assertNotIn("現在の既定例は `HD Pro Webcam C920`", readme)

    def test_launcher_exposes_demo_safe_settings_without_claiming_proof(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()

        self.assertIn('id="demo-safe-summary"', html)
        self.assertIn('id="demo-safe-drawer-summary"', html)
        self.assertIn('id="demo-safe-settings-list"', html)
        self.assertIn("Demo settings", html)

        self.assertIn("demoSafeSettings", app)
        self.assertIn("demoReadinessStatus", app)
        self.assertIn("const renderDemoSafeSettings", app)
        self.assertIn("const currentDemoSafeSettings", app)
        self.assertIn("action_ids", app)
        self.assertIn("timing_estimate_sec", app)
        self.assertIn("max_duration_sec", app)
        self.assertIn("does_not_prove", app)

        self.assertIn("DEMO_SAFE_SETTINGS_FILE", server)
        self.assertIn("effectiveDemoSafeSettings", server)
        self.assertIn("demoReadinessStatus", server)
        self.assertIn("feedback_stimulus_class", server)
        self.assertIn("timing_estimate_source_class", server)
        self.assertIn("launcher_state_dir_gitignored_demo_settings_json", server)

    def test_launcher_exposes_no_live_diagnostic_surfaces_and_startup_timing(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()

        self.assertIn('id="startup-timing-list"', html)
        self.assertIn('id="diagnostic-surface-list"', html)
        self.assertIn("Startup timing", html)
        self.assertIn("Diagnostic surfaces", html)

        self.assertIn("startupTiming", app)
        self.assertIn("diagnosticSurfaces", app)
        self.assertIn("renderStartupTiming", app)
        self.assertIn("renderDiagnosticSurfaces", app)
        self.assertIn("formatReadyTimeout", app)
        self.assertIn("startup.maxWait", app)
        self.assertIn("startup.maxWaitUnset", app)
        self.assertIn("startup-timing-table", app)
        self.assertIn("startup-timing-header", app)
        self.assertIn("startup-timeout-input", app)
        self.assertIn("data-ready-timeout-field", app)
        self.assertIn("readyTimeoutOptionFields", app)
        self.assertIn("setReadyTimeoutOption", app)
        self.assertIn("VoicevoxReadyTimeoutSeconds", app)
        self.assertIn("MediapipeReadyTimeoutSeconds", app)
        self.assertNotIn("advanced.voicevoxReadyTimeout", app)
        self.assertNotIn('id="VoicevoxReadyTimeoutSeconds"', html)
        self.assertNotIn('id="MediapipeReadyTimeoutSeconds"', html)
        self.assertIn("numericOptionFields", app)

        self.assertIn("launcher_startup_timing.v0", server)
        self.assertIn("timelineEvents", server)
        self.assertIn("startupTimingEvents", server)
        self.assertIn("launcher_start_accepted", server)
        self.assertIn("service_first_ready", server)
        self.assertIn("service_first_operational_degraded", server)
        self.assertIn("service_waiting", server)
        self.assertIn("startup_expected_services_operational_with_degraded", server)
        self.assertIn("operationalServiceIds", server)
        self.assertIn("degradedServiceIds", server)
        self.assertIn("startup.degraded", app)
        self.assertIn("startup.operational", app)
        self.assertIn("firstOperationalElapsedMs", app)
        self.assertIn("criticalPathServiceId", server)
        self.assertIn("startupReadyTimeoutMsForService", server)
        self.assertIn("readyTimeoutMs", server)
        self.assertIn("explicit_service_ready_timeout", server)
        self.assertIn("no_explicit_service_ready_timeout", server)
        self.assertIn("diagnosticSurfacesSummary", server)
        self.assertIn("getStartupTimingPayload", server)
        self.assertIn("getDiagnosticSurfacesPayload", server)
        self.assertIn("demoTimedActionReadiness", server)
        self.assertIn("/api/startup-timing", server)
        self.assertIn("/api/diagnostic-surfaces", server)
        self.assertIn("/api/demo-timed-action-readiness", server)
        self.assertIn("launcher_demo_timed_action_readiness.v0", server)
        self.assertIn("ready_for_reviewed_first_action_handoff", server)
        self.assertIn("remaining_ms_to_first_action_target", server)
        self.assertIn("next_operator_steps", server)
        self.assertIn("foreground_projection_visual", server)
        self.assertIn("submit_non_appliance_preface", server)
        self.assertIn("select_reviewed_ac_action_or_hold", server)
        self.assertIn("aircon_cool", server)
        self.assertIn("aircon_hvac_off", server)
        self.assertIn("latency_bottleneck_hints", server)
        self.assertIn("command_submission_authorized_by_this_summary: false", server)
        self.assertIn("Action bridge operator", server)
        self.assertIn("/operator", server)
        self.assertIn("Action bridge operator", app)
        self.assertIn("家電操作面", app)
        self.assertIn("source_static_diagnostic_surface_inventory.v0", server)
        self.assertIn("audio_input_awareness", server)
        self.assertIn("self_mirror_temporal_motion", server)
        self.assertIn("projection_visual_response_binding", server)
        self.assertIn("message_receiver_client_binding_status_summary", server)
        self.assertIn("os_display_window_prompt", server)
        self.assertIn("live_capture_default_class: 'disabled'", server)
        self.assertIn("raw_private_publication_flags: false", server)

    def test_launcher_readme_documents_fast_timing_and_no_live_diagnostics(self) -> None:
        readme = (ROOT / "tools" / "home-control-launcher" / "README.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("VoicevoxReadyTimeoutSeconds", readme)
        self.assertIn("MediapipeReadyTimeoutSeconds", readme)
        self.assertIn("8-second wait", readme)
        self.assertIn("GET /api/startup-timing", readme)
        self.assertIn("launcher_startup_timing.v0", readme)
        self.assertIn("timeline events", readme)
        self.assertIn("GET /api/diagnostic-surfaces", readme)
        self.assertIn("GET /api/demo-timed-action-readiness", readme)
        self.assertIn("first-feedback/first-action readiness", readme)
        self.assertIn("remaining milliseconds", readme)
        self.assertIn("next operator steps", readme)
        self.assertIn("aircon_cool", readme)
        self.assertIn("aircon_hvac_off", readme)
        self.assertIn("collect-demo-timing.mjs", readme)
        self.assertIn("polls only the Launcher summary endpoints", readme)
        self.assertIn("Self Mirror temporal motion", readme)
        self.assertIn("Action bridge operator", readme)
        self.assertIn("not command authority", readme)
        self.assertIn("class/count/timing summaries only", readme)
        self.assertIn("do not perform", readme)
        self.assertIn("Home Control", readme)

    def test_timing_collector_is_read_only_summary_collector(self) -> None:
        collector = read_timing_collector()

        self.assertIn("launcher_demo_timing_snapshot.v0", collector)
        self.assertIn("/api/demo-timed-action-readiness", collector)
        self.assertIn("/api/startup-timing", collector)
        self.assertIn("/api/diagnostic-surfaces", collector)
        self.assertIn("ready_for_reviewed_first_action_handoff", collector)
        self.assertIn("command_submission_count: 0", collector)
        self.assertIn("raw_private_publication_flags: false", collector)
        self.assertIn("not_home_assistant_or_home_control_operation", collector)
        self.assertNotIn("`${baseUrl}/api/start`", collector)
        self.assertNotIn("`${baseUrl}/api/stop`", collector)
        self.assertNotIn("/operator/execute", collector)

    def test_demo_safe_defaults_start_disabled_and_separate_readiness(self) -> None:
        defaults = read_demo_safe_defaults()
        rows = defaults["rows"]
        row_ids = {row["id"] for row in rows}

        self.assertEqual(defaults["schema_version"], "demo_safe_settings.v0")
        self.assertFalse(defaults["fresh_clone_default_enabled"])
        self.assertTrue(rows)
        self.assertTrue(all(row["enabled"] is False for row in rows))

        self.assertIn("appliance.aircon_cool_restore", row_ids)
        self.assertIn("appliance.light_command_stimulus", row_ids)
        self.assertIn("appliance.fan_command_stimulus", row_ids)
        self.assertIn("appliance.door_open_close", row_ids)
        self.assertIn("appliance.vacuum_start_return", row_ids)
        self.assertIn("audio.voicevox_local_speech", row_ids)
        self.assertIn("audio.browser_or_pc_output_awareness", row_ids)
        self.assertIn("avatar.aituber_projection_surface", row_ids)
        self.assertIn("avatar.expression_or_motion_request", row_ids)
        self.assertIn("display.projection_visual_mode", row_ids)
        self.assertIn("display.self_mirror_visible_motion", row_ids)

        for row in rows:
            self.assertIn("restore_required", row)
            self.assertIn("max_action_count", row)
            self.assertIn("max_duration_sec", row)
            self.assertIn("proof_ceiling", row)
            self.assertIn("does_not_prove", row)

    def test_demo_safe_defaults_include_all_appliance_command_stimuli(self) -> None:
        defaults = read_demo_safe_defaults()
        rows = {row["id"]: row for row in defaults["rows"]}

        expected_sequences = {
            "appliance.aircon_cool_restore": ["aircon_cool", "aircon_hvac_off"],
            "appliance.light_command_stimulus": ["light_on"],
            "appliance.fan_command_stimulus": ["fan_on"],
            "appliance.door_open_close": ["door_open", "door_close"],
            "appliance.vacuum_start_return": ["vacuum_start", "vacuum_return"],
        }
        for row_id, action_ids in expected_sequences.items():
            with self.subTest(row_id=row_id):
                row = rows[row_id]
                self.assertEqual(row["area"], "appliance")
                self.assertEqual(row["action_ids"], action_ids)
                self.assertTrue(row["feedback_stimulus_class"].startswith("appliance_command_stimulus"))
                self.assertIn("state_requirement_class", row)
                self.assertGreater(row["timing_estimate_sec"], 0)
                self.assertTrue(row["measurement_required"])

        self.assertFalse(rows["appliance.light_command_stimulus"]["restore_required"])
        self.assertFalse(rows["appliance.fan_command_stimulus"]["restore_required"])
        self.assertTrue(rows["appliance.door_open_close"]["restore_required"])
        self.assertTrue(rows["appliance.vacuum_start_return"]["restore_required"])

    def test_launcher_first_view_keeps_quick_links_and_density_hooks(self) -> None:
        html = read_public("index.html")
        css = read_public("styles.css")

        self.assertIn("Quick Links", html)
        self.assertIn('class="panel read-surface reference-panel"', html)
        self.assertIn("grid-template-columns: clamp(286px, 20%, 310px) minmax(0, 1fr)", css)
        self.assertIn(".profile-summary-grid", css)
        self.assertIn(".launch-panel .option-drawer", css)

    def test_launch_summary_warns_about_duplicate_ports(self) -> None:
        app = read_public("app.js")

        self.assertIn("const renderLaunchSummary", app)
        self.assertIn("const summarizeLaunchScope", app)
        self.assertIn("Duplicate port values", app)
        self.assertIn("Check conflict", app)
        self.assertIn("services-summary", app)
        self.assertIn("launch-scope-enabled", app)
        self.assertIn("launch-scope-skipped", app)
        self.assertIn("ports-drawer-summary", app)

    def test_launcher_switches_read_as_positive_start_scope(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn("Start Stack starts enabled/expected services only.", html)
        self.assertIn("const displaySwitchValue", app)
        self.assertIn("const setSwitchValue", app)
        self.assertIn("field.startsWith('Skip') ? !state.options[field]", app)
        self.assertIn("field.startsWith('Skip') ? !checked : checked", app)
        self.assertIn("Start expression UI", app)
        self.assertIn("Start action bridge", app)
        self.assertIn("Start environment state", app)
        self.assertIn("Start reflex sensor", app)
        self.assertIn("Start vision snapshot", app)
        self.assertIn("Start display runtime GUI", app)
        self.assertIn("Require VOICEVOX readiness check", app)
        self.assertNotIn("Disable expression UI", app)
        self.assertNotIn("Disable action bridge", app)

    def test_launcher_review_ui_has_no_legacy_compatibility_controls(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        server = read_launcher_server()
        system = read_system_script()
        stack = read_stack_start_script()
        status = read_stack_status_script()

        self.assertNotIn("compatibility-switch-grid", html)
        self.assertNotIn("runtime-drawer-summary", html)
        self.assertNotIn("renderSwitchGroup('compatibility-switch-grid'", app)
        self.assertNotIn("Legacy paths active", app)
        self.assertNotIn("Legacy paths off", app)
        self.assertNotIn("'headless'", server)
        self.assertNotIn('"headless"', system)
        self.assertNotIn('"headless"', stack)
        self.assertNotIn("serve_websocket.py", stack)
        self.assertNotIn("pids.mediapipe_ws", server)
        self.assertNotIn('pidState["mediapipe_ws"]', status)

    def test_launcher_public_ui_supports_english_and_japanese_language_mode(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn('id="language-switch"', html)
        self.assertIn('data-language="en"', html)
        self.assertIn('data-language="ja"', html)
        self.assertIn('data-i18n="launch.title"', html)
        self.assertIn('data-i18n="launchScope.statement"', html)
        self.assertIn('data-i18n="quickLinks.title"', html)
        self.assertIn('data-i18n="command.title"', html)
        self.assertIn('data-i18n="log.title"', html)

        self.assertIn("const LANGUAGE_STORAGE_KEY = 'sword.launcher.language'", app)
        self.assertIn("const translations", app)
        self.assertIn("document.documentElement.lang = state.language", app)
        self.assertIn("window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)", app)
        self.assertIn("'launch.title': 'Launch configuration'", app)
        self.assertIn("'launch.title': '起動設定'", app)
        self.assertIn("'button.start': 'Start Stack'", app)
        self.assertIn("'button.start': '起動する'", app)
        self.assertIn("'launchScope.statement': 'Start Stack starts enabled/expected services only.'", app)
        self.assertIn("'launchScope.statement': 'Start Stack は有効な起動対象だけを開始します。'", app)
        self.assertIn("'service.header.target': 'Start target'", app)
        self.assertIn("'service.header.target': '起動対象'", app)
        self.assertIn("'quickLinks.title': '確認リンク'", app)
        self.assertIn("'command.title': '起動コマンド確認'", app)
        self.assertIn("'log.title': 'ランチャー記録'", app)

    def test_launcher_language_mode_preserves_technical_values(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn('data-i18n="port.expression">Expression</span><input id="AituberPort"', html)
        self.assertIn('data-i18n="port.thoughtCore">Thought Core</span><input id="ThoughtCorePort"', html)
        self.assertIn("<span>VOICEVOX URL</span>", html)
        self.assertIn('id="HomeControlConfigPath"', html)
        self.assertIn("$('command-preview').textContent = formatReviewCommandPreview(commandLine)", app)

    def test_launcher_japanese_copy_uses_meaning_first_labels(self) -> None:
        app = read_public("app.js")

        ja_table = extract_between(app, "  ja: {", "  }\n}\n\nconst t =")
        service_labels_ja = extract_between(app, "const serviceLabelsJa = {", "}\nconst hiddenServiceKeys")
        launch_scope_labels_ja = extract_between(app, "const launchScopeLabelsJa = {", "}\n\nconst fieldLabels")
        field_labels_ja = extract_between(app, "const fieldLabelsJa = {", "}\n\nconst switchDescriptions")
        switch_descriptions_ja = extract_between(app, "const switchDescriptionsJa = {", "}\n\nconst positiveDisplayFields")
        endpoint_labels_ja = extract_between(app, "  const labelsJa = {", "  }\n  if (state.language === 'ja')")

        self.assertIn("'port.thoughtCore': '思考中枢'", ja_table)
        self.assertIn("'summary.fallbackOnly': '簡易応答のみ'", ja_table)
        self.assertIn("'summary.providerAllowed': '会話LLMを使用'", ja_table)
        self.assertIn("thought_core_api: '思考中枢API'", service_labels_ja)
        self.assertIn("thought_core_watcher: '思考中枢の監視'", service_labels_ja)
        self.assertIn("vision_snapshot_processor: '視覚状態の取得'", service_labels_ja)
        self.assertIn("EnableThoughtCore: '思考中枢API'", launch_scope_labels_ja)
        self.assertIn("SkipVisionSnapshotProcessor: '視覚状態の取得'", launch_scope_labels_ja)
        self.assertIn("EnableThoughtCore: '思考中枢APIを起動'", field_labels_ja)
        self.assertIn("SkipVisionSnapshotProcessor: '視覚状態の取得を起動'", field_labels_ja)
        self.assertIn("外部LLMを使わない簡易応答のみ", switch_descriptions_ja)
        self.assertIn("'Thought Core health': '思考中枢の状態'", endpoint_labels_ja)
        self.assertIn("'Vision Snapshot Processor WebSocket': '視覚状態取得WebSocket'", endpoint_labels_ja)

        for japanese_block in [
            ja_table,
            service_labels_ja,
            launch_scope_labels_ja,
            field_labels_ja,
            switch_descriptions_ja,
            endpoint_labels_ja,
        ]:
            self.assertNotIn("ソート", japanese_block)
            self.assertNotIn("ビジョンスナップショット", japanese_block)
            self.assertNotIn("フォールバック", japanese_block)
            self.assertNotIn("プロバイダー", japanese_block)

    def test_launcher_command_preview_uses_review_formatter(self) -> None:
        app = read_public("app.js")

        self.assertIn("const formatReviewCommandPreview", app)
        self.assertIn("const setCommandPreview", app)
        self.assertIn("setCommandPreview(preview.commandLine)", app)
        self.assertIn("setCommandPreview(payload.preview?.commandLine || '')", app)
        self.assertIn(
            "$('command-preview').textContent = formatReviewCommandPreview(commandLine)",
            app,
        )
        self.assertNotIn("$('command-preview').textContent = commandLine", app)

    def test_launcher_log_view_groups_entries_without_breaking_plain_text_copy(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn('class="log-meta-strip"', html)
        self.assertIn('id="log-output" class="log-output"', html)
        self.assertIn("'log.viewMode': 'Grouped by module'", app)
        self.assertIn("'log.copyMode': 'Copy keeps plain text'", app)
        self.assertIn("'log.viewMode': '機能別に整理'", app)
        self.assertIn("'log.copyMode': 'コピーは通常テキスト'", app)
        self.assertIn("latestLogTailRaw", app)
        self.assertIn("const parseLauncherLogLine", app)
        self.assertIn("const renderLauncherLog", app)
        self.assertIn("const prependLauncherLog", app)
        self.assertIn("renderLauncherLog(payload.logTail)", app)
        self.assertIn("renderLauncherLog(logs.logTail)", app)
        self.assertIn("navigator.clipboard.writeText(state.latestLogTailRaw", app)
        self.assertIn(".log-entry-source", css)
        self.assertIn(".log-entry-message", css)
        self.assertIn("user-select: text", css)

    def test_operation_banner_exposes_startup_progress_bar(self) -> None:
        html = read_public("index.html")
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn('id="operation-progress"', html)
        self.assertIn('role="progressbar"', html)
        self.assertIn('id="operation-progress-bar"', html)
        self.assertIn('id="operation-progress-label"', html)
        self.assertIn("setOperationProgressFromSummary", app)
        self.assertIn("remaining", app)
        self.assertIn(".operation-progress", css)

    def test_stop_stack_reports_verified_shutdown_or_residue(self) -> None:
        server = read_launcher_server()
        runtime = (
            LAUNCHER_SERVER.parent / "launcher-supervisor-runtime.js"
        ).read_text(encoding="utf-8")
        stop_stack = extract_between(
            server,
            "const stopStack",
            "const reclaimManagedPortsFromLauncher",
        )
        stop_owned = extract_between(
            runtime,
            "  async stopOwnedServices",
            "  async rollback",
        )

        self.assertIn("launcherRuntime.stop", stop_stack)
        self.assertNotIn("waitForStackStopVerification", stop_stack)
        self.assertIn("const cleanupClear = await this.closeClientAndPlan()", stop_owned)
        self.assertLess(
            stop_owned.index("const cleanupClear = await this.closeClientAndPlan()"),
            stop_owned.index("if (held) this.apply(held.event_type, held.service_id"),
        )

    def test_service_rows_mark_startup_booting_progress(self) -> None:
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn("const serviceIsBooting", app)
        self.assertIn("state.operation === 'starting'", app)
        self.assertIn("serviceStateGroup(service?.state) !== 'ok'", app)
        self.assertIn('data-booting="${isBooting ? \'true\' : \'false\'}"', app)
        self.assertIn('.service-row[data-booting="true"]', css)
        self.assertIn("@keyframes service-row-scan", css)
        self.assertIn("@keyframes service-row-boot-line", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("position: absolute", css)

    def test_service_rows_expose_startup_target_without_claiming_runtime_state(self) -> None:
        app = read_public("app.js")
        css = read_public("styles.css")

        self.assertIn("const serviceStartupTargetFields", app)
        self.assertIn("const startupTargetFieldsByService", app)
        self.assertIn("const serviceStartupTargetBlockers", app)
        self.assertIn("vision_snapshot_processor: ['SkipVisionSnapshotProcessor']", app)
        self.assertIn("voicevox: ['SkipVoicevoxCheck']", app)
        self.assertIn("t('service.requires', { targets: targetBlockers.join(', ') })", app)
        self.assertIn("const setServiceStartupTarget", app)
        self.assertIn('data-service-startup-target="${escapeHtml(name)}"', app)
        self.assertIn('data-startup-target="${included ? \'included\' : \'skipped\'}"', app)
        self.assertIn("Startup target only; current runtime state is unchanged.", app)
        self.assertIn("renderControls()", app)
        self.assertIn("renderLaunchSummary()", app)
        self.assertIn("renderServices(state.latestServices || {})", app)
        self.assertIn("Start target", app)
        self.assertIn(".service-startup-target", css)
        self.assertIn(".service-target-toggle", css)
        self.assertIn('.service-row[data-startup-target="skipped"]', css)

    def test_quick_links_use_display_safe_environment_endpoint(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")

        self.assertIn("Environment display state", server)
        self.assertIn("/indicators/current", server)
        self.assertNotIn("name: 'Environment current state'", server)
        self.assertNotIn("/environment/current`,", server)
        self.assertIn("'Environment display state': 'Env state'", app)

    def test_launcher_status_uses_lightweight_action_bridge_probe(self) -> None:
        server = read_launcher_server()

        self.assertIn(
            "`http://127.0.0.1:${options.HomeAssistantBridgePort}/operator`",
            server,
        )
        self.assertNotIn(
            "`http://127.0.0.1:${options.HomeAssistantBridgePort}/health`,\n      2500",
            server,
        )

    def test_projection_quick_links_use_canonical_trailing_slash_routes(self) -> None:
        server = read_launcher_server()
        stack_start = read_stack_start_script()
        app = read_public("app.js")

        self.assertIn("name: 'Passive Projection'", server)
        self.assertIn("/projection-visual/`", server)
        self.assertIn("/projection-visual/?mode=passive&hud=0`", server)
        self.assertIn("/projection-visual/?mode=passive", stack_start)
        self.assertIn("'Passive Projection': 'Stage'", app)
        self.assertNotIn("/projection-visual?mode=passive", server)
        self.assertNotIn("/projection-visual?mode=passive", stack_start)

    def test_stack_start_reclaims_only_managed_stale_port_owners(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("Get-ReclaimableRootForPortConflict", stack_start)
        self.assertIn("Stop-ReclaimablePortConflicts", stack_start)
        self.assertIn("Test-ExternalProcessDenied", stack_start)

    def test_stack_start_uses_voicevox_readiness_helper(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("scripts\\check-voicevox-readiness.ps1", stack_start)
        self.assertIn("-StartIfNeeded", stack_start)
        self.assertIn("started existing local VOICEVOX", stack_start)
        self.assertIn("If you intentionally do not use VOICEVOX", stack_start)

    def test_home_control_stack_prefers_local_live_config(self) -> None:
        server = read_launcher_server()
        stack_start = read_stack_start_script()

        self.assertIn("DEFAULT_HOME_CONTROL_LIVE_CONFIG", server)
        self.assertIn("'home-control.live.yaml'", server)
        self.assertIn("defaultHomeControlConfigPath", server)
        self.assertIn("!normalized.SkipHomeAssistantBridge", server)
        self.assertIn("normalized.HomeControlConfigPath = defaultHomeControlConfigPath()", server)

        self.assertIn("function Resolve-HomeControlConfigPath", stack_start)
        self.assertIn("local\\env\\home-control.live.yaml", stack_start)
        self.assertIn("function Assert-HomeControlConfigNotDemoLiveMapping", stack_start)
        self.assertIn("script\\.demo_light_(on|off)", stack_start)
        self.assertIn("selected bridge config still maps light actions to demo scripts", stack_start)

    def test_launcher_status_exposes_home_control_config_state(self) -> None:
        server = read_launcher_server()

        self.assertIn("compactHomeControlConfigState", server)
        self.assertIn("homeControlConfigState", server)
        self.assertIn("expected_profile", server)
        self.assertIn("active_profile", server)
        self.assertIn("light_demo_mappings_present", server)
        self.assertIn("live_home_invalid", server)
        self.assertIn("payload_policy: 'compact_redacted'", server)

    def test_stack_start_defers_camera_hub_ready_wait_until_after_spawn(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("$mediapipeCameraHubChild = $null", stack_start)
        self.assertIn("$delayedVisionSnapshotSpecs = @()", stack_start)
        self.assertIn('$spec.Name -eq "vision_snapshot_processor"', stack_start)
        self.assertIn("$mediapipeCameraHubChild = $rootChild", stack_start)
        self.assertIn("if ($null -ne $mediapipeCameraHubChild)", stack_start)
        self.assertIn("foreach ($spec in $delayedVisionSnapshotSpecs)", stack_start)

    def test_camera_hub_state_authority_is_validated_and_copied_without_raw_detail(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        stack_start = read_stack_start_script()
        stack_status = read_stack_status_script()

        for source in (server, stack_start, stack_status):
            self.assertIn("camera_state_class", source)
            self.assertIn("mediapipe-sword-sign", source)
            self.assertIn("mediapipe_camera_hub_stack", source)
            self.assertIn("unavailable", source)
            self.assertIn("recovering", source)
            self.assertIn("ready", source)

        self.assertIn("CAMERA_HUB_STATE_FILE", server)
        self.assertIn("cameraHubManifestState", server)
        self.assertIn("cameraHubServiceState", server)
        self.assertIn("camera_state_validation_class", server)
        self.assertIn("camera_state_operational", server)
        status_projection = extract_between(server, "const getStatus", "const getState")
        self.assertIn("launcherRuntime.publicState()", status_projection)
        self.assertNotIn("checkTcpIf(", status_projection)
        self.assertNotIn("cameraHubManifestState(", status_projection)
        camera_manifest_reader = extract_between(
            server,
            "const cameraHubManifestState",
            "const cameraHubServiceState",
        )
        self.assertNotIn("child_process_file", camera_manifest_reader)

        self.assertIn("Get-ValidatedCameraHubState", stack_start)
        self.assertIn("Test-CameraHubRequiredProcessesRunning", stack_start)
        self.assertIn("Camera Hub operational: camera_state_", stack_start)
        self.assertIn("$MediapipeCameraHubChildProcessFile", stack_start)
        self.assertNotIn("Camera Hub topics ready: $lastDetail", stack_start)

        self.assertIn("$CameraHubStateFile", stack_status)
        self.assertIn("camera_manifest_runtime_or_shape_validation_failed", stack_status)
        self.assertIn("Test-CameraHubRegistryEntry", stack_status)
        self.assertIn("Test-CameraHubRuntimeOwnership", stack_status)
        self.assertIn("[int]::TryParse($pidText", stack_status)
        self.assertIn("[DateTimeOffset]::TryParse($startedAtText", stack_status)
        self.assertIn("camera_registry_identity_invalid", server)
        self.assertIn("camera_runtime_root_identity_invalid", server)
        self.assertIn("camera_runtime_listener_lineage_invalid", server)
        self.assertIn("-StateOverride $mediapipeState", stack_status)
        self.assertNotIn("ready_detail", stack_status)

        self.assertIn("startup.degraded", app)
        self.assertIn("カメラ入力なしで稼働中", app)

    def test_thought_core_no_provider_option_flows_to_child_after_env_import(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        system = read_system_script()
        stack_start = read_stack_start_script()
        thought_start = read_thought_core_start_script()

        self.assertIn("ThoughtCoreNoProvider: false", server)
        self.assertIn("'ThoughtCoreNoProvider'", app)
        self.assertIn("Use configured conversation LLM", app)
        self.assertIn("configured Thought Core LLM provider", app)
        self.assertIn("local fallback-only mode", app)
        self.assertNotIn("Use OpenAI-compatible LLM responses", app)
        self.assertIn("const positiveDisplayFields = new Set(['ThoughtCoreNoProvider'])", app)
        self.assertIn("positiveDisplayFields.has(field)", app)
        self.assertIn("setOption(field, !checked)", app)
        self.assertNotIn("Force Thought Core fallback-only", app)
        self.assertIn("[switch]$ThoughtCoreNoProvider", system)
        self.assertIn("ThoughtCoreNoProvider = [bool]$ThoughtCoreNoProvider", system)
        self.assertNotIn("-ThoughtCoreNoProvider", system)
        self.assertIn("[switch]$ThoughtCoreNoProvider", stack_start)
        self.assertIn('"THOUGHT_CORE_FORCE_NO_PROVIDER"', stack_start)
        self.assertIn('$thoughtCoreEnvironment["THOUGHT_CORE_LLM_ENABLED"] = "0"', stack_start)
        self.assertIn(
            '$thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"',
            stack_start,
        )
        import_index = thought_start.index("Import-SwordEnv")
        force_index = thought_start.index("THOUGHT_CORE_FORCE_NO_PROVIDER")
        self.assertLess(import_index, force_index)
        self.assertIn('$env:THOUGHT_CORE_LLM_ENABLED = "0"', thought_start)
        self.assertIn('$env:THOUGHT_CORE_ACTION_LLM_ENABLED = "0"', thought_start)

    def test_launcher_blocks_unknown_profile_parser_paths_before_stack_start(self) -> None:
        server = read_launcher_server()

        self.assertIn("const requireKnownProfile", server)
        self.assertIn("unknown_profile", server)
        self.assertIn("blocked_unknown_profile", server)
        self.assertIn("requestedProfileClass: compactProfileId(profileId)", server)
        self.assertIn("const profileError = requireKnownProfile(profileId)", server)
        self.assertIn("if (!preview.ok)", server)
        self.assertIn("sendJson(response, 400, profileError)", server)
        self.assertIn("profileConfigState", server)

    def test_launcher_profiles_keep_skip_enabled_combinations_explicit(self) -> None:
        profiles = {profile["id"]: profile for profile in read_launcher_profiles()}

        thought_core = profiles["thought-core-v0"]["options"]
        self.assertTrue(thought_core["EnableThoughtCore"])
        self.assertEqual(
            thought_core["ThoughtCoreLlmProvider"],
            "sword-openai-broker",
        )
        self.assertTrue(thought_core["EnableThoughtCoreWatch"])

        demo_fast = profiles["demo-fast"]["options"]
        self.assertEqual(profiles["demo-fast"]["group"], "Compatibility")
        self.assertIn(
            "does not satisfy agentic product acceptance",
            profiles["demo-fast"]["description"],
        )
        self.assertFalse(demo_fast["StopExisting"])
        self.assertTrue(demo_fast["EnableThoughtCore"])
        self.assertFalse(demo_fast["EnableThoughtCoreWatch"])
        self.assertEqual(demo_fast["ThoughtCoreLlmProvider"], "codex-cli")
        self.assertFalse(demo_fast["ThoughtCoreNoProvider"])
        self.assertEqual(demo_fast["VoicevoxReadyTimeoutSeconds"], 8)
        self.assertTrue(demo_fast["SkipHomeAssistantBridge"])
        self.assertTrue(demo_fast["SkipEnvironmentState"])
        self.assertTrue(demo_fast["SkipMediapipe"])
        self.assertTrue(demo_fast["SkipVisionSnapshotProcessor"])
        self.assertTrue(demo_fast["SkipTouchDesignerGui"])

        demo_fast_action = profiles["demo-fast-action"]["options"]
        self.assertEqual(profiles["demo-fast-action"]["group"], "Compatibility")
        self.assertIn(
            "does not satisfy agentic intent or product acceptance",
            profiles["demo-fast-action"]["description"],
        )
        self.assertFalse(demo_fast_action["StopExisting"])
        self.assertTrue(demo_fast_action["EnableThoughtCore"])
        self.assertFalse(demo_fast_action["EnableThoughtCoreWatch"])
        self.assertEqual(demo_fast_action["ThoughtCoreLlmProvider"], "codex-cli")
        self.assertFalse(demo_fast_action["ThoughtCoreNoProvider"])
        self.assertEqual(demo_fast_action["VoicevoxReadyTimeoutSeconds"], 8)
        self.assertNotIn("SkipHomeAssistantBridge", demo_fast_action)
        self.assertTrue(demo_fast_action["SkipEnvironmentState"])
        self.assertTrue(demo_fast_action["SkipMediapipe"])
        self.assertTrue(demo_fast_action["SkipVisionSnapshotProcessor"])
        self.assertTrue(demo_fast_action["SkipTouchDesignerGui"])

        camera_debug = profiles["camera-debug"]["options"]
        self.assertTrue(camera_debug["SkipHomeAssistantBridge"])
        self.assertTrue(camera_debug["SkipEnvironmentState"])
        self.assertTrue(camera_debug["SkipAituber"])
        self.assertTrue(camera_debug["SkipTouchDesignerGui"])
        self.assertFalse(camera_debug["MediapipeNoBrowser"])
        self.assertTrue(camera_debug["MediapipeOpenBrowser"])

        aituber_only = profiles["aituber-only"]["options"]
        self.assertTrue(aituber_only["SkipHomeAssistantBridge"])
        self.assertTrue(aituber_only["SkipEnvironmentState"])
        self.assertTrue(aituber_only["SkipMediapipe"])
        self.assertTrue(aituber_only["SkipVisionSnapshotProcessor"])
        self.assertTrue(aituber_only["SkipTouchDesignerGui"])

        response_provider_profiles = {
            profile_id
            for profile_id, profile in profiles.items()
            if "ThoughtCoreLlmProvider" in profile["options"]
        }
        self.assertEqual(
            response_provider_profiles,
            {"thought-core-v0", "demo-fast", "demo-fast-action"},
        )

    def test_launcher_passes_readiness_timeouts_to_node_plan_and_compatibility_json(self) -> None:
        server = read_launcher_server()
        app = read_public("app.js")
        system = read_system_script()
        stack_start = read_stack_start_script()

        self.assertIn("VoicevoxReadyTimeoutSeconds: 45", server)
        self.assertIn("MediapipeReadyTimeoutSeconds: 90", server)
        self.assertIn("'VoicevoxReadyTimeoutSeconds'", server)
        self.assertIn("'MediapipeReadyTimeoutSeconds'", server)
        self.assertIn("options.VoicevoxReadyTimeoutSeconds", server)
        self.assertIn("options.MediapipeReadyTimeoutSeconds", server)
        self.assertIn("launcherRuntime.start", server)
        self.assertIn("numericOptionFields", app)
        self.assertIn("readyTimeoutOptionFields", app)
        self.assertIn("setReadyTimeoutOption", app)
        self.assertIn("[int]$VoicevoxReadyTimeoutSeconds = 45", system)
        self.assertIn("[int]$MediapipeReadyTimeoutSeconds = 90", system)
        self.assertIn("VoicevoxReadyTimeoutSeconds = $VoicevoxReadyTimeoutSeconds", system)
        self.assertIn("MediapipeReadyTimeoutSeconds = $MediapipeReadyTimeoutSeconds", system)
        self.assertNotIn("-VoicevoxReadyTimeoutSeconds", system)
        self.assertNotIn("-MediapipeReadyTimeoutSeconds", system)
        self.assertIn("[int]$VoicevoxReadyTimeoutSeconds = 45", stack_start)
        self.assertIn("[int]$MediapipeReadyTimeoutSeconds = 90", stack_start)
        self.assertIn("Assert-VoicevoxReady", stack_start)
        self.assertIn("-TimeoutSeconds $VoicevoxReadyTimeoutSeconds", stack_start)
        self.assertIn("Wait-CameraHubStackReady", stack_start)
        self.assertIn("-TimeoutSeconds $MediapipeReadyTimeoutSeconds", stack_start)

    def test_launcher_stack_log_strips_ansi_control_sequences(self) -> None:
        server = read_launcher_server()

        self.assertIn("stripAnsiControlSequences", server)
        self.assertIn("NO_COLOR: '1'", server)
        self.assertIn("FORCE_COLOR: '0'", server)
        self.assertIn("TERM: 'dumb'", server)
        self.assertIn("fs.appendFileSync(STACK_LOG_FILE, sanitizedContent, 'utf8')", server)
        self.assertIn("redactCameraSelectionInCommandText(", server)
        self.assertIn("stripAnsiControlSequences(buffer.toString('utf8'))", server)

    def test_launcher_config_status_uses_compact_redacted_classes(self) -> None:
        server = read_launcher_server()

        self.assertIn("compactProfileId", server)
        self.assertIn("knownProfileIds: profileIds()", server)
        self.assertIn("homeControlConfigProfileFromPath", server)
        self.assertIn("payload_policy: 'compact_redacted'", server)
        self.assertIn("live_home_invalid", server)
        self.assertNotIn("requestedProfileId: profileId", server)

    def test_launcher_environment_status_does_not_keep_stale_action_readiness_payload(self) -> None:
        server = read_launcher_server()

        self.assertNotIn("const compactActionReadiness = (action) =>", server)
        self.assertNotIn("const compactActionReadinessSummary = (summary) =>", server)
        self.assertNotIn("'live_test_readiness'", server)
        self.assertNotIn("'live_test_blockers'", server)
        self.assertNotIn("'restore_action_id'", server)
        self.assertNotIn("'stop_action_id'", server)
        self.assertNotIn("'test_now_count'", server)
        self.assertNotIn("'blocked_candidate_count'", server)
        self.assertNotIn("'HOME_ASSISTANT_TOKEN'", server)

    def test_fixed_start_summary_is_anchored_bounded_and_private(self) -> None:
        self.skipTest("N2 no longer reaches the legacy fixed-start collector")
        server = read_launcher_server()
        system = read_system_script()
        stack_start = read_stack_start_script()
        collector = extract_between(
            server,
            "const FIXED_START_FAILURE_CLASSES",
            "const startStack",
        )
        start_stack = extract_between(server, "const startStack", "const runScriptAndCollect")

        classes = (
            "camera_selection_missing",
            "voicevox_unavailable",
            "required_token_missing_or_short",
            "required_port_conflict",
            "dependency_or_tool_missing",
            "first_service_spawn_failed",
            "stack_start_failed_unknown",
        )
        for failure_class in classes:
            self.assertIn(f"'{failure_class}'", collector)
            self.assertIn(f'"{failure_class}"', system)
            self.assertIn(f'"{failure_class}"', stack_start)

        for failure_class in (
            "system_preflight_failed",
            "profile_preflight_failed",
            "delegated_stack_preflight_failed",
            "entrypoint_missing",
        ):
            self.assertIn(f"'{failure_class}'", collector)
            self.assertIn(f'"{failure_class}"', system)
        for failure_class in (
            "stack_preflight_failed",
            "stack_config_preflight_failed",
            "previous_stack_preflight_failed",
            "pid_registry_write_failed",
        ):
            self.assertIn(f"'{failure_class}'", collector)
            self.assertIn(f'"{failure_class}"', stack_start)

        self.assertIn("SWORD_FIXED_START_FAILURE_CLASS:$FailureClass", system)
        self.assertIn("SWORD_FIXED_START_FAILURE_CLASS:$FailureClass", stack_start)
        for producer in (system, stack_start):
            self.assertIn('GetEnvironmentVariable("SWORD_FIXED_START_FAILURE_FILE")', producer)
            self.assertIn("[System.IO.FileMode]::CreateNew", producer)
            self.assertIn("[System.IO.FileShare]::Read", producer)
            self.assertIn("Write-FixedStartFailureArtifact -FailureClass $FailureClass", producer)
            self.assertIn("[System.StringComparison]::OrdinalIgnoreCase", producer)
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"camera_selection_missing\"",
            system,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"dependency_or_tool_missing\"",
            system,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"voicevox_unavailable\"",
            stack_start,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"required_token_missing_or_short\"",
            stack_start,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"required_port_conflict\"",
            stack_start,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"first_service_spawn_failed\"",
            stack_start,
        )
        self.assertIn(
            "Write-FixedStartFailureMarker -FailureClass \"stack_start_failed_unknown\"",
            stack_start,
        )

        self.assertIn("/^SWORD_FIXED_START_FAILURE_CLASS:([a-z_]+)$/", collector)
        self.assertIn("FIXED_START_MAX_PARTIAL_BYTES = 96", collector)
        self.assertIn("FIXED_START_MAX_CAPTURE_BYTES = 192", collector)
        self.assertIn("FIXED_START_MAX_LINES = 127", collector)
        self.assertIn("FIXED_START_FAILURE_ARTIFACT_MAX_BYTES = 96", collector)
        self.assertIn("newFixedStartFailureArtifactPath", collector)
        self.assertIn("readFixedStartFailureArtifact", collector)
        self.assertIn("removeFixedStartFailureArtifact", collector)
        self.assertNotIn("FIXED_START_CAPTURE_TIMEOUT_MS", collector)
        self.assertIn("pending = { stdout: Buffer.alloc(0), stderr: Buffer.alloc(0) }", collector)
        self.assertIn("collector.consume('stdout', chunk)", start_stack)
        self.assertIn("collector.consume('stderr', chunk)", start_stack)
        self.assertIn(
            "exitIntent.code === 0 ? null : 'stack_start_failed_unknown'",
            start_stack,
        )
        self.assertIn("child.stdout.once('end', onSupervisorStdoutEnd)", start_stack)
        self.assertIn("child.stderr.once('end', onSupervisorStderrEnd)", start_stack)
        self.assertIn("classification_origin", collector)
        self.assertIn("launcher_pre_source_failed", collector)
        self.assertIn("const summaryClass = artifactClass || failureClass || fallbackClass", collector)
        self.assertIn("if (summaryClass)", collector)
        self.assertIn("pending.stdout = Buffer.alloc(0)", collector)
        self.assertIn("pending.stderr = Buffer.alloc(0)", collector)
        self.assertIn("onClose()", collector)
        self.assertIn("releaseCollectorListeners", start_stack)
        self.assertIn("onClose: () => releaseCollectorListeners()", start_stack)
        self.assertIn("removeListener('data', onSupervisorStdout)", start_stack)
        self.assertIn("removeListener('data', onSupervisorStderr)", start_stack)
        self.assertIn("[FIXED_START_FAILURE_ARTIFACT_ENV]: fixedStartFailureArtifactPath", start_stack)
        self.assertIn("const artifactFailureClass = readFixedStartFailureArtifact", start_stack)
        self.assertIn("removeFixedStartFailureArtifact(fixedStartFailureArtifactPath)", start_stack)
        self.assertIn("const removeArtifactAfterFailedSetup", start_stack)
        self.assertIn("releaseCollectorListeners()", start_stack)
        self.assertLess(
            start_stack.index("child.once('exit', removeArtifactAfterFailedSetup)"),
            start_stack.index("writeJsonFile(LAUNCHER_STATE_FILE, state)"),
        )
        self.assertLess(
            start_stack.index("child.stdout.on('data', onSupervisorStdout)"),
            start_stack.index("writeJsonFile(LAUNCHER_STATE_FILE, state)"),
        )

        self.assertIn("schema_version: 'launcher_fixed_start_summary.v1'", collector)
        self.assertIn("failure_class: failureClass", collector)
        self.assertNotIn("appendStackLog(chunk)", start_stack)
        self.assertNotIn("error.message", start_stack)
        self.assertNotIn("lastError:", start_stack)
        self.assertNotIn("preview.commandLine", start_stack)
        self.assertIn("publicFixedStartDiagnostic", server)
        self.assertNotIn("logTail: readTextTail(STACK_LOG_FILE)", server)
        self.assertIn("Save-StartupPidState", stack_start)
        self.assertIn("Assert-SelectedServiceEntrypoints -Specs $specs", stack_start)
        self.assertIn("Write-FixedStartFailureMarker -FailureClass \"pid_registry_write_failed\"", stack_start)
        self.assertIn("Write-FixedStartFailureMarker -FailureClass \"previous_stack_preflight_failed\"", stack_start)
        self.assertIn("Write-FixedStartFailureMarker -FailureClass \"delegated_stack_preflight_failed\"", system)
        self.assertIn("Write-FixedStartFailureMarker -FailureClass \"entrypoint_missing\"", system)
        fresh_state = extract_between(
            start_stack,
            "const state = {",
            "writeJsonFile(LAUNCHER_STATE_FILE, state)",
        )
        self.assertNotIn("fixedStartSummary", fresh_state)
        self.assertNotIn("readLauncherState()", fresh_state)

    def test_service_children_cannot_inherit_fixed_start_artifact_channel(self) -> None:
        stack_start = read_stack_start_script()
        supervised_start = extract_between(
            stack_start,
            "function Start-SupervisedProcess",
            "function Test-VisionSnapshotWorkerCommand",
        )
        child_environment_finalizer = extract_between(
            supervised_start,
            "    foreach ($key in $Spec.Environment.Keys) {",
            "\n\n    $process = [System.Diagnostics.Process]::new()",
        )
        artifact_removal = (
            '$null = $startInfo.Environment.Remove('
            '"SWORD_FIXED_START_FAILURE_FILE")'
        )

        self.assertEqual(supervised_start.count(artifact_removal), 1)
        self.assertLess(
            supervised_start.index(
                "$startInfo.Environment[$key] = [string]$Spec.Environment[$key]"
            ),
            supervised_start.index(artifact_removal),
        )
        self.assertTrue(child_environment_finalizer.rstrip().endswith(artifact_removal))

        powershell_program = """
$ErrorActionPreference = "Stop"
$env:SWORD_FIXED_START_FAILURE_FILE = "producer-owned-sentinel"

function Test-ServiceChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Case,
        [Parameter(Mandatory = $true)][hashtable]$Environment
    )

    $Spec = [pscustomobject]@{ Environment = $Environment }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.Environment["SWORD_FIXED_START_FAILURE_FILE"] = "inherited-sentinel"
""" + child_environment_finalizer + """

    return [pscustomobject]@{
        case = $Case
        child_has_artifact = $startInfo.Environment.ContainsKey(
            "SWORD_FIXED_START_FAILURE_FILE"
        )
        producer_retained = (
            $env:SWORD_FIXED_START_FAILURE_FILE -eq "producer-owned-sentinel"
        )
    }
}

@(
    Test-ServiceChildEnvironment -Case "ordinary" -Environment @{}
    Test-ServiceChildEnvironment -Case "compatibility" -Environment @{
        THOUGHT_CORE_LLM_PROVIDER = "codex-cli"
        SWORD_FIXED_START_FAILURE_FILE = "spec-spoof"
    }
    Test-ServiceChildEnvironment -Case "broker" -Environment @{
        THOUGHT_CORE_LLM_PROVIDER = "sword-openai-broker"
        SWORD_FIXED_START_FAILURE_FILE = "spec-spoof"
    }
) | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            [
                r"C:\Program Files\PowerShell\7\pwsh.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                powershell_program,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        cases = json.loads(completed.stdout)
        self.assertEqual(
            [case["case"] for case in cases],
            ["ordinary", "compatibility", "broker"],
        )
        for case in cases:
            self.assertFalse(case["child_has_artifact"])
            self.assertTrue(case["producer_retained"])

    def test_fixed_start_summary_collector_handles_split_and_bounded_private_input(self) -> None:
        self.skipTest("N2 no longer reaches the legacy fixed-start collector")
        server = read_launcher_server()
        collector = extract_between(
            server,
            "const FIXED_START_FAILURE_CLASSES",
            "const publicFixedStartDiagnostic",
        )
        start_stack = extract_between(
            server,
            "const startStack",
            "const runScriptAndCollect",
        )
        self.assertIn("FIXED_START_MAX_PARTIAL_BYTES = 96", collector)
        self.assertIn("FIXED_START_MAX_CAPTURE_BYTES = 192", collector)
        self.assertIn("discardUntilNewline", collector)
        self.assertIn("inspectionClosed", collector)
        self.assertIn("classification_origin", collector)
        self.assertIn("finalizeAfterDrain", start_stack)
        self.assertIn("child.once('exit'", start_stack)

        node_program = r"""
const { EventEmitter } = require('events')
const crypto = require('crypto')
const fs = require('fs')
const os = require('os')
const path = require('path')
let currentChild = null
let writes = []
let currentState = {}
let failNextStateWrite = false
let artifactFailureClassDuringSpawn = ''
let artifactFailureClassOnKill = ''
const PROJECT_ROOT = 'fixture-project-root'
const WORKSPACE_ROOT = 'fixture-workspace-root'
const STATE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'sword-fixed-start-'))
const LAUNCHER_STATE_FILE = 'fixture-state-file'
const ensureRuntimeDirs = () => {}
const normalizeOptions = () => ({})
const resolveVideoInputSelectionForStart = () => ({ ok: true, captureName: 'fixture-camera' })
const previewCommand = () => ({ ok: true, command: ['fixture-supervisor'], options: {} })
const saveConfig = () => {}
const nowIso = () => '2026-07-27T00:00:00.000Z'
const expectedServicesForOptions = () => []
const writeJsonFile = (_path, value) => {
  if (failNextStateWrite) {
    failNextStateWrite = false
    throw new Error('fixture state write failed')
  }
  currentState = value
  writes.push(value)
}
const readLauncherState = () => currentState
const trackedStream = () => {
  const stream = new EventEmitter()
  stream.dataRemovals = 0
  const removeListener = stream.removeListener.bind(stream)
  stream.removeListener = (event, listener) => {
    if (event === 'data') stream.dataRemovals += 1
    return removeListener(event, listener)
  }
  return stream
}
const childProcess = {
  spawn: (_command, _args, options) => {
    const child = new EventEmitter()
    child.pid = 9876
    child.stdout = trackedStream()
    child.stderr = trackedStream()
    child.spawnOptions = options
    child.killCalls = 0
    child.kill = () => {
      child.killCalls += 1
      if (artifactFailureClassOnKill) {
        fs.writeFileSync(
          options.env[FIXED_START_FAILURE_ARTIFACT_ENV],
          artifactFailureClassOnKill,
          { flag: 'wx' }
        )
      }
      child.emit('exit', 1)
      child.emit('close')
      return true
    }
    if (artifactFailureClassDuringSpawn) {
      fs.writeFileSync(
        options.env[FIXED_START_FAILURE_ARTIFACT_ENV],
        artifactFailureClassDuringSpawn,
        { flag: 'wx' }
      )
    }
    currentChild = child
    return child
  }
}
__COLLECTOR__
__START_STACK__
const summaryWrites = () => writes.filter((entry) => entry.fixedStartSummary)
const runSetupWriteFailure = () => {
  writes = []
  currentState = {}
  currentChild = null
  failNextStateWrite = true
  artifactFailureClassDuringSpawn = 'required_port_conflict'
  artifactFailureClassOnKill = 'required_port_conflict'
  const result = startStack('thought-core-v0', {})
  artifactFailureClassDuringSpawn = ''
  artifactFailureClassOnKill = ''
  const child = currentChild
  const artifactPath = child.spawnOptions.env[FIXED_START_FAILURE_ARTIFACT_ENV]
  return {
    resultOk: result.ok,
    summary: result.fixedStartSummary,
    writes: writes.length,
    killCalls: child.killCalls,
    stdoutRemovals: child.stdout.dataRemovals,
    stderrRemovals: child.stderr.dataRemovals,
    stdoutDataListeners: child.stdout.listenerCount('data'),
    stderrDataListeners: child.stderr.listenerCount('data'),
    artifactPath,
    artifactExists: fs.existsSync(artifactPath)
  }
}
const runLifecycle = ({
  code,
  stdout = [],
  stderr = [],
  repeat = false,
  artifactFailureClass = '',
  artifactAfterStdout = -1
}) => {
  writes = []
  currentState = {}
  currentChild = null
  const result = startStack('thought-core-v0', {})
  const child = currentChild
  child.emit('exit', code)
  const summariesAfterExit = summaryWrites().length
  if (artifactFailureClass && artifactAfterStdout < 0) {
    fs.writeFileSync(
      child.spawnOptions.env[FIXED_START_FAILURE_ARTIFACT_ENV],
      artifactFailureClass,
      { flag: 'wx' }
    )
  }
  for (let index = 0; index < stdout.length; index += 1) {
    const chunk = stdout[index]
    child.stdout.emit('data', Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8'))
    if (artifactFailureClass && artifactAfterStdout === index) {
      fs.writeFileSync(
        child.spawnOptions.env[FIXED_START_FAILURE_ARTIFACT_ENV],
        artifactFailureClass,
        { flag: 'wx' }
      )
    }
  }
  for (const chunk of stderr) child.stderr.emit('data', Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk, 'utf8'))
  child.stdout.emit('end')
  child.stderr.emit('end')
  child.emit('close')
  if (repeat) {
    child.stdout.emit('end')
    child.stderr.emit('end')
    child.emit('close')
  }
  return {
    resultOk: result.ok,
    summariesAfterExit,
    summaries: summaryWrites().map((entry) => entry.fixedStartSummary),
    stdoutRemovals: child.stdout.dataRemovals,
    stderrRemovals: child.stderr.dataRemovals,
    stdoutDataListeners: child.stdout.listenerCount('data'),
    stderrDataListeners: child.stderr.listenerCount('data'),
    artifactPath: child.spawnOptions.env[FIXED_START_FAILURE_ARTIFACT_ENV],
    artifactExists: fs.existsSync(child.spawnOptions.env[FIXED_START_FAILURE_ARTIFACT_ENV])
  }
}
const privateSentinel = 'PRIVATE_SUPERVISOR_SENTINEL_NEVER_PUBLIC'
const delegatedPrelude = [
  '[ops] command=start profile=thought-core-v0 state_dir=fixture-state-root',
  '[ops] delegate=start layer=ops script=fixture-start-home-control-stack.ps1',
  '[ops] delegate_args=-Profile thought-core-v0 -Services home_assistant_bridge,thought_core_api',
  privateSentinel
].join('\n') + '\n'
if (Buffer.byteLength(delegatedPrelude, 'utf8') <= 192) {
  throw new Error('delegated prelude must exhaust the raw inspection budget')
}
const opaqueLargeChunk = () => {
  const chunk = Buffer.alloc(1024 * 1024, 0x78)
  chunk.toString = () => { throw new Error('post-classification chunk decoded') }
  return chunk
}
const report = {
  setupWriteFailure: runSetupWriteFailure(),
  sourceAfterExit: runLifecycle({
    code: 1,
    stdout: [
      privateSentinel + '\nSWORD_FIXED_START_FAILURE_',
      'CLASS:required_port_conflict\n'
    ],
    repeat: true
  }),
  codeZeroNoMarker: runLifecycle({ code: 0, stdout: [privateSentinel + '\n'] }),
  nonzeroNoMarker: runLifecycle({ code: 1, stdout: [privateSentinel + '\nordinary\n'] }),
  overLimit: runLifecycle({
    code: 1,
    stdout: ['x'.repeat(97) + '\nSWORD_FIXED_START_FAILURE_CLASS:voicevox_unavailable\n']
  }),
  lineLimit: runLifecycle({
    code: 1,
    stdout: ['x\n'.repeat(127) + 'SWORD_FIXED_START_FAILURE_CLASS:voicevox_unavailable\n']
  }),
  inspectionExhausted: runLifecycle({
    code: 1,
    stdout: [
      Buffer.alloc(192, 0x78),
      Buffer.from('SWORD_FIXED_START_FAILURE_CLASS:voicevox_unavailable\n', 'utf8')
    ]
  }),
  delegatedAfterPrelude: runLifecycle({
    code: 1,
    stdout: [
      delegatedPrelude,
      Buffer.from('SWORD_FIXED_START_FAILURE_CLASS:required_port_conflict\n', 'utf8')
    ],
    artifactFailureClass: 'required_port_conflict',
    artifactAfterStdout: 0,
    repeat: true
  }),
  classifiedLargeChunk: runLifecycle({
    code: 1,
    stdout: [
      'SWORD_FIXED_START_FAILURE_CLASS:required_port_conflict\n',
      opaqueLargeChunk()
    ]
  })
}
fs.rmSync(STATE_DIR, { recursive: true, force: true })
process.stdout.write(JSON.stringify(report))
""".replace("__COLLECTOR__", collector).replace("__START_STACK__", start_stack)
        completed = subprocess.run(
            [r"C:\Program Files\nodejs\node.exe", "-e", node_program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotIn("PRIVATE_SUPERVISOR_SENTINEL_NEVER_PUBLIC", completed.stdout)
        results = json.loads(completed.stdout)

        setup_failure = results["setupWriteFailure"]
        self.assertFalse(setup_failure["resultOk"])
        self.assertEqual(setup_failure["writes"], 0)
        self.assertEqual(setup_failure["killCalls"], 1)
        self.assertEqual(setup_failure["stdoutRemovals"], 1)
        self.assertEqual(setup_failure["stderrRemovals"], 1)
        self.assertEqual(setup_failure["stdoutDataListeners"], 0)
        self.assertEqual(setup_failure["stderrDataListeners"], 0)
        self.assertFalse(setup_failure["artifactExists"])
        self.assertEqual(
            setup_failure["summary"],
            {
                "schema_version": "launcher_fixed_start_summary.v1",
                "status": "failed",
                "failure_class": "launcher_pre_source_failed",
                "classification_origin": "launcher_fallback",
                "captured_bytes": 0,
                "captured_lines": 0,
                "capture_limited": False,
                "proof_ceiling": "bounded_source_marker_diagnostic_only",
            },
        )

        source_after_exit = results["sourceAfterExit"]
        self.assertNotEqual(
            setup_failure["artifactPath"],
            results["codeZeroNoMarker"]["artifactPath"],
        )
        self.assertEqual(source_after_exit["summariesAfterExit"], 0)
        self.assertEqual(
            source_after_exit["summaries"],
            [
                {
                    "schema_version": "launcher_fixed_start_summary.v1",
                    "status": "failed",
                    "failure_class": "required_port_conflict",
                    "classification_origin": "source_marker",
                    "captured_bytes": 96,
                    "captured_lines": 2,
                    "capture_limited": False,
                    "proof_ceiling": "bounded_source_marker_diagnostic_only",
                }
            ],
        )
        for key in (
            "sourceAfterExit",
            "codeZeroNoMarker",
            "nonzeroNoMarker",
            "overLimit",
            "lineLimit",
            "inspectionExhausted",
            "delegatedAfterPrelude",
            "classifiedLargeChunk",
        ):
            self.assertTrue(results[key]["resultOk"])
            self.assertEqual(results[key]["stdoutRemovals"], 1)
            self.assertEqual(results[key]["stderrRemovals"], 1)
            self.assertEqual(results[key]["stdoutDataListeners"], 0)
            self.assertEqual(results[key]["stderrDataListeners"], 0)
            self.assertFalse(results[key]["artifactExists"])

        self.assertEqual(results["codeZeroNoMarker"]["summariesAfterExit"], 0)
        self.assertEqual(results["codeZeroNoMarker"]["summaries"], [])
        self.assertEqual(results["nonzeroNoMarker"]["summariesAfterExit"], 0)
        self.assertEqual(
            results["nonzeroNoMarker"]["summaries"],
            [
                {
                    "schema_version": "launcher_fixed_start_summary.v1",
                    "status": "failed",
                    "failure_class": "stack_start_failed_unknown",
                    "classification_origin": "launcher_fallback",
                    "captured_bytes": 50,
                    "captured_lines": 2,
                    "capture_limited": False,
                    "proof_ceiling": "bounded_source_marker_diagnostic_only",
                }
            ],
        )
        self.assertEqual(len(results["overLimit"]["summaries"]), 1)
        self.assertEqual(
            results["overLimit"]["summaries"][0]["failure_class"],
            "voicevox_unavailable",
        )
        self.assertEqual(
            results["overLimit"]["summaries"][0]["classification_origin"],
            "source_marker",
        )
        self.assertTrue(results["overLimit"]["summaries"][0]["capture_limited"])
        self.assertEqual(len(results["lineLimit"]["summaries"]), 1)
        self.assertEqual(
            results["lineLimit"]["summaries"][0]["failure_class"],
            "stack_start_failed_unknown",
        )
        self.assertEqual(
            results["lineLimit"]["summaries"][0]["classification_origin"],
            "launcher_fallback",
        )
        self.assertTrue(results["lineLimit"]["summaries"][0]["capture_limited"])
        self.assertEqual(len(results["inspectionExhausted"]["summaries"]), 1)
        self.assertEqual(
            results["inspectionExhausted"]["summaries"][0]["failure_class"],
            "stack_start_failed_unknown",
        )
        self.assertEqual(
            results["inspectionExhausted"]["summaries"][0]["classification_origin"],
            "launcher_fallback",
        )
        self.assertEqual(results["inspectionExhausted"]["summaries"][0]["captured_bytes"], 192)
        self.assertTrue(results["inspectionExhausted"]["summaries"][0]["capture_limited"])
        self.assertEqual(len(results["delegatedAfterPrelude"]["summaries"]), 1)
        self.assertEqual(
            results["delegatedAfterPrelude"]["summaries"][0]["failure_class"],
            "required_port_conflict",
        )
        self.assertEqual(
            results["delegatedAfterPrelude"]["summaries"][0]["classification_origin"],
            "source_marker",
        )
        self.assertEqual(
            results["delegatedAfterPrelude"]["summaries"][0]["captured_bytes"],
            192,
        )
        self.assertTrue(results["delegatedAfterPrelude"]["summaries"][0]["capture_limited"])
        self.assertEqual(len(results["classifiedLargeChunk"]["summaries"]), 1)
        self.assertEqual(
            results["classifiedLargeChunk"]["summaries"][0]["failure_class"],
            "required_port_conflict",
        )
        self.assertEqual(
            results["classifiedLargeChunk"]["summaries"][0]["classification_origin"],
            "source_marker",
        )
        self.assertLessEqual(results["classifiedLargeChunk"]["summaries"][0]["captured_bytes"], 192)
        self.assertTrue(results["classifiedLargeChunk"]["summaries"][0]["capture_limited"])

    def test_fixed_start_summary_collector_preserves_only_bounded_source_markers(self) -> None:
        collector = extract_between(
            read_launcher_server(),
            "const FIXED_START_FAILURE_CLASSES",
            "const publicFixedStartDiagnostic",
        )
        node_program = f"""
{collector}
const sentinel = 'PRIVATE_SUPERVISOR_SENTINEL_NEVER_PUBLIC'
const capture = (chunks, fallback = null, repeat = false) => {{
  const summaries = []
  let closeCount = 0
  const collector = createFixedStartSummaryCollector({{
    onSummary: (summary) => summaries.push(summary),
    onClose: () => {{ closeCount += 1 }}
  }})
  for (const [stream, value] of chunks) {{
    collector.consume(stream, Buffer.from(value, 'utf8'))
  }}
  collector.finalize(fallback)
  if (repeat) collector.finalize(fallback)
  return {{ summaries, closeCount }}
}}
const result = {{
  split: capture([['stdout', sentinel + '\\nSWORD_FIXED_START_FAILURE_'], ['stdout', 'CLASS:entrypoint_missing\\n']]),
  delayed: capture([['stdout', 'ordinary output\\n'], ['stderr', 'SWORD_FIXED_START_FAILURE_CLASS:pid_registry_write_failed\\n']]),
  first: capture([['stdout', 'SWORD_FIXED_START_FAILURE_CLASS:voicevox_unavailable\\nSWORD_FIXED_START_FAILURE_CLASS:required_port_conflict\\n']]),
  overlong: capture([['stdout', 'x'.repeat(97) + '\\nSWORD_FIXED_START_FAILURE_CLASS:required_port_conflict\\n']], 'stack_start_failed_unknown'),
  fallback: capture([['stdout', sentinel + '\\n']], 'stack_start_failed_unknown'),
  healthy: capture([], null, true)
}}
process.stdout.write(JSON.stringify(result))
"""
        completed = subprocess.run(
            [r"C:\Program Files\nodejs\node.exe", "-e", node_program],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertNotIn("PRIVATE_SUPERVISOR_SENTINEL_NEVER_PUBLIC", completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["split"]["summaries"][0]["failure_class"], "entrypoint_missing")
        self.assertEqual(result["split"]["summaries"][0]["classification_origin"], "source_marker")
        self.assertEqual(result["delayed"]["summaries"][0]["failure_class"], "pid_registry_write_failed")
        self.assertEqual(result["first"]["summaries"][0]["failure_class"], "voicevox_unavailable")
        self.assertEqual(result["overlong"]["summaries"][0]["failure_class"], "required_port_conflict")
        self.assertEqual(result["fallback"]["summaries"][0]["classification_origin"], "launcher_fallback")
        self.assertEqual(result["healthy"]["summaries"], [])
        for value in result.values():
            self.assertEqual(value["closeCount"], 1)
