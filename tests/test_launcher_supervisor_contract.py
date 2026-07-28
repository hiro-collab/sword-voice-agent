from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-service-graph.v1.schema.json"
OPERATION_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-operation.v1.schema.json"
WORKER_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-worker.v1.schema.json"
VECTORS_PATH = ROOT / "contracts" / "launcher" / "launcher-reducer-vectors.v1.json"
GRAPH_PATH = ROOT / "ops" / "manifests" / "launcher-service-graph.standard.v1.json"
BINDING_PATH = ROOT / "contracts" / "launcher" / "generated" / "launcher-service-graph.standard.v1.binding.json"
PROJECT_PATH = ROOT / "tools" / "launcher-supervisor" / "LauncherSupervisor.Domain.csproj"
NUGET_CONFIG_PATH = ROOT / "tools" / "launcher-supervisor" / "NuGet.Config"
DLL_PATH = ROOT / "tools" / "launcher-supervisor" / "bin" / "Debug" / "net8.0" / "LauncherSupervisor.Domain.dll"
DOTNET_APPLICATION_CONTROL_EXIT = 0xE0434352
DOTNET_APPLICATION_CONTROL_MARKER = "0x800711C7"
DOTNET_EXECUTION_PHASES = frozenset({"validate", "self-test"})


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_lf_sha256(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    if text.startswith("\ufeff"):
        raise AssertionError("contract_text_bom_invalid")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: object) -> str:
    compact = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()


def classify_dotnet_phase_result(phase: str, returncode: int, stderr: str) -> str:
    if returncode == 0:
        return "pass"
    if (
        phase in DOTNET_EXECUTION_PHASES
        and returncode & 0xFFFFFFFF == DOTNET_APPLICATION_CONTROL_EXIT
        and DOTNET_APPLICATION_CONTROL_MARKER in stderr
    ):
        return "skip_application_control"
    return "fail"


class LauncherSupervisorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_schema = load_json(GRAPH_SCHEMA_PATH)
        cls.operation_schema = load_json(OPERATION_SCHEMA_PATH)
        cls.worker_schema = load_json(WORKER_SCHEMA_PATH)
        cls.vectors = load_json(VECTORS_PATH)
        cls.graph = load_json(GRAPH_PATH)
        cls.binding_document = load_json(BINDING_PATH)

    def test_schemas_are_strict_draft_2020_12(self) -> None:
        for schema in (self.graph_schema, self.operation_schema, self.worker_schema):
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

        def assert_strict_objects(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    assert_strict_objects(value)
            elif isinstance(node, list):
                for value in node:
                    assert_strict_objects(value)

        assert_strict_objects(self.graph_schema)
        assert_strict_objects(self.operation_schema)
        assert_strict_objects(self.worker_schema)
        phases = self.operation_schema["properties"]["phase"]["enum"]
        for required in ("planned", "preflight", "prepared", "starting", "waiting_ready", "ready", "failed", "stopping", "stopped", "residue"):
            self.assertIn(required, phases)

    def test_operation_schema_owns_service_id_and_safe_revision_contracts(self) -> None:
        service_ref = "launcher-operation.v1.schema.json#/$defs/service_id"
        service_pattern = r"^[a-z][a-z0-9_]{0,63}$"
        self.assertEqual(self.operation_schema["$defs"]["service_id"]["pattern"], service_pattern)
        graph_service = self.graph_schema["$defs"]["service"]["properties"]
        self.assertEqual(graph_service["service_id"]["$ref"], service_ref)
        self.assertEqual(graph_service["dependencies"]["items"]["$ref"], service_ref)
        self.assertEqual(graph_service["start"]["properties"]["legacy_spec_ids"]["items"]["$ref"], service_ref)
        worker_request = self.worker_schema["$defs"]["request"]["properties"]
        worker_result = self.worker_schema["$defs"]["result"]["properties"]
        self.assertEqual(worker_request["service_id"]["$ref"], service_ref)
        self.assertEqual(worker_result["service_id"]["$ref"], service_ref)
        self.assertNotIn("id", self.worker_schema["$defs"])
        maximum = 9007199254740991
        self.assertEqual(self.operation_schema["properties"]["revision"]["maximum"], maximum)
        self.assertEqual(worker_request["expected_revision"]["maximum"], maximum)
        self.assertEqual(worker_result["expected_revision"]["maximum"], maximum)

    def test_standard_graph_keeps_boundaries_and_pins_future_worker_adapters(self) -> None:
        services = {service["service_id"]: service for service in self.graph["services"]}
        self.assertEqual(len(services), 10)
        self.assertEqual(
            {service_id for service_id, service in services.items() if service["requirement"] == "optional"},
            {"mediapipe_camera_hub_stack", "vision_snapshot_processor"},
        )
        for service in services.values():
            if service["ownership"] == "owned":
                self.assertEqual(service["start"]["adapter_id"], "job_worker_service")
                self.assertEqual(service["stop"]["adapter_id"], "job_worker_job_close")
                self.assertEqual(service["stop"]["escalation"], "owned_only")
            else:
                self.assertEqual(service["start"]["adapter_id"], "external_probe_only")
                self.assertEqual(service["stop"]["adapter_id"], "external_noop")
                self.assertEqual(service["stop"]["escalation"], "none")
        self.assertEqual(services["voicevox"]["requirement"], "external")
        self.assertEqual(services["mediapipe_camera_hub_stack"]["start"]["absent_behavior"], "optional_absent")

    def test_generated_binding_matches_canonical_lf_sources(self) -> None:
        binding = self.binding_document["binding"]
        self.assertEqual(binding["text_hash_mode"], "utf8_lf_v1")
        expected = {
            "graph_sha256": canonical_lf_sha256(GRAPH_PATH),
            "graph_schema_sha256": canonical_lf_sha256(GRAPH_SCHEMA_PATH),
            "operation_schema_sha256": canonical_lf_sha256(OPERATION_SCHEMA_PATH),
            "worker_schema_sha256": canonical_lf_sha256(WORKER_SCHEMA_PATH),
            "reducer_vectors_sha256": canonical_lf_sha256(VECTORS_PATH),
        }
        for field, digest in expected.items():
            self.assertEqual(binding[field], digest)
        self.assertEqual(self.binding_document["binding_sha256"], canonical_json_sha256(binding))
        self.assertEqual(binding["optional_service_ids"], ["mediapipe_camera_hub_stack", "vision_snapshot_processor"])
        self.assertEqual(binding["external_service_ids"], ["voicevox"])

    def test_reducer_vectors_cover_the_required_lifecycle_classes(self) -> None:
        coverage = {item for vector in self.vectors["vectors"] for item in vector["coverage"]}
        for required in (
            "planned", "preflight", "prepared", "dependency", "ownership", "pid_reuse", "listener",
            "deadline", "rollback", "recovery", "stop", "residue", "optional_camera", "external_voicevox",
            "stale_no_write",
        ):
            self.assertIn(required, coverage)

    def test_public_contracts_exclude_private_and_process_fields(self) -> None:
        prohibited = {"stdout", "stderr", "exception", "command", "args", "env", "token", "secret", "path", "url", "pid", "process"}

        def visit(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(key.lower(), prohibited)
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(self.graph)
        visit(self.binding_document)
        visit(self.operation_schema)
        visit(self.worker_schema)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps([self.graph, self.binding_document, self.operation_schema, self.worker_schema]))

    def test_adopted_csharp_m0_m1_sources_are_byte_frozen(self) -> None:
        expected = {
            "LauncherDomain.cs": "49f9a502cf80da7f4f233ad0ae6949f629720d7f96c509d08ed9cf8005b62747",
            "OperationReducer.cs": "d051527717c62889693f7f7c15223509befeba371133a276a41f3a08c569cef3",
            "SelfTests.cs": "2bda999e8218e04aa520f043993b47c097cccacd3b276cd0e436a3efce9788d8",
            "SupervisorProgram.cs": "3c6a2a3c727c058225e403b36469411685e673bc4560c21f3d86a8b6e0f6b697",
            "Program.cs": "acbe9beb96e6c1bd4ff715c4546b2f0b3058106eb05a278a23518d2ebee42604",
            "LauncherSupervisor.Domain.csproj": "c8b40283f0ec46a046d43e6db80ad027b3d3ea90343be22a2e5943e80de4bb1f",
            "NuGet.Config": "652624e1ac15698be31dc7bc90a74b1fcc4314c387551013c61fb3a113843d28",
            "README.md": "929cede3226560d28e013a5d2ce2ebcf779461688ef34d6fe3ee935f915b256e",
        }
        root = ROOT / "tools" / "launcher-supervisor"
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest, name)

    def test_dotnet_application_control_skip_is_execution_phase_only(self) -> None:
        blocked = DOTNET_APPLICATION_CONTROL_EXIT
        marker = DOTNET_APPLICATION_CONTROL_MARKER
        self.assertEqual(classify_dotnet_phase_result("restore", blocked, marker), "fail")
        self.assertEqual(classify_dotnet_phase_result("build", blocked, marker), "fail")
        self.assertEqual(classify_dotnet_phase_result("validate", blocked, marker), "skip_application_control")
        self.assertEqual(classify_dotnet_phase_result("self-test", blocked, marker), "skip_application_control")
        self.assertEqual(classify_dotnet_phase_result("validate", 1, marker), "fail")
        self.assertEqual(classify_dotnet_phase_result("validate", blocked, "different"), "fail")
        for phase in ("restore", "build", "validate", "self-test"):
            self.assertEqual(classify_dotnet_phase_result(phase, 0, marker), "pass")

    def test_offline_domain_build_validation_and_self_tests(self) -> None:
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            self.fail("dotnet_not_found")
        commands = (
            ("restore", [dotnet, "restore", str(PROJECT_PATH), "--configfile", str(NUGET_CONFIG_PATH), "-nologo"]),
            ("build", [dotnet, "build", str(PROJECT_PATH), "--no-restore", "-nologo"]),
            ("validate", [dotnet, str(DLL_PATH), "validate", str(ROOT)]),
            ("self-test", [dotnet, str(DLL_PATH), "self-test", str(ROOT)]),
        )
        outputs: list[str] = []
        for phase, command in commands:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            outputs.append(completed.stdout)
            disposition = classify_dotnet_phase_result(phase, completed.returncode, completed.stderr)
            if disposition == "skip_application_control":
                self.skipTest("dotnet_execute_blocked_by_application_control_0x800711C7")
            self.assertEqual(disposition, "pass", msg=f"launcher_supervisor_offline_check_failed:{phase}")
        self.assertIn("VALIDATION_CLEAR", outputs[2])
        summary = re.search(r"SELF_TEST (\d+)/(\d+)", outputs[3])
        self.assertIsNotNone(summary)
        self.assertEqual(summary.group(1), summary.group(2))
        self.assertGreater(int(summary.group(1)), 0)


if __name__ == "__main__":
    unittest.main()
