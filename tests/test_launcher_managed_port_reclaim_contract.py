from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_SERVER = ROOT / "tools" / "home-control-launcher" / "server.js"
STACK_START = ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
STACK_STOP = ROOT / "ops" / "scripts" / "home-control-stack" / "stop-home-control-stack.ps1"
NODE = shutil.which("node")
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("bounded fixture condition was not reached before timeout")


LISTENER_SOURCE = """
const net = require('net')
const port = Number(process.argv[process.argv.indexOf('--port') + 1])
const server = net.createServer((socket) => socket.end())
server.listen(port, '127.0.0.1')
const stop = () => server.close(() => process.exit(0))
process.once('SIGTERM', stop)
process.once('SIGINT', stop)
"""


class LauncherFixture:
    def __init__(self, environment: dict[str, str] | None = None) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sword-launcher-port-contract-")
        self.root = Path(self.temp_dir.name)
        self.workspace = self.root / "workspace"
        self.foreign_root = self.root / "foreign"
        self.state_dir = self.root / "state"
        self.launcher_port = unused_loopback_port()
        self.launcher: subprocess.Popen[str] | None = None
        self.children: list[subprocess.Popen[str]] = []
        self.environment = environment or {}

    def __enter__(self) -> "LauncherFixture":
        self.workspace.mkdir()
        self.foreign_root.mkdir()
        environment = {
            "NODE_ENV": "test",
            "HOME_CONTROL_LAUNCHER_STOP_VERIFY_TIMEOUT_MS": "250",
            "HOME_CONTROL_LAUNCHER_STOP_VERIFY_INTERVAL_MS": "50",
            **self.environment,
        }
        self.launcher = subprocess.Popen(
            [
                NODE,
                str(LAUNCHER_SERVER),
                "--workspace",
                str(self.workspace),
                "--state-dir",
                str(self.state_dir),
                "--port",
                str(self.launcher_port),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **environment},
        )
        wait_for(lambda: port_is_listening(self.launcher_port))
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        for child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
        if self.launcher is not None and self.launcher.poll() is None:
            self.launcher.terminate()
            try:
                self.launcher.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.launcher.kill()
                self.launcher.wait(timeout=3)
        self.temp_dir.cleanup()

    def listener(self, *, managed: bool, executable: str | None = None) -> tuple[subprocess.Popen[str], int]:
        port = unused_loopback_port()
        script_root = self.workspace if managed else self.foreign_root
        script = script_root / "server.js"
        script.write_text(LISTENER_SOURCE, encoding="utf-8")
        process = subprocess.Popen(
            [
                executable or NODE,
                str(script),
                "--workspace",
                str(script_root),
                "--port",
                str(port),
                "--touchdesigner-port",
                "1",
                "--thought-core-port",
                "1",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.children.append(process)
        wait_for(lambda: port_is_listening(port))
        return process, port

    def write_pid_state(self, entry: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "pids.json").write_text(
            json.dumps({"processes": [entry]}), encoding="utf-8"
        )

    def post(self, route: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        response = request.urlopen(
            request.Request(
                f"http://127.0.0.1:{self.launcher_port}{route}",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=10,
        )
        return json.loads(response.read().decode("utf-8"))

    def configure_touchdesigner_target(self, port: int | None = None) -> None:
        options = {
            "SkipVoicevoxCheck": True,
            "SkipHomeAssistantBridge": True,
            "SkipEnvironmentState": True,
            "SkipMediapipe": True,
            "SkipVisionSnapshotProcessor": True,
            "SkipAituber": True,
            "SkipTouchDesignerGui": port is None,
            "EnableThoughtCore": False,
            "EnableThoughtCoreWatch": False,
        }
        if port is not None:
            options["TouchDesignerGuiPort"] = port
        saved = self.post("/api/save-config", {"profileId": "thought-core-v0", "options": options})
        assert saved["ok"] is True

    def partial_start_workspace(self, port: int) -> Path:
        thought_core = self.workspace / "control-plane" / "core"
        scripts = thought_core / "scripts"
        scripts.mkdir(parents=True)
        home_control_config = self.workspace / "organs" / "action" / "home-assistant-server" / "config"
        home_control_config.mkdir(parents=True)
        (home_control_config / "home-control.yaml").write_text("actions: []\n", encoding="utf-8")
        for relative_path in (
            "organs/environment/environment-state-server",
            "organs/environment/vision-snapshot-processor",
            "organs/expression/aituber-kit",
            "organs/reflex/mediapipe-sword-sign",
            "organs/display/touchdesigner-ai-controller",
            "organs/speech-input/ai-talk-core",
        ):
            (self.workspace / relative_path).mkdir(parents=True, exist_ok=True)
        (thought_core / "services" / "thought-core").mkdir(parents=True)
        (thought_core / ".env").write_text("THOUGHT_CORE_LLM_MODE=off\n", encoding="utf-8")
        (scripts / "start-thought-core.ps1").write_text(
            """param([string]$HostName, [int]$Port, [string]$StatusDir)
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
$listener.Start()
try { while ($true) { Start-Sleep -Milliseconds 100 } }
finally { $listener.Stop() }
""",
            encoding="utf-8",
        )
        (scripts / "start-thought-core-watch.ps1").write_text(
            "Start-Sleep -Milliseconds 150\n"
            "$markerPath = Join-Path $PSScriptRoot 'watcher-ran-and-failed.txt'\n"
            "Set-Content -LiteralPath $markerPath -Value 'fixture watcher executed; exit=41' -NoNewline\n"
            "exit 41\n",
            encoding="utf-8",
        )
        return thought_core

    def start_partial_stack(self, *, port: int, cleanup_failure: bool) -> subprocess.CompletedProcess[str]:
        thought_core = self.partial_start_workspace(port)
        environment = {**os.environ, "NODE_ENV": "test"}
        if cleanup_failure:
            environment["HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE"] = "true"
        return subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(STACK_START),
                "-WorkspaceRoot",
                str(self.workspace),
                "-StackStateDir",
                str(self.state_dir),
                "-ThoughtCoreRoot",
                str(thought_core),
                "-ThoughtCorePort",
                str(port),
                "-SkipVoicevoxCheck",
                "-SkipHomeAssistantBridge",
                "-SkipEnvironmentState",
                "-SkipMediapipe",
                "-SkipVisionSnapshotProcessor",
                "-SkipAituber",
                "-SkipTouchDesignerGui",
                "-EnableThoughtCore",
                "-EnableThoughtCoreWatch",
                "-ThoughtCoreNoProvider",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            env=environment,
        )

    def stop_partial_stack(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(STACK_STOP),
                "-WorkspaceRoot",
                str(self.workspace),
                "-StackStateDir",
                str(self.state_dir),
                "-Force",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, "NODE_ENV": "test"},
        )


@unittest.skipUnless(NODE and POWERSHELL, "Node and PowerShell are required for launcher port contracts")
class LauncherManagedPortReclaimContractTest(unittest.TestCase):
    def test_reclaim_stops_only_test_owned_managed_listener_and_releases_port(self) -> None:
        with LauncherFixture() as fixture:
            child, port = fixture.listener(managed=True)
            fixture.configure_touchdesigner_target(port)

            payload = fixture.post("/api/reclaim-managed-ports", {})

            self.assertTrue(payload["ok"])
            reclaim = payload["managedPortReclaim"]
            self.assertEqual(reclaim["attempted"], 1)
            self.assertEqual([entry["pid"] for entry in reclaim["reclaimed"]], [child.pid])
            wait_for(lambda: child.poll() is not None)
            wait_for(lambda: not port_is_listening(port))

    def test_reclaim_refuses_unmanaged_listener_until_fixture_cleanup(self) -> None:
        with LauncherFixture() as fixture:
            child, port = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target(port)

            payload = fixture.post("/api/reclaim-managed-ports", {})

            self.assertTrue(payload["ok"])
            reclaim = payload["managedPortReclaim"]
            self.assertEqual(reclaim["reclaimed"], [])
            self.assertGreaterEqual(reclaim["skippedOwners"], 1)
            self.assertIsNone(child.poll())
            self.assertTrue(port_is_listening(port))

    def test_stop_refuses_unmanaged_listener_on_configured_managed_port(self) -> None:
        with LauncherFixture() as fixture:
            child, port = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target(port)

            payload = fixture.post("/api/stop", {})

            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["stopVerification"]["openPorts"],
                [
                    {
                        "key": "touchdesigner_control_gui",
                        "label": "Display runtime GUI",
                        "host": "127.0.0.1",
                        "port": port,
                        "detail": "listen",
                    }
                ],
            )
            self.assertIn("managed ports still listening", payload["message"])
            self.assertIsNone(child.poll())
            self.assertTrue(port_is_listening(port))

    def test_stop_treats_stale_pid_metadata_as_stale_without_touching_fixture_process(self) -> None:
        with LauncherFixture() as fixture:
            child, _ = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target()
            fixture.write_pid_state(
                {
                    "name": "stale-fixture",
                    "pid": child.pid,
                    "started_at": "2000-01-01T00:00:00+00:00",
                    "allowed_process_names": ["node"],
                }
            )

            payload = fixture.post("/api/stop", {})

            self.assertTrue(payload["ok"])
            self.assertIsNone(child.poll())
            self.assertEqual(payload["stopVerification"]["aliveRecorded"], [])
            self.assertEqual(
                payload["stopVerification"]["staleRecorded"],
                ["stale_or_unowned_registry_entry"],
            )

    def test_stop_rejects_same_name_pid_with_registry_timestamp_thirty_seconds_too_old(self) -> None:
        with LauncherFixture() as fixture:
            child, _ = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target()
            fixture.write_pid_state(
                {
                    "name": "same-name-pid-reuse-fixture",
                    "pid": child.pid,
                    "started_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
                    "allowed_process_names": ["node"],
                }
            )

            payload = fixture.post("/api/stop", {})

            self.assertTrue(payload["ok"])
            self.assertIsNone(child.poll())
            self.assertEqual(
                payload["stopVerification"]["staleRecorded"],
                ["stale_or_unowned_registry_entry"],
            )

    def test_stop_treats_missing_timestamp_and_empty_allowlist_as_unowned(self) -> None:
        for entry in (
            {"name": "missing-timestamp", "allowed_process_names": ["node"]},
            {
                "name": "empty-allowlist",
                "started_at": "2026-01-01T00:00:00+00:00",
                "allowed_process_names": [],
            },
        ):
            with self.subTest(entry=entry["name"]), LauncherFixture() as fixture:
                child, _ = fixture.listener(managed=False)
                fixture.configure_touchdesigner_target()
                fixture.write_pid_state({**entry, "pid": child.pid})

                payload = fixture.post("/api/stop", {})

                self.assertTrue(payload["ok"])
                self.assertIsNone(child.poll())
                self.assertEqual(
                    payload["stopVerification"]["staleRecorded"],
                    ["stale_or_unowned_registry_entry"],
                )

    def test_stop_carries_inspection_failure_past_pid_registry_deletion(self) -> None:
        with LauncherFixture(
            {"HOME_CONTROL_LAUNCHER_TEST_INSPECTION_FAILURE": "true"}
        ) as fixture:
            child, _ = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target()
            fixture.write_pid_state(
                {
                    "name": "inspection-failure-fixture",
                    "pid": child.pid,
                    "allowed_process_names": ["node"],
                }
            )

            payload = fixture.post("/api/stop", {})

            self.assertFalse(payload["ok"])
            self.assertFalse(payload["stopVerification"]["pidFileExists"])
            self.assertEqual(
                payload["stopVerification"]["aliveRecorded"],
                [
                    {
                        "name": "unverified_registry_entry",
                        "module": "",
                        "role": "",
                        "pid": child.pid,
                    }
                ],
            )
            self.assertIsNone(child.poll())

    def test_stop_revalidation_identity_change_leaves_fixture_child_alive(self) -> None:
        with LauncherFixture(
            {"HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE": "true"}
        ) as fixture:
            child, _ = fixture.listener(managed=False)
            fixture.configure_touchdesigner_target()
            fixture.write_pid_state(
                {
                    "name": "identity-change-fixture",
                    "pid": child.pid,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "allowed_process_names": ["node"],
                }
            )

            payload = fixture.post("/api/stop", {})

            self.assertFalse(payload["ok"])
            self.assertTrue(payload["stopVerification"]["pidFileExists"])
            self.assertEqual(
                payload["stopVerification"]["aliveRecorded"][0]["pid"], child.pid)
            self.assertIsNone(child.poll())

    def test_stop_refuses_external_deny_list_fixture_with_plausible_timestamp(self) -> None:
        with LauncherFixture() as fixture:
            chrome_fixture = fixture.root / "chrome.exe"
            shutil.copyfile(NODE, chrome_fixture)
            child, _ = fixture.listener(managed=False, executable=str(chrome_fixture))
            fixture.configure_touchdesigner_target()
            fixture.write_pid_state(
                {
                    "name": "external-deny-fixture",
                    "pid": child.pid,
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                    "allowed_process_names": ["chrome"],
                }
            )

            payload = fixture.post("/api/stop", {})

            self.assertTrue(payload["ok"])
            self.assertIsNone(child.poll())
            self.assertEqual(
                payload["stopVerification"]["staleRecorded"],
                ["stale_or_unowned_registry_entry"],
            )

    def test_stop_script_revalidates_identity_before_termination(self) -> None:
        script = (ROOT / "ops" / "scripts" / "home-control-stack" / "stop-home-control-stack.ps1").read_text(
            encoding="utf-8"
        )

        revalidation = script.index("registry root identity changed before termination")
        terminate = script.index("Stop-Process -Id $pidValue -Force")
        self.assertLess(revalidation, terminate)
        self.assertIn("Test-ProcessStartTimeMatches -Process $rootProcess", script)
        self.assertIn("Test-ProcessNameAllowed -ProcessName ([string]$rootProcess.ProcessName)", script)

    def test_partial_start_failure_runs_real_cleanup_and_removes_fixture_child_port_and_registry(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()

            result = fixture.start_partial_stack(port=port, cleanup_failure=False)

            watcher_marker = (
                fixture.workspace
                / "control-plane"
                / "core"
                / "scripts"
                / "watcher-ran-and-failed.txt"
            )
            self.assertNotEqual(
                result.returncode,
                0,
                f"watcher marker: {watcher_marker.read_text(encoding='utf-8') if watcher_marker.exists() else 'missing'}",
            )
            self.assertTrue(watcher_marker.exists())
            self.assertEqual(
                watcher_marker.read_text(encoding="utf-8"),
                "fixture watcher executed; exit=41",
            )
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertFalse(port_is_listening(port))
            self.assertIsNone(unrelated.poll())

    def test_partial_start_cleanup_failure_retains_registry_and_never_stops_unrelated_fixture_process(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()

            result = fixture.start_partial_stack(port=port, cleanup_failure=True)

            pid_state_path = fixture.state_dir / "pids.json"
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(pid_state_path.exists())
            recorded_pid = json.loads(pid_state_path.read_text(encoding="utf-8"))["processes"][0]["pid"]
            self.assertIsNotNone(recorded_pid)
            self.assertTrue(port_is_listening(port))
            self.assertIsNone(unrelated.poll())

            cleanup = fixture.stop_partial_stack()

            self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: not port_is_listening(port))
            self.assertFalse(pid_state_path.exists())
            self.assertIsNone(unrelated.poll())

    def test_stop_is_idempotent_when_no_test_owned_stack_residue_exists(self) -> None:
        with LauncherFixture() as fixture:
            fixture.configure_touchdesigner_target()

            first = fixture.post("/api/stop", {})
            second = fixture.post("/api/stop", {})

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(second["stopVerification"]["openPorts"], [])


if __name__ == "__main__":
    unittest.main()
