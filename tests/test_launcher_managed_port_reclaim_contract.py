from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
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
        self.vsp_fixture_pids: set[int] = set()
        self.vsp_fixture_started = False
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

    def _quiesce_vsp_fixture_workers(self) -> bool:
        try:
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    (
                        "$marker='--fixture-owner " + self.root.name + "';"
                        "$deadline=[DateTime]::UtcNow.AddSeconds(6);"
                        "$zeroPasses=0;"
                        "do {"
                        "$owned=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.Name -eq 'python.exe' -and [string]$_.CommandLine -like \"*$marker*\" });"
                        "foreach($item in $owned){Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue};"
                        "Start-Sleep -Milliseconds 100;"
                        "$remaining=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.Name -eq 'python.exe' -and [string]$_.CommandLine -like \"*$marker*\" });"
                        "if($remaining.Count -eq 0){$zeroPasses+=1}else{$zeroPasses=0};"
                        "if($zeroPasses -ge 2){exit 0}"
                        "} while([DateTime]::UtcNow -lt $deadline);"
                        "exit 1"
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def __exit__(self, exc_type, exc, traceback) -> None:
        record = self.read_vsp_listener_record()
        if record is not None:
            self.vsp_fixture_pids.add(int(record.get("pid", 0)))
            self.vsp_fixture_pids.update(
                int(row.get("pid", 0)) for row in record.get("ownership_lineage", [])
            )
        for owned_pid in sorted((pid for pid in self.vsp_fixture_pids if pid > 0), reverse=True):
            subprocess.run(
                [
                    POWERSHELL,
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {owned_pid} -Force -ErrorAction SilentlyContinue",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            alive = subprocess.run(
                [
                    POWERSHELL,
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    "exit [int](@(Get-Process -Id "
                    + ",".join(str(pid) for pid in self.vsp_fixture_pids if pid > 0)
                    + " -ErrorAction SilentlyContinue).Count -gt 0)",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            if alive == 0:
                break
            time.sleep(0.05)
        for child in reversed(self.children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()
        time.sleep(0.3)
        if self.launcher is not None and self.launcher.poll() is None:
            self.launcher.terminate()
            try:
                self.launcher.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.launcher.kill()
                self.launcher.wait(timeout=3)
        fixture_workers_stopped = (
            self._quiesce_vsp_fixture_workers() if self.vsp_fixture_started else True
        )
        temp_cleanup_succeeded = True
        try:
            self.temp_dir.cleanup()
        except OSError:
            temp_cleanup_succeeded = False
        if not fixture_workers_stopped or not temp_cleanup_succeeded or self.root.exists():
            raise AssertionError("fixture_cleanup_incomplete")

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
            timeout=20,
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

    def configure_vsp_target(self, port: int) -> None:
        saved = self.post(
            "/api/save-config",
            {
                "profileId": "thought-core-v0",
                "options": {
                    "SkipVoicevoxCheck": True,
                    "SkipHomeAssistantBridge": True,
                    "SkipEnvironmentState": True,
                    "SkipMediapipe": False,
                    "SkipVisionSnapshotProcessor": False,
                    "SkipAituber": True,
                    "SkipTouchDesignerGui": True,
                    "EnableThoughtCore": False,
                    "EnableThoughtCoreWatch": False,
                    "VisionSnapshotProcessorPort": port,
                },
            },
        )
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

    def stop_partial_stack(
        self, extra_environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
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
            env={**os.environ, "NODE_ENV": "test", **(extra_environment or {})},
        )

    def start_vsp_orphan_fixture(self, *, port: int) -> subprocess.Popen[str]:
        self.vsp_fixture_started = True
        for relative_path in (
            "organs/action/home-assistant-server/config",
            "organs/environment/environment-state-server",
            "organs/environment/vision-snapshot-processor/src/vision_snapshot_processor",
            "organs/expression/aituber-kit",
            "organs/reflex/mediapipe-sword-sign",
            "organs/display/touchdesigner-ai-controller",
            "organs/speech-input/ai-talk-core",
            "control-plane/core",
        ):
            (self.workspace / relative_path).mkdir(parents=True, exist_ok=True)
        (self.workspace / "organs/action/home-assistant-server/config/home-control.yaml").write_text(
            "actions: []\n", encoding="utf-8"
        )
        package = (
            self.workspace
            / "organs/environment/vision-snapshot-processor/src/vision_snapshot_processor"
        )
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "main.py").write_text(
            """from __future__ import annotations
import os
import socket
import subprocess
import sys
import time

args = sys.argv[1:]
port = int(args[args.index('--port') + 1])
if '--fixture-worker' not in args:
    subprocess.Popen(
        [sys.executable, '-m', 'vision_snapshot_processor.main', *args, '--fixture-worker'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    time.sleep(2.0)
    raise SystemExit(0)

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', port))
listener.listen()
try:
    while True:
        connection, _ = listener.accept()
        connection.close()
finally:
    listener.close()
""",
            encoding="utf-8",
        )
        tool_dir = self.root / "tools"
        tool_dir.mkdir()
        (tool_dir / "uv.cmd").write_text(
            (
                f'@echo off\r\n"{sys.executable}" -m vision_snapshot_processor.main '
                f'--host 127.0.0.1 --port %8 --fixture-owner {self.root.name}\r\n'
            ),
            encoding="utf-8",
        )
        environment = {
            **os.environ,
            "NODE_ENV": "test",
            "HOME_CONTROL_STACK_TEST_LAUNCH_VSP_WITHOUT_MEDIAPIPE": "true",
            "PATH": f"{tool_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(package.parent),
        }
        process = subprocess.Popen(
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
                "-VisionSnapshotProcessorPort",
                str(port),
                "-SkipVoicevoxCheck",
                "-SkipHomeAssistantBridge",
                "-SkipEnvironmentState",
                "-SkipMediapipe",
                "-SkipAituber",
                "-SkipTouchDesignerGui",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        self.children.append(process)
        return process

    def read_vsp_listener_record(self) -> dict | None:
        pid_state_path = self.state_dir / "pids.json"
        if not pid_state_path.exists():
            return None
        try:
            state = json.loads(pid_state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        record = next(
            (
                entry
                for entry in state.get("processes", [])
                if entry.get("ownership_class") == "vsp_descendant_listener.v0"
            ),
            None,
        )
        if record is not None:
            self.vsp_fixture_pids.add(int(record.get("pid", 0)))
            self.vsp_fixture_pids.update(
                int(row.get("pid", 0)) for row in record.get("ownership_lineage", [])
            )
        return record

    def replace_vsp_listener_record(self, transform) -> dict:
        pid_state_path = self.state_dir / "pids.json"
        state = json.loads(pid_state_path.read_text(encoding="utf-8"))
        for index, entry in enumerate(state.get("processes", [])):
            if entry.get("ownership_class") == "vsp_descendant_listener.v0":
                state["processes"][index] = transform(dict(entry))
                pid_state_path.write_text(json.dumps(state), encoding="utf-8")
                return state["processes"][index]
        raise AssertionError("VSP listener record missing")


@unittest.skipUnless(NODE and POWERSHELL, "Node and PowerShell are required for launcher port contracts")
class LauncherManagedPortReclaimContractTest(unittest.TestCase):
    def test_fixture_quiescence_timeout_uses_fixed_failure_and_preserves_processes(self) -> None:
        fixture = LauncherFixture()
        fixture.__enter__()
        fixture.vsp_fixture_started = True
        owned = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(30)",
                "--fixture-owner",
                fixture.root.name,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            with patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired("fixture-quiescence", 8),
            ):
                self.assertFalse(fixture._quiesce_vsp_fixture_workers())
            with patch.object(fixture, "_quiesce_vsp_fixture_workers", return_value=False):
                with self.assertRaisesRegex(AssertionError, "^fixture_cleanup_incomplete$"):
                    fixture.__exit__(None, None, None)
            self.assertIsNone(owned.poll())
            self.assertIsNone(unrelated.poll())
        finally:
            for process in (owned, unrelated):
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)

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
        terminate = script.index("Stop-Process -Id $pidValue -Force", revalidation)
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
            if port_is_listening(port):
                subprocess.run(
                    [
                        POWERSHELL,
                        "-NoLogo",
                        "-NoProfile",
                        "-Command",
                        f"Stop-Process -Id {record['pid']} -Force -ErrorAction SilentlyContinue",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.fail(f"VSP listener survived direct stop\n{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: not port_is_listening(port))
            self.assertFalse(pid_state_path.exists())
            self.assertIsNone(unrelated.poll())

    def test_vsp_listener_record_survives_wrapper_exit_and_direct_stop_cleans_owned_listener(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()
            supervisor = fixture.start_vsp_orphan_fixture(port=port)
            pid_state_path = fixture.state_dir / "pids.json"

            wait_for(
                lambda: fixture.read_vsp_listener_record() is not None or supervisor.poll() is not None,
                timeout=20,
            )
            if supervisor.poll() is not None and fixture.read_vsp_listener_record() is None:
                stdout, stderr = supervisor.communicate(timeout=2)
                self.fail(f"VSP fixture start exited={supervisor.returncode}\n{stdout}\n{stderr}")
            wait_for(lambda: port_is_listening(port), timeout=5)
            record = fixture.read_vsp_listener_record()
            self.assertIsNotNone(record)
            self.assertEqual(record["role"], "vision_snapshot_processor_listener")
            self.assertEqual(record["expected_module"], "vision_snapshot_processor.main")
            self.assertEqual(record["expected_port"], port)
            self.assertEqual(record["allowed_process_names"], ["python"])
            self.assertGreaterEqual(len(record["ownership_lineage"]), 1)
            self.assertRegex(record["ownership_seal"], r"^[0-9a-f]{64}$")

            root_pid = record["ownership_root_pid"]
            wait_for(
                lambda: subprocess.run(
                    [
                        POWERSHELL,
                        "-NoLogo",
                        "-NoProfile",
                        "-Command",
                        f"exit [int]($null -ne (Get-Process -Id {root_pid} -ErrorAction SilentlyContinue))",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0,
                timeout=8,
            )

            cleanup = fixture.stop_partial_stack()

            self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: not port_is_listening(port))
            self.assertFalse(pid_state_path.exists())
            self.assertIsNone(unrelated.poll())
            wait_for(lambda: supervisor.poll() is not None)
            supervisor.communicate(timeout=2)

    def test_launcher_reclaim_uses_recorded_vsp_listener_after_wrapper_exit(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()
            supervisor = fixture.start_vsp_orphan_fixture(port=port)
            wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
            record = fixture.read_vsp_listener_record()
            wait_for(
                lambda: subprocess.run(
                    [
                        POWERSHELL,
                        "-NoLogo",
                        "-NoProfile",
                        "-Command",
                        f"exit [int]($null -ne (Get-Process -Id {record['ownership_root_pid']} -ErrorAction SilentlyContinue))",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0,
                timeout=8,
            )
            fixture.configure_vsp_target(port)

            payload = fixture.post("/api/reclaim-managed-ports", {})

            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["managedPortReclaim"]["attempted"],
                1,
                payload["managedPortReclaim"],
            )
            self.assertEqual(
                [row["pid"] for row in payload["managedPortReclaim"]["reclaimed"]],
                [record["pid"]],
            )
            wait_for(lambda: not port_is_listening(port))
            self.assertIsNone(unrelated.poll())
            cleanup = fixture.stop_partial_stack()
            self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: supervisor.poll() is not None)
            supervisor.communicate(timeout=2)

    def test_vsp_stop_refuses_identity_and_lineage_mutations_and_retains_registry(self) -> None:
        mutations = {
            "pid_reuse_start_time": lambda entry: {
                **entry,
                "started_at": "2000-01-01T00:00:00+00:00",
            },
            "changed_parent": lambda entry: {
                **entry,
                "ownership_parent_pid": entry["ownership_parent_pid"] + 1,
            },
            "changed_module": lambda entry: {
                **entry,
                "expected_module": "other.module",
            },
            "changed_port": lambda entry: {
                **entry,
                "expected_port": entry["expected_port"] + 1,
            },
            "missing_lineage": lambda entry: {
                **entry,
                "ownership_lineage": [],
            },
            "changed_lineage": lambda entry: {
                **entry,
                "ownership_lineage": [
                    {**entry["ownership_lineage"][0], "process_name": "other"},
                    *entry["ownership_lineage"][1:],
                ],
            },
            "changed_seal": lambda entry: {
                **entry,
                "ownership_seal": "0" * 64,
            },
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), LauncherFixture() as fixture:
                unrelated, _ = fixture.listener(managed=False)
                port = unused_loopback_port()
                fixture.start_vsp_orphan_fixture(port=port)
                wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
                fixture.replace_vsp_listener_record(mutation)

                cleanup = fixture.stop_partial_stack()

                self.assertNotEqual(cleanup.returncode, 0)
                self.assertTrue((fixture.state_dir / "pids.json").exists())
                self.assertTrue(port_is_listening(port))
                self.assertIsNone(unrelated.poll())

    def test_vsp_stop_inspection_failure_is_fail_closed(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()
            fixture.start_vsp_orphan_fixture(port=port)
            wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)

            cleanup = fixture.stop_partial_stack(
                {"HOME_CONTROL_STACK_STOP_TEST_VSP_INSPECTION_FAILURE": "true"}
            )

            self.assertNotEqual(cleanup.returncode, 0)
            self.assertTrue((fixture.state_dir / "pids.json").exists())
            self.assertTrue(port_is_listening(port))
            self.assertIsNone(unrelated.poll())

    def test_launcher_reclaim_refuses_same_module_listener_with_changed_record(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()
            fixture.start_vsp_orphan_fixture(port=port)
            wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
            fixture.replace_vsp_listener_record(
                lambda entry: {**entry, "ownership_parent_pid": entry["ownership_parent_pid"] + 1}
            )
            fixture.configure_vsp_target(port)

            payload = fixture.post("/api/reclaim-managed-ports", {})

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["managedPortReclaim"]["attempted"], 0)
            self.assertGreaterEqual(payload["managedPortReclaim"]["skippedOwners"], 1)
            self.assertTrue(port_is_listening(port))
            self.assertIsNone(unrelated.poll())

    def test_launcher_reclaim_refuses_same_module_listener_without_child_record(self) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()
            fixture.start_vsp_orphan_fixture(port=port)
            wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
            state_path = fixture.state_dir / "pids.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["processes"] = [
                entry
                for entry in state["processes"]
                if entry.get("ownership_class") != "vsp_descendant_listener.v0"
            ]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            fixture.configure_vsp_target(port)

            payload = fixture.post("/api/reclaim-managed-ports", {})

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["managedPortReclaim"]["attempted"], 0)
            self.assertGreaterEqual(payload["managedPortReclaim"]["skippedOwners"], 1)
            self.assertTrue(port_is_listening(port))
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
