from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
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


def process_is_alive(pid: int) -> bool:
    return (
        subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"exit [int]($null -eq (Get-Process -Id {pid} -ErrorAction SilentlyContinue))",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).returncode
        == 0
    )


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
        ambient_environment = os.environ.copy()
        ambient_environment.pop("HOME_CONTROL_STACK_STOP_TEST_SETTLE_TIMEOUT_MS", None)
        self.test_environment = {
            **ambient_environment,
            "NODE_ENV": "test",
            "HOME_CONTROL_LAUNCHER_STOP_VERIFY_TIMEOUT_MS": "250",
            "HOME_CONTROL_LAUNCHER_STOP_VERIFY_INTERVAL_MS": "50",
            "HOME_CONTROL_STACK_STOP_TEST_SETTLE_TIMEOUT_MS": "250",
            **self.environment,
        }

    def __enter__(self) -> "LauncherFixture":
        self.workspace.mkdir()
        self.foreign_root.mkdir()
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
            env=self.test_environment,
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

    def write_pid_state(self, entry: dict, *, schema_version: int | None = None) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {"processes": [entry]}
        if schema_version is not None:
            state["schema_version"] = schema_version
        (self.state_dir / "pids.json").write_text(
            json.dumps(state), encoding="utf-8"
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
        thought_package = thought_core / "services" / "thought-core" / "src" / "thought_core"
        thought_package.mkdir(parents=True)
        (thought_package / "__init__.py").write_text("", encoding="utf-8")
        (thought_package / "__main__.py").write_text(
            """import argparse
import os
import socket
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--host')
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
private_env_present = any(
    os.environ.get(name)
    for name in (
        'SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST',
        'SWORD_THOUGHT_CORE_LAUNCH_NONCE',
    )
)
status_path = os.environ.get('HOME_CONTROL_STACK_TEST_THOUGHT_CORE_LISTENER_ENV_STATUS_FILE')
if status_path:
    Path(status_path).write_text(
        'private_env_present' if private_env_present else 'private_env_absent',
        encoding='utf-8',
    )
if private_env_present:
    raise SystemExit(42)
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', args.port))
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
        (thought_core / ".env").write_text("THOUGHT_CORE_LLM_MODE=off\n", encoding="utf-8")
        controller_script = scripts / "thought-core-controller.py"
        controller_script.write_text(
            """import json
import os
from pathlib import Path
import subprocess
import sys

private_env_present = any(
    os.environ.get(name)
    for name in (
        'SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST',
        'SWORD_THOUGHT_CORE_LAUNCH_NONCE',
    )
)
status_path = os.environ.get('HOME_CONTROL_STACK_TEST_THOUGHT_CORE_CONTROLLER_ENV_STATUS_FILE')
if status_path:
    Path(status_path).write_text(
        'private_env_present' if private_env_present else 'private_env_absent',
        encoding='utf-8',
    )
if private_env_present:
    raise SystemExit(43)
child = subprocess.Popen([sys.executable, *sys.argv[1:]])
pid_path = os.environ.get('HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PID_FILE')
if pid_path:
    Path(pid_path).write_text(
        json.dumps({'controller_pid': os.getpid(), 'listener_pid': child.pid}),
        encoding='utf-8',
    )
raise SystemExit(child.wait())
""",
            encoding="utf-8",
        )
        shutil.copy2(ROOT / "scripts" / "common.ps1", scripts / "common.ps1")
        shutil.copy2(ROOT / "scripts" / "load-env.ps1", scripts / "load-env.ps1")
        shutil.copy2(
            ROOT / "scripts" / "start-thought-core.ps1",
            scripts / "start-thought-core.ps1",
        )
        (scripts / "start-thought-core-watch.ps1").write_text(
            "Start-Sleep -Milliseconds 150\n"
            "$markerPath = Join-Path $PSScriptRoot 'watcher-ran-and-failed.txt'\n"
            "Set-Content -LiteralPath $markerPath -Value 'fixture watcher executed; exit=41' -NoNewline\n"
            "exit 41\n",
            encoding="utf-8",
        )
        return thought_core

    def start_partial_stack(
        self,
        *,
        port: int,
        cleanup_failure: bool,
        extra_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        thought_core = self.partial_start_workspace(port)
        environment = self.test_environment.copy()
        case_environment = dict(extra_environment or {})
        controller_python = sys.executable
        if (
            case_environment.get("HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_MUTATION")
            == "write_failure"
        ):
            controller_python = getattr(sys, "_base_executable", sys.executable)
        thought_core_src = thought_core / "services" / "thought-core" / "src"
        environment.update(
            {
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PYTHON": controller_python,
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_CONTROLLER_SCRIPT": str(
                    thought_core / "scripts" / "thought-core-controller.py"
                ),
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PRIVATE_ENV_INJECTION": "true",
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_CONTROLLER_ENV_STATUS_FILE": str(
                    thought_core / "scripts" / "controller-env-status.txt"
                ),
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_LISTENER_ENV_STATUS_FILE": str(
                    thought_core / "scripts" / "listener-env-status.txt"
                ),
                "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PID_FILE": str(
                    thought_core / "scripts" / "controller-pids.json"
                ),
                "PYTHONPATH": str(thought_core_src),
            }
        )
        if cleanup_failure:
            environment["HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE"] = "true"
        environment.update(case_environment)
        command = [
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
            ]
        with tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        ) as stdout_file, tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        ) as stderr_file:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=35,
                env=environment,
            )
            stdout_file.seek(0)
            stderr_file.seek(0)
            return subprocess.CompletedProcess(
                completed.args,
                completed.returncode,
                stdout_file.read(),
                stderr_file.read(),
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
            env={**self.test_environment, **(extra_environment or {})},
        )

    def start_managed_camera_tree_fixture(self, *, port: int) -> tuple[subprocess.Popen[str], list[dict]]:
        self.vsp_fixture_started = True
        fixture_script = self.root / "managed_camera_tree_fixture.py"
        fixture_script.write_text(
            """from __future__ import annotations
import argparse
import socket
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--stage', choices=('root', 'parent', 'listener'), required=True)
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--fixture-owner', required=True)
args = parser.parse_args()
base = [sys.executable, __file__, '--port', str(args.port), '--fixture-owner', args.fixture_owner]
if args.stage == 'root':
    subprocess.Popen([*base, '--stage', 'parent'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(60)
elif args.stage == 'parent':
    subprocess.Popen([*base, '--stage', 'listener'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(60)
else:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', args.port))
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
        root = subprocess.Popen(
            [
                getattr(sys, "_base_executable", sys.executable),
                str(fixture_script),
                "--stage",
                "root",
                "--port",
                str(port),
                "--fixture-owner",
                self.root.name,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(root)
        wait_for(lambda: port_is_listening(port), timeout=8)
        inspect_script = f"""
$rootPid = {root.pid}
$listenerPid = [int](@(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)[0])
$rows = @()
$cursor = $listenerPid
for ($depth = 0; $depth -lt 8 -and $cursor -gt 0; $depth++) {{
  $runtime = Get-Process -Id $cursor -ErrorAction Stop
  $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $cursor" -ErrorAction Stop
  $rows += [pscustomobject]@{{ pid=[int]$cursor; parent_pid=[int]$identity.ParentProcessId; started_at=([DateTimeOffset]$runtime.StartTime).ToString('o') }}
  if ($cursor -eq $rootPid) {{ break }}
  $cursor = [int]$identity.ParentProcessId
}}
$rows | ConvertTo-Json -Depth 4 -Compress
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", inspect_script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            raise AssertionError("managed_camera_tree_fixture_inspection_failed")
        parsed = json.loads(result.stdout)
        lineage = parsed if isinstance(parsed, list) else [parsed]
        if not lineage or int(lineage[-1]["pid"]) != root.pid:
            raise AssertionError("managed_camera_tree_fixture_lineage_incomplete")
        self.vsp_fixture_pids.update(int(row["pid"]) for row in lineage)
        child_process_file = self.state_dir / "modules" / "camera-hub" / "processes.json"
        child_process_file.parent.mkdir(parents=True, exist_ok=True)
        child_process_file.write_text(
            json.dumps({"processes": lineage[:-1]}), encoding="utf-8"
        )
        self.write_pid_state(
            {
                "name": "camera-hub-tree-fixture",
                "pid": root.pid,
                "started_at": lineage[-1]["started_at"],
                "stop_strategy": "managed_tree",
                "allowed_process_names": ["python"],
                "child_process_file": str(child_process_file),
            },
            schema_version=3,
        )
        return root, lineage

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
            **self.test_environment,
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

    def configure_sealed_target(self, service: str, port: int) -> None:
        is_home = service == "home"
        saved = self.post(
            "/api/save-config",
            {
                "profileId": "thought-core-v0",
                "options": {
                    "SkipVoicevoxCheck": True,
                    "SkipHomeAssistantBridge": not is_home,
                    "HomeAssistantBridgePort": port if is_home else unused_loopback_port(),
                    "SkipEnvironmentState": True,
                    "SkipMediapipe": True,
                    "SkipVisionSnapshotProcessor": True,
                    "SkipAituber": True,
                    "SkipTouchDesignerGui": True,
                    "EnableThoughtCore": not is_home,
                    "EnableThoughtCoreWatch": False,
                    "ThoughtCorePort": port if not is_home else unused_loopback_port(),
                },
            },
        )
        assert saved["ok"] is True

    def start_sealed_service_fixture(
        self, service: str, port: int, *, root_exit_seconds: float = 5.0
    ) -> tuple[subprocess.Popen[str], dict]:
        self.vsp_fixture_started = True
        modules = self.root / f"sealed-{service}-modules"
        package_name = "uvicorn" if service == "home" else "thought_core"
        package = modules / package_name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            """import argparse
import socket

parser = argparse.ArgumentParser()
parser.add_argument('app', nargs='?')
parser.add_argument('--host')
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(('127.0.0.1', args.port))
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
        root_source = """import os, subprocess, sys, time
if '--sealed-parent' in sys.argv:
    service = sys.argv[sys.argv.index('--service') + 1]
    port = sys.argv[sys.argv.index('--port') + 1]
    command = [sys.executable, '-m', 'uvicorn', 'home_control_bridge.main:app', '--host', '127.0.0.1', '--port', port] if service == 'home' else [sys.executable, '-m', 'thought_core', '--host', '127.0.0.1', '--port', port]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    time.sleep(30)
else:
    source = sys.argv[sys.argv.index('--source') + 1]
    subprocess.Popen([sys.executable, '-c', source, '--sealed-parent', '--service', sys.argv[sys.argv.index('--service') + 1], '--port', sys.argv[sys.argv.index('--port') + 1]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    time.sleep(__ROOT_EXIT_SECONDS__)
""".replace("__ROOT_EXIT_SECONDS__", repr(root_exit_seconds))
        root = subprocess.Popen(
            [sys.executable, "-c", root_source, "--source", root_source, "--service", service, "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONPATH": str(modules)},
        )
        self.children.append(root)
        wait_for(lambda: port_is_listening(port), timeout=8)
        inspect_script = f"""
$rootPid = {root.pid}
$listenerPid = [int](@(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)[0])
$listener = Get-Process -Id $listenerPid -ErrorAction Stop
$identity = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction Stop
$lineage = @()
$cursor = [int]$identity.ParentProcessId
for ($depth = 0; $depth -lt 8 -and $cursor -gt 0; $depth++) {{
  $runtime = Get-Process -Id $cursor -ErrorAction Stop
  $item = Get-CimInstance Win32_Process -Filter "ProcessId = $cursor" -ErrorAction Stop
  $lineage += [pscustomobject]@{{ pid=[int]$cursor; parent_pid=[int]$item.ParentProcessId; process_name=([string]$runtime.ProcessName).ToLowerInvariant().Replace('.exe',''); started_at=([DateTimeOffset]$runtime.StartTime).ToString('o') }}
  if ($cursor -eq $rootPid) {{ break }}
  $cursor = [int]$item.ParentProcessId
}}
[pscustomobject]@{{ pid=$listenerPid; parent_pid=[int]$identity.ParentProcessId; started_at=([DateTimeOffset]$listener.StartTime).ToString('o'); lineage=$lineage }} | ConvertTo-Json -Depth 6 -Compress
"""
        result = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-Command", inspect_script],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            raise AssertionError(f"sealed fixture inspection failed: {result.stderr}")
        identity = json.loads(result.stdout)
        ownership_class = (
            "home_control_bridge_descendant_listener.v0"
            if service == "home"
            else "thought_core_descendant_listener.v0"
        )
        role = "home_assistant_bridge_listener" if service == "home" else "thought_core_api_listener"
        expected_module = "home_control_bridge.main:app" if service == "home" else "thought_core"
        lineage = identity["lineage"] if isinstance(identity["lineage"], list) else [identity["lineage"]]
        record = {
            "name": role,
            "module": "fixture",
            "role": role,
            "pid": identity["pid"],
            "working_directory": "",
            "command": "",
            "started_at": identity["started_at"],
            "stop_strategy": "role_scoped_descendant",
            "allowed_process_names": ["python"],
            "child_process_file": "",
            "ownership_class": ownership_class,
            "ownership_root_pid": root.pid,
            "ownership_root_started_at": lineage[-1]["started_at"],
            "ownership_parent_pid": identity["parent_pid"],
            "ownership_lineage": lineage,
            "expected_module": expected_module,
            "expected_port": port,
        }
        parts = [
            ownership_class,
            str(record["pid"]),
            record["started_at"],
            str(record["ownership_parent_pid"]),
            str(record["ownership_root_pid"]),
            record["ownership_root_started_at"],
            expected_module,
            str(port),
            *[
                f'{row["pid"]}|{row["parent_pid"]}|{row["process_name"]}|{row["started_at"]}'
                for row in lineage
            ],
        ]
        record["ownership_seal"] = hashlib.sha256("\n".join(parts).encode()).hexdigest()
        self.write_pid_state(record)
        self.vsp_fixture_pids.update([record["pid"], *(row["pid"] for row in lineage)])
        return root, record

    @staticmethod
    def reseal_record(record: dict) -> dict:
        updated = dict(record)
        parts = [
            updated["ownership_class"],
            str(updated["pid"]),
            updated["started_at"],
            str(updated["ownership_parent_pid"]),
            str(updated["ownership_root_pid"]),
            updated["ownership_root_started_at"],
            updated["expected_module"],
            str(updated["expected_port"]),
            *[
                f'{row["pid"]}|{row["parent_pid"]}|{row["process_name"]}|{row["started_at"]}'
                for row in updated["ownership_lineage"]
            ],
        ]
        updated["ownership_seal"] = hashlib.sha256("\n".join(parts).encode()).hexdigest()
        return updated


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

    def test_camera_hub_managed_tree_stops_deepest_descendants_before_root(self) -> None:
        with LauncherFixture(
            {"HOME_CONTROL_STACK_STOP_TEST_ROOT_FIRST_TARGETS": "true"}
        ) as fixture:
            port = unused_loopback_port()
            root, lineage = fixture.start_managed_camera_tree_fixture(port=port)
            unrelated_python = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            fixture.children.append(unrelated_python)

            result = fixture.stop_partial_stack()

            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            for row in lineage:
                wait_for(
                    lambda pid=int(row["pid"]): subprocess.run(
                        [
                            POWERSHELL,
                            "-NoLogo",
                            "-NoProfile",
                            "-Command",
                            f"exit [int]($null -ne (Get-Process -Id {pid} -ErrorAction SilentlyContinue))",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    ).returncode
                    == 0
                )
            root_marker = f"stopped PID {root.pid} "
            listener_marker = f"stopped PID {int(lineage[0]['pid'])} "
            self.assertIn(listener_marker, output, output)
            if root_marker in output:
                self.assertLess(output.index(listener_marker), output.index(root_marker))
            self.assertFalse(port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(unrelated_python.poll())

    def test_camera_hub_managed_tree_retains_registry_when_revalidation_refuses_targets(self) -> None:
        with LauncherFixture() as fixture:
            port = unused_loopback_port()
            root, lineage = fixture.start_managed_camera_tree_fixture(port=port)
            unrelated_python = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            fixture.children.append(unrelated_python)

            result = fixture.stop_partial_stack(
                {"HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE": "true"}
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("stop incomplete", (result.stdout + result.stderr).lower())
            self.assertTrue(port_is_listening(port))
            self.assertTrue((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(root.poll())
            self.assertTrue(
                all(
                    subprocess.run(
                        [
                            POWERSHELL,
                            "-NoLogo",
                            "-NoProfile",
                            "-Command",
                            f"exit [int]($null -ne (Get-Process -Id {int(row['pid'])} -ErrorAction SilentlyContinue))",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    ).returncode
                    == 1
                    for row in lineage
                )
            )
            self.assertIsNone(unrelated_python.poll())

    def test_camera_hub_managed_tree_retains_snapshot_depth_when_current_tree_omits_listener(self) -> None:
        with LauncherFixture() as fixture:
            port = unused_loopback_port()
            _, lineage = fixture.start_managed_camera_tree_fixture(port=port)
            listener_pid = int(lineage[0]["pid"])
            unrelated_python = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            fixture.children.append(unrelated_python)

            result = fixture.stop_partial_stack(
                {
                    "HOME_CONTROL_STACK_STOP_TEST_OMIT_CURRENT_DESCENDANT_PID": str(
                        listener_pid
                    )
                }
            )

            output = result.stdout + result.stderr
            omitted_marker = (
                f"skipped PID {listener_pid} (no longer a current registry-root descendant)"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(omitted_marker, output)
            self.assertIn("stopped PID ", output)
            self.assertLess(output.index(omitted_marker), output.index("stopped PID "))
            self.assertTrue(port_is_listening(port))
            self.assertTrue((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(unrelated_python.poll())

    def test_stop_accepts_final_quiescence_after_shutdown_script_nonzero(self) -> None:
        with LauncherFixture(
            {"HOME_CONTROL_LAUNCHER_TEST_FORCE_STOP_SCRIPT_NONZERO": "true"}
        ) as fixture:
            fixture.configure_touchdesigner_target()
            payload = fixture.post("/api/stop", {})
            self.assertTrue(payload["ok"])
            self.assertNotEqual(payload.get("code"), 0)
            self.assertFalse(payload["stopVerification"]["pidFileExists"])
            self.assertEqual(payload["stopVerification"]["aliveRecorded"], [])
            self.assertEqual(payload["stopVerification"]["openPorts"], [])

    def test_stop_waits_for_refused_owned_target_to_exit_before_removing_registry(self) -> None:
        with LauncherFixture() as fixture:
            child, _ = fixture.listener(managed=False)
            fixture.write_pid_state(
                {
                    "name": "settling-owned-fixture",
                    "pid": child.pid,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "allowed_process_names": ["node"],
                },
                schema_version=3,
            )
            environment = {
                **fixture.test_environment,
                "HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE": "true",
                "HOME_CONTROL_STACK_STOP_TEST_SETTLE_TIMEOUT_MS": "300",
                "HOME_CONTROL_STACK_STATE_DIR": str(fixture.state_dir),
            }
            process = subprocess.Popen(
                [
                    POWERSHELL,
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STACK_STOP),
                    "-WorkspaceRoot",
                    str(fixture.workspace),
                    "-StackStateDir",
                    str(fixture.state_dir),
                    "-Force",
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            branch_observed = threading.Event()

            def release_after_refusal() -> None:
                assert process.stdout is not None
                for line in process.stdout:
                    if "registry root identity changed before termination" in line:
                        time.sleep(0.25)
                        child.terminate()
                        branch_observed.set()

            reader = threading.Thread(target=release_after_refusal, daemon=True)
            reader.start()
            self.assertEqual(process.wait(timeout=10), 0)
            reader.join(timeout=2)
            assert process.stdout is not None
            process.stdout.close()
            self.assertTrue(branch_observed.is_set())
            child.wait(timeout=3)
            self.assertFalse((fixture.state_dir / "pids.json").exists())

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
            self.assertEqual(
                fixture.test_environment["HOME_CONTROL_STACK_STOP_TEST_SETTLE_TIMEOUT_MS"],
                "250",
            )
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

    def test_home_and_thought_sealed_two_hop_listener_stop_after_root_exit(self) -> None:
        for service in ("home", "thought"):
            with self.subTest(service=service), LauncherFixture() as fixture:
                unrelated, _ = fixture.listener(managed=False)
                port = unused_loopback_port()
                root, record = fixture.start_sealed_service_fixture(service, port)
                wait_for(lambda: root.poll() is not None, timeout=5)

                cleanup = fixture.stop_partial_stack()
                second = fixture.stop_partial_stack()

                self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
                self.assertEqual(second.returncode, 0, f"{second.stdout}\n{second.stderr}")
                wait_for(lambda: not port_is_listening(port))
                self.assertFalse((fixture.state_dir / "pids.json").exists())
                self.assertIsNone(unrelated.poll())
                self.assertEqual(record["working_directory"], "")
                self.assertEqual(record["command"], "")

    def test_thought_listener_seal_retries_after_stale_first_descendant_snapshot(self) -> None:
        source = STACK_START.read_text(encoding="utf-8")
        self.assertIn(
            "HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ONCE",
            source,
        )
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()

            result = fixture.start_partial_stack(
                port=port,
                cleanup_failure=False,
                extra_environment={
                    "HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ONCE": "true"
                },
            )

            watcher_marker = (
                fixture.workspace
                / "control-plane"
                / "core"
                / "scripts"
                / "watcher-ran-and-failed.txt"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(
                watcher_marker.exists(),
                f"{result.stdout}\n{result.stderr}",
            )
            manifest = json.loads(
                (fixture.state_dir / "thought-core-api" / "controller.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                set(manifest),
                {
                    "schema_version",
                    "service_class",
                    "launch_nonce",
                    "controller_pid",
                    "controller_started_at",
                    "expected_module",
                    "expected_port",
                    "written_at",
                },
            )
            self.assertFalse(
                {"command", "cwd", "path", "environment", "token", "provider"}
                & set(manifest)
            )
            scripts = fixture.workspace / "control-plane" / "core" / "scripts"
            self.assertEqual(
                (scripts / "controller-env-status.txt").read_text(encoding="utf-8"),
                "private_env_absent",
            )
            self.assertEqual(
                (scripts / "listener-env-status.txt").read_text(encoding="utf-8"),
                "private_env_absent",
            )
            self.assertFalse(port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(unrelated.poll())

    def test_thought_listener_seal_rejects_persistent_non_descendant(self) -> None:
        source = STACK_START.read_text(encoding="utf-8")
        self.assertIn(
            "HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ALWAYS",
            source,
        )
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()

            result = fixture.start_partial_stack(
                port=port,
                cleanup_failure=False,
                extra_environment={
                    "HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ALWAYS": "true"
                },
            )

            watcher_marker = (
                fixture.workspace
                / "control-plane"
                / "core"
                / "scripts"
                / "watcher-ran-and-failed.txt"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(watcher_marker.exists())
            self.assertFalse(port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(unrelated.poll())

    def test_thought_controller_manifest_mutations_fail_closed(self) -> None:
        mutations = (
            "missing",
            "partial",
            "stale",
            "nonce",
            "controller_start",
            "service_class",
            "module",
            "port",
            "controller_pid",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), LauncherFixture() as fixture:
                unrelated, _ = fixture.listener(managed=False)
                port = unused_loopback_port()
                extra_environment = {
                    "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_MUTATION": mutation
                }
                if mutation == "controller_pid":
                    extra_environment[
                        "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_CONTROLLER_PID"
                    ] = str(unrelated.pid)

                result = fixture.start_partial_stack(
                    port=port,
                    cleanup_failure=False,
                    extra_environment=extra_environment,
                )

                watcher_marker = (
                    fixture.workspace
                    / "control-plane"
                    / "core"
                    / "scripts"
                    / "watcher-ran-and-failed.txt"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(watcher_marker.exists())
                self.assertFalse(port_is_listening(port))
                wait_for(lambda: not (fixture.state_dir / "pids.json").exists())
                self.assertIsNone(unrelated.poll())

    def test_thought_controller_manifest_write_failure_cleans_exact_process_tree(
        self,
    ) -> None:
        with LauncherFixture() as fixture:
            unrelated, _ = fixture.listener(managed=False)
            port = unused_loopback_port()

            result = fixture.start_partial_stack(
                port=port,
                cleanup_failure=False,
                extra_environment={
                    "HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_MUTATION": "write_failure"
                },
            )

            scripts = fixture.workspace / "control-plane" / "core" / "scripts"
            watcher_marker = scripts / "watcher-ran-and-failed.txt"
            pid_file = scripts / "controller-pids.json"
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(watcher_marker.exists())
            self.assertTrue(pid_file.exists())
            owned_pids = json.loads(pid_file.read_text(encoding="utf-8"))
            wait_for(
                lambda: not process_is_alive(owned_pids["controller_pid"])
                and not process_is_alive(owned_pids["listener_pid"])
            )
            wait_for(lambda: not port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertEqual(
                list((fixture.state_dir / "thought-core-api").glob("controller.json.*.tmp")),
                [],
            )
            self.assertEqual(
                (scripts / "controller-env-status.txt").read_text(encoding="utf-8"),
                "private_env_absent",
            )
            combined_output = result.stdout + result.stderr
            self.assertIn(
                "thought_core_controller_manifest_write_failed",
                combined_output,
            )
            self.assertNotIn(str(fixture.root), combined_output)
            self.assertNotIn(
                str(fixture.state_dir / "thought-core-api" / "controller.json"),
                combined_output,
            )
            self.assertNotIn(str(pid_file), combined_output)
            self.assertNotIn("fixture_write_failure", combined_output)
            self.assertNotIn("CategoryInfo", combined_output)
            self.assertNotIn("FullyQualifiedErrorId", combined_output)
            self.assertNotIn("At line:", combined_output)
            self.assertNotIn("start-home-control-stack.ps1:", combined_output)
            self.assertIn("home_control_stack_start_failed", combined_output)
            self.assertIsNone(unrelated.poll())

    def test_home_and_thought_launcher_reclaim_use_sealed_record(self) -> None:
        for service in ("home", "thought"):
            with self.subTest(service=service), LauncherFixture() as fixture:
                unrelated, _ = fixture.listener(managed=False)
                port = unused_loopback_port()
                root, record = fixture.start_sealed_service_fixture(service, port)
                wait_for(lambda: root.poll() is not None, timeout=5)
                fixture.configure_sealed_target(service, port)

                payload = fixture.post("/api/reclaim-managed-ports", {})

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["managedPortReclaim"]["attempted"], 1)
                self.assertEqual(
                    [row["pid"] for row in payload["managedPortReclaim"]["reclaimed"]],
                    [record["pid"]],
                )
                wait_for(lambda: not port_is_listening(port))
                self.assertIsNone(unrelated.poll())
                cleanup = fixture.stop_partial_stack()
                self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")

    def test_home_and_thought_sealed_mutations_fail_closed_with_residue(self) -> None:
        for service in ("home", "thought"):
            with self.subTest(service=service), LauncherFixture() as fixture:
                port = unused_loopback_port()
                root, original = fixture.start_sealed_service_fixture(service, port)
                wait_for(lambda: root.poll() is not None, timeout=5)
                other_class = (
                    "thought_core_descendant_listener.v0"
                    if service == "home"
                    else "home_control_bridge_descendant_listener.v0"
                )
                mutations = {
                    "listener_start_drift": lambda row: fixture.reseal_record(
                        {**row, "started_at": "2000-01-01T00:00:00+00:00"}
                    ),
                    "parent_start_drift": lambda row: fixture.reseal_record(
                        {
                            **row,
                            "ownership_lineage": [
                                {**row["ownership_lineage"][0], "started_at": "2000-01-01T00:00:00+00:00"},
                                *row["ownership_lineage"][1:],
                            ],
                        }
                    ),
                    "parent_replacement": lambda row: fixture.reseal_record(
                        {**row, "ownership_parent_pid": row["ownership_parent_pid"] + 1}
                    ),
                    "seal_mutation": lambda row: {**row, "ownership_seal": "0" * 64},
                    "missing_seal": lambda row: {key: value for key, value in row.items() if key != "ownership_seal"},
                    "cross_service": lambda row: fixture.reseal_record(
                        {**row, "ownership_class": other_class}
                    ),
                }
                for name, mutate in mutations.items():
                    with self.subTest(service=service, mutation=name):
                        fixture.write_pid_state(mutate(original))
                        cleanup = fixture.stop_partial_stack()
                        self.assertNotEqual(cleanup.returncode, 0)
                        self.assertIn("stop incomplete", (cleanup.stdout + cleanup.stderr).lower())
                        self.assertTrue(port_is_listening(port))
                        self.assertTrue((fixture.state_dir / "pids.json").exists())
                fixture.write_pid_state(original)
                cleanup = fixture.stop_partial_stack()
                self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")

    def test_all_sealed_services_fail_closed_before_generic_cleanup_on_invalid_class(self) -> None:
        service_classes = {
            "vsp": "vsp_descendant_listener.v0",
            "home": "home_control_bridge_descendant_listener.v0",
            "thought": "thought_core_descendant_listener.v0",
        }
        service_roles = {
            "vsp": "vision_snapshot_processor_listener",
            "home": "home_assistant_bridge_listener",
            "thought": "thought_core_api_listener",
        }
        selected_service = os.environ.get("HOME_CONTROL_STACK_TEST_SEALED_SERVICE", "")
        for service, ownership_class in service_classes.items():
            if selected_service and service != selected_service:
                continue
            with self.subTest(service=service), LauncherFixture() as fixture:
                port = unused_loopback_port()
                if service == "vsp":
                    fixture.start_vsp_orphan_fixture(port=port)
                    wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
                    original = fixture.read_vsp_listener_record()
                else:
                    _, original = fixture.start_sealed_service_fixture(
                        service, port, root_exit_seconds=30.0
                    )
                self.assertIsNotNone(original)
                unrelated_python = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                fixture.children.append(unrelated_python)
                cross_service_class = next(value for value in service_classes.values() if value != ownership_class)
                cross_service_role = next(value for key, value in service_roles.items() if key != service)
                mutations: list[tuple[str, dict]] = []
                for class_mutation in ("missing", "empty", "unknown"):
                    for role_mutation in ("missing", "empty", "unknown", "cross_service"):
                        mutated = dict(original)
                        if class_mutation == "missing":
                            mutated.pop("ownership_class", None)
                        elif class_mutation == "empty":
                            mutated["ownership_class"] = ""
                        else:
                            mutated["ownership_class"] = "unknown_descendant_listener.v0"
                        if role_mutation == "missing":
                            mutated.pop("role", None)
                        elif role_mutation == "empty":
                            mutated["role"] = ""
                        elif role_mutation == "unknown":
                            mutated["role"] = "unknown_listener_role"
                        else:
                            mutated["role"] = cross_service_role
                        mutations.append((f"{class_mutation}_class_{role_mutation}_role", mutated))
                mutations.append(("cross_service_class_and_role", {
                    **original,
                    "ownership_class": cross_service_class,
                    "role": cross_service_role,
                }))
                generic_fields = {
                    key: original[key]
                    for key in (
                        "name",
                        "module",
                        "pid",
                        "started_at",
                        "allowed_process_names",
                        "child_process_file",
                    )
                    if key in original
                }
                for marker in (
                    "ownership_seal",
                    "ownership_root_pid",
                    "ownership_parent_pid",
                    "ownership_root_started_at",
                    "ownership_lineage",
                ):
                    mutations.append((f"partial_{marker}", {**generic_fields, marker: original[marker]}))
                mutations.append(("partial_stop_strategy", {
                    **generic_fields,
                    "stop_strategy": "role_scoped_descendant",
                }))
                sealed_property_values = {
                    "ownership_seal": ("", 0, None, [], {"malformed": True}),
                    "ownership_root_pid": ("", 0, None, [], {"malformed": True}),
                    "ownership_root_started_at": ("", 0, None, [], {"malformed": True}),
                    "ownership_parent_pid": ("", 0, None, [], {"malformed": True}),
                    "ownership_lineage": ("", 0, None, [], {"malformed": True}),
                    "expected_module": ("", 0, None, [], {"malformed": True}),
                    "expected_port": ("", 0, None, [], {"malformed": True}),
                }
                value_labels = ("empty_string", "zero", "null", "empty_array", "malformed_type")
                for sealed_property, values in sealed_property_values.items():
                    for value_label, value in zip(value_labels, values, strict=True):
                        mutations.append((
                            f"present_{sealed_property}_{value_label}",
                            {**generic_fields, sealed_property: value},
                        ))
                for mutation_name, mutated in mutations:
                    with self.subTest(service=service, mutation=mutation_name):
                        fixture.write_pid_state(mutated, schema_version=3)

                        cleanup = fixture.stop_partial_stack()

                        output = cleanup.stdout + cleanup.stderr
                        self.assertNotEqual(cleanup.returncode, 0)
                        self.assertIn("Sealed descendant listener ownership unavailable:", output)
                        self.assertIn("stop incomplete", output.lower())
                        self.assertIn(
                            f"retained PID registry for verification: {original['pid']}",
                            output,
                        )
                        self.assertTrue(port_is_listening(port))
                        self.assertTrue((fixture.state_dir / "pids.json").exists())
                        self.assertIsNone(unrelated_python.poll())
                fixture.write_pid_state(original, schema_version=3)
                cleanup = fixture.stop_partial_stack()
                self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
                self.assertIsNone(unrelated_python.poll())

    def test_genuine_legacy_nonsealed_entry_uses_generic_direct_cleanup(self) -> None:
        with LauncherFixture() as fixture:
            child, port = fixture.listener(managed=True)
            fixture.write_pid_state({
                "name": "legacy_generic_listener",
                "module": "legacy-fixture",
                "role": "legacy_api",
                "pid": child.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "allowed_process_names": ["node"],
                "child_process_file": "",
            })

            cleanup = fixture.stop_partial_stack()

            self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: child.poll() is not None)
            self.assertFalse(port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())

    def test_exact_v2_default_bundle_uses_prior_generic_cleanup(self) -> None:
        with LauncherFixture() as fixture:
            child, port = fixture.listener(managed=True)
            unrelated_python = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            fixture.children.append(unrelated_python)
            v2_entry = {
                "name": "legacy_v2_generic_listener",
                "module": "legacy-fixture",
                "role": "legacy_api",
                "pid": child.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "stop_strategy": "managed_tree",
                "allowed_process_names": ["node"],
                "child_process_file": "",
                "ownership_class": "",
                "ownership_root_pid": 0,
                "ownership_root_started_at": "",
                "ownership_parent_pid": 0,
                "ownership_lineage": [],
                "ownership_seal": "",
                "expected_module": "",
                "expected_port": 0,
            }
            near_default_mutations = {
                "nondefault_root_pid": {**v2_entry, "ownership_root_pid": 1},
                "null_seal": {**v2_entry, "ownership_seal": None},
                "strategy_process_tree": {**v2_entry, "stop_strategy": "process_tree"},
                "strategy_empty": {**v2_entry, "stop_strategy": ""},
                "strategy_null": {**v2_entry, "stop_strategy": None},
                "strategy_unknown": {**v2_entry, "stop_strategy": "unknown"},
                "strategy_case_title": {**v2_entry, "stop_strategy": "Managed_Tree"},
                "strategy_case_upper": {**v2_entry, "stop_strategy": "MANAGED_TREE"},
                "strategy_case_mixed": {**v2_entry, "stop_strategy": "managed_Tree"},
                "strategy_non_string": {**v2_entry, "stop_strategy": ["managed_tree"]},
                "partial_bundle": {
                    key: value for key, value in v2_entry.items() if key != "expected_port"
                },
            }
            for mutation_name, mutated in near_default_mutations.items():
                with self.subTest(mutation=mutation_name):
                    fixture.write_pid_state(mutated, schema_version=2)
                    refused = fixture.stop_partial_stack()
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn("stop incomplete", (refused.stdout + refused.stderr).lower())
                    self.assertTrue(port_is_listening(port))
                    self.assertTrue((fixture.state_dir / "pids.json").exists())
                    self.assertIsNone(unrelated_python.poll())
            fixture.write_pid_state(v2_entry, schema_version=2)

            cleanup = fixture.stop_partial_stack()

            self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")
            wait_for(lambda: child.poll() is not None)
            self.assertFalse(port_is_listening(port))
            self.assertFalse((fixture.state_dir / "pids.json").exists())
            self.assertIsNone(unrelated_python.poll())

    def test_real_v2_sealed_records_still_validate_and_fail_closed(self) -> None:
        selected_service = os.environ.get("HOME_CONTROL_STACK_TEST_SEALED_SERVICE", "")
        services = (selected_service,) if selected_service else ("vsp", "home", "thought")
        for service in services:
            with self.subTest(service=service), LauncherFixture() as fixture:
                port = unused_loopback_port()
                if service == "vsp":
                    fixture.start_vsp_orphan_fixture(port=port)
                    wait_for(lambda: fixture.read_vsp_listener_record() is not None, timeout=20)
                    original = fixture.read_vsp_listener_record()
                else:
                    _, original = fixture.start_sealed_service_fixture(
                        service, port, root_exit_seconds=30.0
                    )
                unrelated_python = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                fixture.children.append(unrelated_python)
                fixture.write_pid_state(
                    {**original, "ownership_seal": "0" * 64},
                    schema_version=2,
                )

                cleanup = fixture.stop_partial_stack()

                output = cleanup.stdout + cleanup.stderr
                self.assertNotEqual(cleanup.returncode, 0)
                self.assertIn("seal_invalid", output)
                self.assertIn("stop incomplete", output.lower())
                self.assertTrue(port_is_listening(port))
                self.assertTrue((fixture.state_dir / "pids.json").exists())
                self.assertIsNone(unrelated_python.poll())
                fixture.write_pid_state(original, schema_version=2)
                restored = fixture.stop_partial_stack()
                self.assertEqual(restored.returncode, 0, f"{restored.stdout}\n{restored.stderr}")

    def test_sealed_reclaim_source_requires_unique_revalidation_and_no_generic_python(self) -> None:
        source = LAUNCHER_SERVER.read_text(encoding="utf-8")
        stop_source = STACK_STOP.read_text(encoding="utf-8")
        self.assertIn("owners.length === 1", source)
        self.assertIn("currentOwners.length === 1", source)
        self.assertIn("validateSealedListenerEntry", source)
        self.assertIn("Test-SwordSealedDescendantListenerEntry", stop_source)
        self.assertNotIn("allowedProcessNames: ['python']\n  },\n  thought_core_api", source)
        for legacy_symbol, owning_source in {
            "Test-VisionSnapshotListenerCommand": stop_source,
            "Get-VisionSnapshotOwnershipSeal": stop_source,
            "Test-VisionSnapshotListenerEntryOwned": stop_source,
            "vspListenerCommandMatches": source,
            "vspOwnershipSeal": source,
            "vspListenerRegistryEntryMatches": source,
        }.items():
            self.assertNotIn(legacy_symbol, owning_source)

    def test_home_and_thought_launcher_reclaim_fail_closed_on_duplicate_and_revalidation(self) -> None:
        modes = {
            "duplicate_owner": "HOME_CONTROL_LAUNCHER_TEST_DUPLICATE_PORT_OWNER",
            "revalidation_failure": "HOME_CONTROL_LAUNCHER_TEST_FAIL_SEALED_REVALIDATION",
        }
        for service in ("home", "thought"):
            for mode, environment_name in modes.items():
                with self.subTest(service=service, mode=mode), LauncherFixture(
                    {environment_name: "true"}
                ) as fixture:
                    port = unused_loopback_port()
                    fixture.start_sealed_service_fixture(service, port, root_exit_seconds=30.0)
                    fixture.configure_sealed_target(service, port)

                    payload = fixture.post("/api/reclaim-managed-ports", {})

                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["managedPortReclaim"]["attempted"], 0)
                    self.assertGreaterEqual(payload["managedPortReclaim"]["skippedOwners"], 1)
                    self.assertTrue(port_is_listening(port))
                    cleanup = fixture.stop_partial_stack()
                    self.assertEqual(cleanup.returncode, 0, f"{cleanup.stdout}\n{cleanup.stderr}")

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
