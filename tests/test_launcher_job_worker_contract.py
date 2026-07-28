from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "tools" / "home-control-launcher" / "launcher-job-worker-client.js"
PLAN = ROOT / "ops" / "scripts" / "home-control-stack" / "launcher-service-plan.psm1"
WORKER = ROOT / "ops" / "scripts" / "home-control-stack" / "launcher-job-worker.ps1"

FROZEN_N0 = {
    "contracts/launcher/launcher-operation.v1.schema.json": "a91b5d54d45d99e2e25adcef4319ed3c2bc7dd4bd564a261e796a8dc191c85e3",
    "contracts/launcher/launcher-service-graph.v1.schema.json": "ee1c82978459e2983e1ddb02994007b8be6be82a0466d1fd236597007588ad56",
    "contracts/launcher/launcher-worker.v1.schema.json": "928b94669baa7ea9311d02fdc59ae73fb4727429138ee4f93e8fb657b76202c4",
    "contracts/launcher/launcher-reducer-vectors.v1.json": "379fc9998a943b98a56857bc494f5840c2662cfdce7ab5bfb270b678d78ccf1c",
    "contracts/launcher/generated/launcher-service-graph.standard.v1.binding.json": "3a74d2c620f55c8203b6a1e9cc66c631c1867d131d69362743fe73e300bd9229",
    "ops/manifests/launcher-service-graph.standard.v1.json": "dc548b8ddd9528af3a6d10325f868af200fe3d85d1e88182cdcf32407506ea77",
    "tools/home-control-launcher/launcher-supervisor-contract.js": "90212e330049bc2e5b0fabec50de270ffdb9f12b08b0f2cfb1df7c28a67b0a61",
    "tools/home-control-launcher/launcher-supervisor-reducer.js": "e8375a1fba2cd0c54dfd84ae7bb471635215050ca8662c07c657f2884d0ab673",
    "tools/home-control-launcher/launcher-operation-store.js": "9ca00cfaea8c8058ef7cfb99e11e39b991ea09082f698304efeb908068c8410f",
    "tools/home-control-launcher/server.js": "99bdc202bd5b8cafa0e3463801df961728ad4612d3f00a4f9f09b77c8b581606",
    "ops/scripts/home-control-stack/start-home-control-stack.ps1": "d5f1b2556e3a71520b5117eef8774326b70b05221064122dccc9c1296ac8d1ec",
    "ops/scripts/home-control-stack/stop-home-control-stack.ps1": "acdb237f13f76eabfd743f24619b8b5c90512a7f1149ab55232239d476e67619",
    "ops/scripts/home-control-stack/status-home-control-stack.ps1": "db2ed1f9e7f6e21785d4a081cc35db818d1fbbd4e9c2b7e88d40ddbb628eda44",
}


