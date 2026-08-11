from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-service-graph.v1.schema.json"
OPERATION_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-operation.v1.schema.json"
WORKER_SCHEMA_PATH = ROOT / "contracts" / "launcher" / "launcher-worker.v1.schema.json"
VECTORS_PATH = ROOT / "contracts" / "launcher" / "launcher-reducer-vectors.v1.json"
GRAPH_PATH = ROOT / "ops" / "manifests" / "launcher-service-graph.standard.v1.json"
BINDING_PATH = ROOT / "contracts" / "launcher" / "generated" / "launcher-service-graph.standard.v1.binding.json"


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
            {
                "home_assistant_bridge",
                "environment_state_server",
                "mediapipe_camera_hub_stack",
                "vision_snapshot_processor",
                "thought_core_watcher",
                "touchdesigner_control_gui",
            },
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

    def test_frozen_v1_binding_remains_historically_self_consistent(self) -> None:
        binding = self.binding_document["binding"]
        self.assertEqual(binding["text_hash_mode"], "utf8_lf_v1")
        self.assertEqual(binding["graph_sha256"], "d5514bcbdd1e1499ac7fc025ea985d39cef83d3dff75552fc93296d7d3172b23")
        self.assertNotEqual(binding["graph_sha256"], canonical_lf_sha256(GRAPH_PATH))
        expected = {
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

    def test_parallel_csharp_supervisor_is_not_reintroduced(self) -> None:
        self.assertFalse((ROOT / "tools" / "launcher-supervisor").exists())


if __name__ == "__main__":
    unittest.main()
