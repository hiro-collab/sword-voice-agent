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
GRAPH_PATH = ROOT / "ops" / "manifests" / "launcher-service-graph.standard.v1.json"
BINDING_PATH = ROOT / "contracts" / "launcher" / "generated" / "launcher-service-graph.standard.v1.binding.json"
PROJECT_PATH = ROOT / "tools" / "launcher-supervisor" / "LauncherSupervisor.Domain.csproj"
NUGET_CONFIG_PATH = ROOT / "tools" / "launcher-supervisor" / "NuGet.Config"
DLL_PATH = ROOT / "tools" / "launcher-supervisor" / "bin" / "Debug" / "net8.0" / "LauncherSupervisor.Domain.dll"


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LauncherSupervisorContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_schema = load_json(GRAPH_SCHEMA_PATH)
        cls.operation_schema = load_json(OPERATION_SCHEMA_PATH)
        cls.graph = load_json(GRAPH_PATH)
        cls.binding_document = load_json(BINDING_PATH)

    def test_schemas_are_strict_draft_2020_12(self) -> None:
        for schema in (self.graph_schema, self.operation_schema):
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(schema["additionalProperties"])

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
        phases = self.operation_schema["properties"]["phase"]["enum"]
        for required in ("planned", "preflight", "prepared", "starting", "waiting_ready", "ready", "failed", "stopping", "stopped", "residue"):
            self.assertIn(required, phases)
        self.assertNotIn("created", phases)
        self.assertNotIn("preflighting", phases)

    def test_standard_graph_ownership_and_optional_boundaries(self) -> None:
        services = {service["service_id"]: service for service in self.graph["services"]}
        self.assertEqual(len(services), 10)
        self.assertEqual(
            {service_id for service_id, service in services.items() if service["requirement"] == "optional"},
            {"mediapipe_camera_hub_stack", "vision_snapshot_processor"},
        )
        self.assertEqual(services["mediapipe_camera_hub_stack"]["start"]["absent_behavior"], "optional_absent")
        self.assertEqual(services["vision_snapshot_processor"]["start"]["absent_behavior"], "optional_absent")
        expected_ports = {
            "aituber_kit": ("manifest_default", "http", 3000),
            "environment_state_server": ("manifest_default", "http", 8790),
            "home_assistant_bridge": ("manifest_default", "http", 8787),
            "mediapipe_camera_hub_stack": ("manifest_default", "websocket", 8765),
            "openai_provider_broker": ("manifest_default", "http", 18786),
            "thought_core_api": ("manifest_default", "http", 18787),
            "thought_core_watcher": ("none", "none", None),
            "touchdesigner_control_gui": ("manifest_default", "http", 8788),
            "vision_snapshot_processor": ("manifest_default", "websocket", 8776),
            "voicevox": ("launcher_default", "http", 50021),
        }
        for service_id, (mode, transport, port) in expected_ports.items():
            self.assertEqual(
                (services[service_id]["port"]["port_mode"], services[service_id]["port"]["transport"], services[service_id]["port"]["loopback_port"]),
                (mode, transport, port),
            )

        voicevox = services["voicevox"]
        self.assertEqual(voicevox["requirement"], "external")
        self.assertEqual(voicevox["ownership"], "external")
        self.assertEqual(voicevox["start"]["adapter_id"], "external_probe_only")
        self.assertEqual(voicevox["stop"], {"adapter_id": "external_noop", "graceful_timeout_ms": 0, "escalation": "none"})

    def test_generated_binding_matches_exact_sources(self) -> None:
        binding = self.binding_document["binding"]
        sha_pattern = re.compile(r"^[0-9a-f]{64}$")
        for field in ("binding_sha256",):
            self.assertRegex(self.binding_document[field], sha_pattern)
        for field in ("graph_sha256", "graph_schema_sha256", "operation_schema_sha256"):
            self.assertRegex(binding[field], sha_pattern)

        self.assertEqual(binding["graph_sha256"], sha256(GRAPH_PATH))
        self.assertEqual(binding["graph_schema_sha256"], sha256(GRAPH_SCHEMA_PATH))
        self.assertEqual(binding["operation_schema_sha256"], sha256(OPERATION_SCHEMA_PATH))
        compact = json.dumps(binding, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertEqual(self.binding_document["binding_sha256"], hashlib.sha256(compact).hexdigest())

        self.assertEqual(binding["optional_service_ids"], ["mediapipe_camera_hub_stack", "vision_snapshot_processor"])
        self.assertEqual(binding["external_service_ids"], ["voicevox"])
        self.assertEqual(len(binding["service_order"]), len(self.graph["services"]))
        self.assertEqual(set(binding["service_order"]), {service["service_id"] for service in self.graph["services"]})

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
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps([self.graph, self.binding_document, self.operation_schema]))

    def test_offline_domain_build_validation_and_self_tests(self) -> None:
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            self.fail("dotnet_not_found")
        commands = (
            [dotnet, "restore", str(PROJECT_PATH), "--configfile", str(NUGET_CONFIG_PATH), "-nologo"],
            [dotnet, "build", str(PROJECT_PATH), "--no-restore", "-nologo"],
            [dotnet, str(DLL_PATH), "validate", str(ROOT)],
            [dotnet, str(DLL_PATH), "self-test", str(ROOT)],
        )
        outputs: list[str] = []
        for command in commands:
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
            self.assertEqual(completed.returncode, 0, msg="launcher_supervisor_offline_check_failed")
        self.assertIn("VALIDATION_CLEAR", outputs[2])
        summary = re.search(r"SELF_TEST (\d+)/(\d+)", outputs[3])
        self.assertIsNotNone(summary)
        self.assertEqual(summary.group(1), summary.group(2))
        self.assertGreater(int(summary.group(1)), 0)


if __name__ == "__main__":
    unittest.main()