class LauncherJobWorkerContractTests(unittest.TestCase):
    def test_n0_authority_and_legacy_lifecycle_are_byte_frozen(self) -> None:
        for relative, expected in FROZEN_N0.items():
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)

    def test_exact_worker_sources_have_no_launcher_cutover(self) -> None:
        client = CLIENT.read_text(encoding="utf-8")
        plan = PLAN.read_text(encoding="utf-8")
        worker = WORKER.read_text(encoding="utf-8")
        self.assertIn("validateWorkerRequestAgainstAuthority", client)
        self.assertIn("correlateWorkerResult", client)
        self.assertIn("createOwnerLivenessObserver", client)
        self.assertIn("SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE", client)
        self.assertIn("exactAbsoluteFile(powershellPath)", client)
        self.assertIn("DEFAULT_RESPONSE_GRACE_MS", client)
        self.assertIn("this.terminal = true", client)
        self.assertIn("launcher_private_service_plans.v1", plan)
        self.assertIn("SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE", plan)
        self.assertIn("Resolve-LauncherServicePlan", plan)
        self.assertIn("CreateProcessW", worker)
        self.assertIn("CREATE_SUSPENDED", worker)
        self.assertIn("AssignProcessToJobObject", worker)
        self.assertIn("JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", worker)
        self.assertIn("ResumeThread", worker)
        self.assertIn("QueryInformationJobObject", worker)
        self.assertIn("IsProcessInJob", worker)
        self.assertIn("finally {", worker)
        self.assertIn("$record.Native.Dispose()", worker)
        self.assertIn('$ReservedEnvironmentNames = @("SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE")', worker)
        self.assertIn("$environment.Remove($name)", worker)
        self.assertNotIn("Get-ChildItem Env:", worker)
        self.assertNotIn("launcher-job-worker-client", (ROOT / "tools/home-control-launcher/server.js").read_text(encoding="utf-8"))
        self.assertNotIn("launcher-job-worker.ps1", (ROOT / "ops/scripts/home-control-stack/start-home-control-stack.ps1").read_text(encoding="utf-8"))

    def test_public_worker_result_is_the_frozen_bounded_shape(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        expected = {
            "schema_version",
            "message_type",
            "operation_id",
            "service_id",
            "action",
            "expected_revision",
            "worker_nonce",
            "result_class",
            "ownership_class",
            "listener_class",
            "descendant_class",
        }
        schema = json.loads((ROOT / "contracts/launcher/launcher-worker.v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["$defs"]["result"]["required"]), expected)
        self.assertEqual(set(schema["$defs"]["result"]["properties"]), expected)
        for prohibited in (
            "raw_command", "raw_args", "raw_env", "raw_path", "raw_stdout",
            "raw_stderr", "secret_value", "PRIVATE_SENTINEL",
        ):
            self.assertNotIn(prohibited, worker)

    def test_powershell_sources_parse_without_execution(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.fail("pwsh_not_found")
        parser = (
            "$tokens=$null;$errors=$null;"
            "[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)|Out-Null;"
            "if($errors.Count-ne 0){exit 1}"
        )
        for source in (PLAN, WORKER):
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", parser, str(source)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, source.name)

    def test_embedded_job_object_source_compiles_without_starting_a_process(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.fail("pwsh_not_found")
        worker = WORKER.read_text(encoding="utf-8")
        match = re.search(r"(?s)\$nativeSource\s*=\s*@'\n(.*?)\n'@", worker)
        self.assertIsNotNone(match)
        command = (
            "Add-Type -TypeDefinition $args[0] -Language CSharp -ErrorAction Stop;"
            "$observation=[SwordLauncherOwnedJob]::ObserveProcess($PID,1);"
            "if($observation-ne2){exit 4}"
        )
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", command, match.group(1)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0)

    def test_plan_module_exposes_exact_graph_service_ids_and_external_boundary(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.fail("pwsh_not_found")
        command = (
            "Import-Module $args[0] -Force;"
            "$ids=@(Get-LauncherServiceIds);"
            "$external=Get-LauncherServiceDescriptor -ServiceId voicevox;"
            "[pscustomobject]@{ids=$ids;ownership=$external.Ownership;requirement=$external.Requirement;port=$external.DefaultListenerPort}|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", command, str(PLAN)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0)
        result = json.loads(completed.stdout.strip())
        graph = json.loads((ROOT / "ops/manifests/launcher-service-graph.standard.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(result["ids"], [service["service_id"] for service in graph["services"]])
        self.assertEqual(result["ownership"], "external")
        self.assertEqual(result["requirement"], "external")
        self.assertEqual(result["port"], 50021)

    def test_worker_result_literals_match_frozen_n0_action_boundaries(self) -> None:
        worker = WORKER.read_text(encoding="utf-8")
        start = worker.split("function Invoke-LauncherStart", 1)[1].split("function Invoke-LauncherProbe", 1)[0]
        probe = worker.split("function Invoke-LauncherProbe", 1)[1].split("function Invoke-LauncherStop", 1)[0]
        stop = worker.split("function Invoke-LauncherStop", 1)[1].split("function New-LauncherInvalidRequestResult", 1)[0]
        self.assertIn('"accepted" "matched" "not_applicable" "owned_active"', start)
        self.assertNotIn("Wait-LauncherOwnedReady", start)
        self.assertNotIn('"optional_absent"', start)
        self.assertIn('"external_ready" "not_applicable" "matched" "not_applicable"', probe)
        self.assertIn('"stopped" "matched" "not_applicable" "owned_clear"', stop)
        self.assertIn('"stop_failed" "unknown" "unknown" "unknown"', stop)
        self.assertNotIn('"stop_failed" "matched" "mismatch" "foreign"', stop)

    def test_private_plan_rejects_the_reserved_worker_capability(self) -> None:
        powershell = shutil.which("pwsh")
        if powershell is None:
            self.fail("pwsh_not_found")
        executable_by_service = {
            "home_assistant_bridge": "uv.exe",
            "environment_state_server": "uv.exe",
            "openai_provider_broker": "uv.exe",
            "thought_core_api": "pwsh.exe",
            "aituber_kit": "cmd.exe",
            "thought_core_watcher": "pwsh.exe",
            "touchdesigner_control_gui": "node.exe",
        }
        graph = json.loads((ROOT / "ops/manifests/launcher-service-graph.standard.v1.json").read_text(encoding="utf-8"))
        services = []
        for service in graph["services"]:
            if service["requirement"] != "required":
                continue
            services.append({
                "service_id": service["service_id"],
                "file_path": f"C:\\N1\\{executable_by_service[service['service_id']]}",
                "arguments": [],
                "working_directory": "C:\\N1",
                "environment": {},
                "remove_environment": [],
                "clear_inherited_environment": True,
                "listener_port": service["port"]["loopback_port"] or 0,
            })
        document = {
            "schema_version": "launcher_private_service_plans.v1",
            "graph_sha256": "a" * 64,
            "binding_sha256": "b" * 64,
            "services": services,
        }
        with tempfile.TemporaryDirectory(prefix="launcher-n1-plan-") as directory:
            plan_path = Path(directory) / "private-plan.json"
            plan_path.write_text(json.dumps(document), encoding="utf-8")
            valid_command = "Import-Module $args[0] -Force; Read-LauncherPrivateServicePlans -Path $args[1] | Out-Null"
            valid = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", valid_command, str(PLAN), str(plan_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(valid.returncode, 0)
            command = (
                "Import-Module $args[0] -Force;"
                "try { Read-LauncherPrivateServicePlans -Path $args[1] | Out-Null; exit 2 } "
                "catch { if($_.Exception.Message -cne 'launcher_private_plan_invalid'){exit 3}; exit 0 }"
            )
            document["services"][0]["clear_inherited_environment"] = False
            plan_path.write_text(json.dumps(document), encoding="utf-8")
            inherited = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", command, str(PLAN), str(plan_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(inherited.returncode, 0)
            document["services"][0]["clear_inherited_environment"] = True
            document["services"][0]["environment"] = {
                "SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE": "PRIVATE_SENTINEL"
            }
            plan_path.write_text(json.dumps(document), encoding="utf-8")
            completed = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-CommandWithArgs", command, str(PLAN), str(plan_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
