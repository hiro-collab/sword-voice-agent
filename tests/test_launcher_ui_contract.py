import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = ROOT.parents[1]
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


def extract_between(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


class LauncherUiContractTest(TestCase):
    def test_launcher_runtime_copies_camera_state_and_fails_closed(self) -> None:
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
            "MediapipeCameraName",
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

        self.assertIn('<select id="MediapipeCameraName"></select>', html)
        self.assertIn('id="refresh-camera-devices"', html)
        self.assertIn('id="MediapipeCameraNameManual"', html)
        self.assertIn('maxlength="256"', html)
        self.assertIn('id="apply-manual-camera"', html)
        self.assertNotIn('id="MediapipeCameraName" type="text"', html)
        self.assertIn("const refreshVideoInputDevices = async () =>", app)
        self.assertIn("api('/api/video-input-devices')", app)
        self.assertIn("selected && !selectedMatch", app)
        self.assertIn("launch.cameraSelectionMissing", app)
        self.assertIn("setOption('MediapipeCameraName', event.target.value)", app)
        self.assertIn("setOption('MediapipeCameraName', value)", app)
        self.assertIn("const normalizeCameraSelection = (value) =>", app)
        self.assertNotIn("'MediapipeCameraName',\n  'MediapipeCameraInputCodec'", app)
        self.assertIn("-list_devices", server)
        self.assertIn("\\(video\\)", server)
        self.assertIn("device_start_count: 0", server)
        self.assertIn("capture_count: 0", server)
        self.assertIn("video_input_enumeration_unavailable", server)
        self.assertIn("normalized.MediapipeCameraName = sanitizeVideoInputDeviceName", server)

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
                    str(ROOT.parents[1]),
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

                save_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": "camera-a"},
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

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/state", timeout=5
                ) as response:
                    reloaded_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    reloaded_state["config"]["options"]["MediapipeCameraName"],
                    "camera-a",
                )

                video_input_fixture.write_text(
                    json.dumps(["camera-b"]), encoding="utf-8"
                )
                with urllib.request.urlopen(url, timeout=5) as response:
                    missing_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(missing_payload["selection_class"], "selected_missing")
                self.assertFalse(missing_payload["selected_match"])
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{launcher_port}/api/state", timeout=5
                ) as response:
                    missing_state = json.loads(response.read().decode("utf-8"))
                self.assertEqual(
                    missing_state["config"]["options"]["MediapipeCameraName"],
                    "camera-a",
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
                    {"profileId": "thought-core-v0", "useSavedOptions": True}
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
                self.assertIn("<local-camera-selection>", public_preview["commandLine"])

                invalid_body = json.dumps(
                    {
                        "profileId": "thought-core-v0",
                        "options": {"MediapipeCameraName": "camera\ncontrol"},
                    }
                ).encode("utf-8")
                invalid_request = urllib.request.Request(
                    f"http://127.0.0.1:{launcher_port}/api/test/camera-command-boundary",
                    data=invalid_body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(invalid_request, timeout=5) as response:
                    invalid_boundary = json.loads(response.read().decode("utf-8"))
                self.assertFalse(invalid_boundary["input_accepted"])
                self.assertTrue(invalid_boundary["execution_argv_exact"])
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
                self.assertIn(
                    "<local-camera-selection>",
                    state_payload["launcherState"]["commandLine"],
                )
                self.assertIn("<local-camera-selection>", state_payload["logTail"])

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
                self.assertIn("<local-camera-selection>", logs["logTail"])
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

        self.assertNotIn("compatibility-switch-grid", html)
        self.assertNotIn("runtime-drawer-summary", html)
        self.assertNotIn("renderSwitchGroup('compatibility-switch-grid'", app)
        self.assertNotIn("Legacy paths active", app)
        self.assertNotIn("Legacy paths off", app)

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
        html = read_public("index.html")
        app = read_public("app.js")

        self.assertIn("collectStackStopVerification", server)
        self.assertIn("waitForStackStopVerification", server)
        self.assertIn("stopVerification", server)
        self.assertIn("managed ports still listening", server)
        self.assertIn("recorded processes still alive", server)
        self.assertIn("formatStopVerificationDetail", app)
        self.assertIn("Stop verified", app)
        self.assertIn("Stop incomplete", app)
        self.assertIn("Stop Launcher Only", html)
        self.assertIn("Stop Launcher Only", app)

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
        self.assertIn("checkTcpIf(mediapipeEnabled, options.MediapipePort)", server)
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
        self.assertIn("-ThoughtCoreNoProvider", system)
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
        self.assertTrue(thought_core["EnableThoughtCoreWatch"])

        demo_fast = profiles["demo-fast"]["options"]
        self.assertFalse(demo_fast["StopExisting"])
        self.assertTrue(demo_fast["EnableThoughtCore"])
        self.assertFalse(demo_fast["EnableThoughtCoreWatch"])
        self.assertTrue(demo_fast["ThoughtCoreNoProvider"])
        self.assertEqual(demo_fast["VoicevoxReadyTimeoutSeconds"], 8)
        self.assertTrue(demo_fast["SkipHomeAssistantBridge"])
        self.assertTrue(demo_fast["SkipEnvironmentState"])
        self.assertTrue(demo_fast["SkipMediapipe"])
        self.assertTrue(demo_fast["SkipVisionSnapshotProcessor"])
        self.assertTrue(demo_fast["SkipTouchDesignerGui"])

        demo_fast_action = profiles["demo-fast-action"]["options"]
        self.assertFalse(demo_fast_action["StopExisting"])
        self.assertTrue(demo_fast_action["EnableThoughtCore"])
        self.assertFalse(demo_fast_action["EnableThoughtCoreWatch"])
        self.assertTrue(demo_fast_action["ThoughtCoreNoProvider"])
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

    def test_launcher_passes_readiness_timeouts_to_stack_scripts(self) -> None:
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
        self.assertIn("addSupportedParam", server)
        self.assertIn("numericOptionFields", app)
        self.assertIn("readyTimeoutOptionFields", app)
        self.assertIn("setReadyTimeoutOption", app)
        self.assertIn("[int]$VoicevoxReadyTimeoutSeconds = 45", system)
        self.assertIn("[int]$MediapipeReadyTimeoutSeconds = 90", system)
        self.assertIn("-VoicevoxReadyTimeoutSeconds", system)
        self.assertIn("-MediapipeReadyTimeoutSeconds", system)
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
