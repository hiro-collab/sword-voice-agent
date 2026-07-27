import hashlib
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.ordinary_route_contract import (  # noqa: E402
    canonical_review_match_required,
    load_ordinary_route_contract,
    ordinary_route_contract_sha256,
    review_checkpoint_class,
    review_checkpoint_payload,
)
from thought_core.tools import MockThoughtTools  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.tools import (  # noqa: E402
    HomeControlHttpTools,
    HomeControlToolConfig,
    HomeControlToolError,
)


CONTRACT_PATH = REPO_ROOT / "contracts" / "turn" / "ordinary-standard-route.v1.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "turn" / "ordinary-standard-route.v1.schema.json"
NODE_HELPER = REPO_ROOT / "tools" / "home-control-launcher" / "ordinary-route-contract.js"


class OrdinaryStandardRouteContractTest(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if cls.node is None:
            raise AssertionError("Node.js is required for ordinary route contract tests")
        cls.contract = load_ordinary_route_contract()

    def test_draft_2020_12_schema_accepts_canonical_instance(self) -> None:
        schema = _load_json(SCHEMA_PATH)
        instance = _load_json(CONTRACT_PATH)

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(_validate(instance, schema, schema, "$"), [])

        invalid = json.loads(json.dumps(instance))
        invalid["deadlines"]["client_seconds"] = 76
        self.assertTrue(_validate(invalid, schema, schema, "$"))

    def test_python_and_node_consume_the_same_contract_bytes(self) -> None:
        raw = CONTRACT_PATH.read_bytes()
        expected_hash = hashlib.sha256(raw).hexdigest()
        binding = self._node_json(
            "const m=require(process.argv[1]);process.stdout.write(JSON.stringify(m.runtimeBinding()))",
            str(NODE_HELPER),
        )

        self.assertEqual(ordinary_route_contract_sha256(), expected_hash)
        self.assertEqual(binding["contract_sha256"], expected_hash)
        self.assertEqual(binding["deadlines"], self.contract["deadlines"])
        self.assertEqual(binding["public_surfaces"], self.contract["public_surfaces"])
        self.assertEqual(binding["readiness"], self.contract["readiness"])
        self.assertFalse(binding["raw_private_publication_flags"])

    def test_launcher_and_thought_core_validate_or_consume_contract_authority(self) -> None:
        launcher = (REPO_ROOT / "tools" / "home-control-launcher" / "server.js").read_text(
            encoding="utf-8"
        )
        server = (
            REPO_ROOT / "services" / "thought-core" / "src" / "thought_core" / "server.py"
        ).read_text(encoding="utf-8")

        self.assertIn("require('./ordinary-route-contract')", launcher)
        self.assertIn("assertLauncherRuntimeAlignment", launcher)
        self.assertIn("ORDINARY_ROUTE_PUBLIC_SURFACES.status.path", launcher)
        self.assertIn("ORDINARY_ROUTE_PUBLIC_SURFACES.state.path", launcher)
        self.assertIn("max_route_deadline_seconds()", server)
        self.assertIn("route_deadline_header()", server)
        self.assertNotIn('MAX_ROUTE_DEADLINE_SECONDS = 75.0', server)

    def test_launcher_public_contract_endpoint_serves_the_same_safe_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            process = subprocess.Popen(
                [
                    self.node,
                    str(REPO_ROOT / "tools" / "home-control-launcher" / "server.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workspace",
                    temp_dir,
                    "--state-dir",
                    str(Path(temp_dir) / "state"),
                ],
                cwd=REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                payload = None
                for _ in range(40):
                    if process.poll() is not None:
                        self.fail("launcher contract fixture exited before readiness")
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/ordinary-route-contract",
                            timeout=0.25,
                        ) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        break
                    except OSError:
                        time.sleep(0.05)
                self.assertIsNotNone(payload)
                assert isinstance(payload, dict)
                self.assertEqual(payload["contract"], self.contract)
                self.assertEqual(payload["contract_sha256"], ordinary_route_contract_sha256())
                self.assertFalse(payload["raw_private_publication_flags"])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_standard_readiness_names_match_launcher_profile_logic(self) -> None:
        expected = [
            "home_assistant_bridge",
            "environment_state_server",
            "mediapipe",
            "vision_snapshot_processor",
            "aituber_kit",
            "touchdesigner_control_gui",
            "thought_core_api",
            "thought_core_watcher",
            "voicevox",
        ]
        self.assertEqual(self.contract["readiness"]["expected_service_ids"], expected)

    def test_budget_formulas_are_derived_and_abort_before_request_after_ceiling(self) -> None:
        budget = self._node_json(
            "const m=require(process.argv[1]);process.stdout.write(JSON.stringify(m.deriveBudget(['conversation','confirmed_action','single_decision_action'])))",
            str(NODE_HELPER),
        )

        self.assertEqual(
            budget,
            {
                "user_turns": 4,
                "max_provider_requests": 5,
                "abort_request_number": 6,
                "retry_count": 0,
            },
        )
        for flow in self.contract["turn_accounting"]["flows"].values():
            self.assertEqual(
                flow["abort_request_number"],
                flow["max_provider_requests"] + 1,
            )

    def test_review_checkpoint_mapping_is_fixed_and_ordered(self) -> None:
        tracking = {
            "action_id": "door_close",
            "target": "door",
            "expected_state": "closed",
        }
        matched = {
            "action_id": "door_close",
            "status": "matched",
            "state_tracking": "tracked",
            "verification_mode": "ha_state",
            "state_authority": "home_assistant",
        }

        self.assertEqual(review_checkpoint_class(None, None), "not_checked")
        self.assertEqual(review_checkpoint_class(tracking, None), "pending")
        self.assertEqual(review_checkpoint_class(tracking, None, unavailable=True), "unavailable")
        self.assertEqual(review_checkpoint_class(tracking, matched), "matched")

        cases = {
            "action_id_mismatch": {**matched, "action_id": "door_open"},
            "status_not_matched": {**matched, "status": "unavailable"},
            "tracking_not_tracked": {**matched, "state_tracking": "untracked"},
            "verification_mode_mismatch": {**matched, "verification_mode": "external"},
            "authority_mismatch": {**matched, "state_authority": "unknown"},
        }
        for expected, payload in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(review_checkpoint_class(tracking, payload), expected)
        self.assertEqual(
            review_checkpoint_payload("deadline")["review_checkpoint_class"],
            "deadline",
        )

    def test_http_adapter_retains_delayed_match_unavailable_and_open_close_classes(self) -> None:
        for expected_state in ("open", "closed"):
            with self.subTest(expected_state=expected_state):
                turn = TurnInput.from_mapping(
                    {
                        "text": "confirm",
                        "turn_id": f"turn_{expected_state}",
                        "session_id": "session_route_contract",
                        "locale": "ja-JP",
                        "context_refs": {},
                    }
                )
                tools = HomeControlHttpTools(
                    HomeControlToolConfig(
                        bridge_base_url="http://127.0.0.1:8787",
                        api_token="fixture-token",
                    )
                )
                tracking = {
                    "action_id": f"door_{'open' if expected_state == 'open' else 'close'}",
                    "target": "door",
                    "expected_state": expected_state,
                }
                tools.tracked_state_check_by_turn[turn.turn_id] = tracking
                delayed = {
                    "action_id": tracking["action_id"],
                    "status": "pending",
                    "state_tracking": "tracked",
                    "verification_mode": "ha_state",
                    "state_authority": "home_assistant",
                }
                matched = {**delayed, "status": "matched"}
                with patch.object(tools, "_json_request", side_effect=[delayed, matched]):
                    self.assertIsNone(tools._matched_tracked_state_fact(turn))
                    self.assertEqual(
                        tools.tracked_state_checkpoint_by_turn[turn.turn_id],
                        "status_not_matched",
                    )
                    self.assertIsNotNone(tools._matched_tracked_state_fact(turn))
                    self.assertEqual(
                        tools.tracked_state_checkpoint_by_turn[turn.turn_id],
                        "matched",
                    )

                with patch.object(
                    tools,
                    "_json_request",
                    side_effect=HomeControlToolError("bridge_unavailable", "fixed"),
                ):
                    self.assertIsNone(tools._matched_tracked_state_fact(turn))
                    self.assertEqual(
                        tools.tracked_state_checkpoint_by_turn[turn.turn_id],
                        "unavailable",
                    )

    def test_review_event_retains_only_fixed_checkpoint_class(self) -> None:
        loop = ThoughtLoop(tools=MockThoughtTools())
        review = loop._review_action_result(
            turn_input=type("Turn", (), {"text": ""})(),  # unused by this branch
            action={"action_id": "door_close", "expected_state": "closed"},
            observation={
                "review_checkpoint": review_checkpoint_payload("authority_mismatch"),
                "facts": {"devices": []},
            },
            execute_result={"status": "accepted", "executed": True},
        )

        self.assertEqual(review["review_checkpoint_class"], "authority_mismatch")
        self.assertNotIn("state_authority", review)
        self.assertNotIn("entity", review)

    def test_nonmatched_canonical_checkpoint_blocks_incidental_state_success(self) -> None:
        loop = ThoughtLoop(tools=MockThoughtTools())
        action = {
            "action_id": "door_close",
            "target": "door",
            "expected_state": "closed",
        }
        observation = {
            "facts": {
                "devices": [
                    {
                        "id": "door",
                        "state": "closed",
                        "stale": False,
                    }
                ]
            }
        }
        execute_result = {"status": "accepted", "executed": True}
        blocking_classes = [
            "pending",
            "action_id_mismatch",
            "status_not_matched",
            "tracking_not_tracked",
            "verification_mode_mismatch",
            "authority_mismatch",
            "unavailable",
            "deadline",
        ]

        for checkpoint_class in blocking_classes:
            with self.subTest(checkpoint_class=checkpoint_class):
                review = loop._review_action_result(
                    turn_input=type("Turn", (), {"text": ""})(),
                    action=action,
                    observation={
                        **observation,
                        "review_checkpoint": review_checkpoint_payload(checkpoint_class),
                    },
                    execute_result=execute_result,
                )
                self.assertEqual(review["status"], "pending")
                self.assertEqual(
                    review["reason"],
                    "canonical_review_checkpoint_not_matched",
                )
                self.assertEqual(review["review_checkpoint_class"], checkpoint_class)

        matched_review = loop._review_action_result(
            turn_input=type("Turn", (), {"text": ""})(),
            action=action,
            observation={
                **observation,
                "review_checkpoint": review_checkpoint_payload("matched"),
            },
            execute_result=execute_result,
        )
        self.assertEqual(matched_review["status"], "succeeded")
        self.assertEqual(matched_review["review_checkpoint_class"], "matched")

        invalid_review = loop._review_action_result(
            turn_input=type("Turn", (), {"text": ""})(),
            action=action,
            observation={
                **observation,
                "review_checkpoint": {"review_checkpoint_class": "raw_unknown"},
            },
            execute_result=execute_result,
        )
        self.assertEqual(invalid_review["status"], "pending")
        self.assertEqual(invalid_review["review_checkpoint_class"], "unavailable")

    def test_declared_canonical_tracking_requires_matched_checkpoint(self) -> None:
        loop = ThoughtLoop(tools=MockThoughtTools())
        observation = {
            "facts": {
                "devices": [
                    {
                        "id": "door",
                        "state": "closed",
                        "stale": False,
                    }
                ]
            },
            "review_checkpoint": review_checkpoint_payload("not_checked"),
        }
        base_action = {
            "action_id": "door_close",
            "target": "door",
            "expected_state": "closed",
        }
        base_execute = {"status": "accepted", "executed": True}
        declarations = [
            ({"state_tracking": "tracked"}, {}),
            ({"verification_mode": "ha_state"}, {}),
            ({"state_authority": "home_assistant"}, {}),
            ({}, {"state_tracking": "tracked"}),
            ({}, {"verification_mode": "ha_state"}),
            ({}, {"state_authority": "ha_entity"}),
        ]

        for action_fields, execute_fields in declarations:
            with self.subTest(action=action_fields, execute=execute_fields):
                action = {**base_action, **action_fields}
                execute_result = {**base_execute, **execute_fields}
                self.assertTrue(
                    canonical_review_match_required(action, execute_result)
                )
                review = loop._review_action_result(
                    turn_input=type("Turn", (), {"text": ""})(),
                    action=action,
                    observation=observation,
                    execute_result=execute_result,
                )
                self.assertEqual(review["status"], "pending")
                self.assertEqual(
                    review["reason"],
                    "canonical_review_checkpoint_not_matched",
                )
                self.assertEqual(review["review_checkpoint_class"], "not_checked")

        self.assertFalse(canonical_review_match_required(base_action, base_execute))
        compatible_review = loop._review_action_result(
            turn_input=type("Turn", (), {"text": ""})(),
            action=base_action,
            observation=observation,
            execute_result=base_execute,
        )
        self.assertEqual(compatible_review["status"], "succeeded")

    def test_binding_validation_aborts_on_stale_contract(self) -> None:
        binding = self._node_json(
            "const m=require(process.argv[1]);process.stdout.write(JSON.stringify(m.runtimeBinding()))",
            str(NODE_HELPER),
        )
        stale = json.loads(json.dumps(binding))
        stale["deadlines"]["client_seconds"] = 73

        script = (
            "const m=require(process.argv[1]);"
            "const v=JSON.parse(process.argv[2]);"
            "try{m.validateRuntimeBinding(v);process.stdout.write('unexpected')}"
            "catch(e){process.stdout.write(e.message)}"
        )
        result = self._node_text(script, str(NODE_HELPER), json.dumps(stale))
        self.assertEqual(result, "ordinary_route_binding_drift")

    def test_result_finalization_preserves_phase_on_cleanup_failure_and_is_idempotent(self) -> None:
        provisional = {
            "flow_id": "confirmed_action",
            "phase_class": "turn_completed",
            "user_turn_count": 2,
            "provider_request_count": 3,
            "bridge_submission_count": 1,
            "correlated_receipt_count": 1,
            "success_presentation_count": 1,
            "review_checkpoint_class": "matched",
            "fixed_result_class": "success",
        }
        script = (
            "const m=require(process.argv[1]);"
            "const p=JSON.parse(process.argv[2]);"
            "const first=m.finalizeResult(p,'cleanup_degraded');"
            "const second=m.finalizeResult(first,'cleanup_degraded');"
            "process.stdout.write(JSON.stringify({first,second,valid:m.validateFinalResult(second)}))"
        )
        result = self._node_json(script, str(NODE_HELPER), json.dumps(provisional))

        self.assertEqual(result["first"], result["second"])
        self.assertEqual(result["first"]["finalization_class"], "result_partial")
        self.assertEqual(result["first"]["cleanup_class"], "cleanup_degraded")
        self.assertEqual(result["first"]["provider_request_count"], 3)
        self.assertTrue(result["valid"])

    def test_complete_result_requires_stop_and_clear_cleanup(self) -> None:
        provisional = {
            "flow_id": "conversation",
            "phase_class": "stopped",
            "user_turn_count": 1,
            "provider_request_count": 1,
            "bridge_submission_count": 0,
            "correlated_receipt_count": 0,
            "success_presentation_count": 0,
            "review_checkpoint_class": "not_checked",
        }
        result = self._node_json(
            "const m=require(process.argv[1]);process.stdout.write(JSON.stringify(m.finalizeResult(JSON.parse(process.argv[2]),'cleanup_clear')))",
            str(NODE_HELPER),
            json.dumps(provisional),
        )
        self.assertEqual(result["finalization_class"], "result_complete")

    def test_result_rejects_raw_fields_and_success_before_receipt(self) -> None:
        base = {
            "flow_id": "confirmed_action",
            "phase_class": "turn_completed",
            "user_turn_count": 2,
            "provider_request_count": 3,
            "bridge_submission_count": 1,
            "correlated_receipt_count": 0,
            "success_presentation_count": 0,
            "review_checkpoint_class": "pending",
        }
        script = (
            "const m=require(process.argv[1]);"
            "try{m.finalizeResult(JSON.parse(process.argv[2]),'cleanup_clear');process.stdout.write('unexpected')}"
            "catch(e){process.stdout.write(e.message)}"
        )
        raw = {**base, "stdout": "PRIVATE_SENTINEL"}
        self.assertEqual(
            self._node_text(script, str(NODE_HELPER), json.dumps(raw)),
            "ordinary_route_result_field_rejected",
        )
        premature = {**base, "success_presentation_count": 1}
        self.assertEqual(
            self._node_text(script, str(NODE_HELPER), json.dumps(premature)),
            "ordinary_route_receipt_order_invalid",
        )
        stale = {**base, "contract_sha256": "0" * 64}
        self.assertEqual(
            self._node_text(script, str(NODE_HELPER), json.dumps(stale)),
            "ordinary_route_result_contract_drift",
        )

    def test_cli_binding_and_result_round_trip_is_fixed_output_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            binding_path = root / "binding.json"
            result_input = root / "result-input.json"
            result_output = root / "result.json"
            binding = self._node_json(
                "const m=require(process.argv[1]);process.stdout.write(JSON.stringify(m.runtimeBinding()))",
                str(NODE_HELPER),
            )
            binding_path.write_text(json.dumps(binding), encoding="utf-8")
            result_input.write_text(
                json.dumps(
                    {
                        "flow_id": "conversation",
                        "phase_class": "stopped",
                        "user_turn_count": 1,
                        "provider_request_count": 1,
                        "bridge_submission_count": 0,
                        "correlated_receipt_count": 0,
                        "success_presentation_count": 0,
                        "review_checkpoint_class": "not_checked",
                    }
                ),
                encoding="utf-8",
            )

            validated = subprocess.run(
                [self.node, str(NODE_HELPER), "--validate-binding", str(binding_path)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            finalized = subprocess.run(
                [
                    self.node,
                    str(NODE_HELPER),
                    "--finalize-result",
                    str(result_input),
                    str(result_output),
                    "cleanup_clear",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            finalized_again = subprocess.run(
                [
                    self.node,
                    str(NODE_HELPER),
                    "--finalize-result",
                    str(result_input),
                    str(result_output),
                    "cleanup_clear",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            result_validated = subprocess.run(
                [self.node, str(NODE_HELPER), "--validate-result", str(result_output)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(validated.stdout.strip(), "ordinary_route_binding_valid")
            self.assertEqual(finalized.stdout.strip(), "ordinary_route_result_finalized")
            self.assertEqual(finalized_again.stdout.strip(), "ordinary_route_result_finalized")
            self.assertEqual(result_validated.stdout.strip(), "ordinary_route_result_valid")
            self.assertEqual(_load_json(result_output)["finalization_class"], "result_complete")

    def _node_text(self, script: str, *args: str) -> str:
        completed = subprocess.run(
            [self.node, "-e", script, *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def _node_json(self, script: str, *args: str) -> dict[str, Any]:
        value = json.loads(self._node_text(script, *args))
        self.assertIsInstance(value, dict)
        return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain an object")
    return value


def _validate(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    field_path: str,
) -> list[str]:
    if "$ref" in schema:
        return _validate(value, _resolve_local_ref(schema["$ref"], root_schema), root_schema, field_path)
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{field_path}: const")
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{field_path}: enum")
    expected_type = schema.get("type")
    if expected_type and not _matches_type(value, expected_type):
        errors.append(f"{field_path}: type")
        return errors
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{field_path}: missing {key}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{field_path}: extra {key}")
        for key, item_schema in properties.items():
            if key in value:
                errors.extend(_validate(value[key], item_schema, root_schema, f"{field_path}.{key}"))
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{field_path}: minItems")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errors.append(f"{field_path}: maxItems")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            errors.append(f"{field_path}: uniqueItems")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, schema["items"], root_schema, f"{field_path}[{index}]"))
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{field_path}: minLength")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{field_path}: maxLength")
        if isinstance(schema.get("pattern"), str) and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{field_path}: pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            errors.append(f"{field_path}: minimum")
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            errors.append(f"{field_path}: maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and value <= schema["exclusiveMinimum"]:
            errors.append(f"{field_path}: exclusiveMinimum")
    return errors


def _resolve_local_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise AssertionError(f"unsupported ref: {ref}")
    value: Any = root_schema
    for token in ref[2:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise AssertionError(f"schema ref is not an object: {ref}")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]
