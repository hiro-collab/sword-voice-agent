import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "電気つけて",
    "turn_id": "turn_schema_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "context_refs": {
        "environment_snapshot": "env_abc123",
        "voice_turn": "voice_789",
    },
}

ENVIRONMENT_CURRENT = {
    "schema_version": 1,
    "snapshot_id": "env_001",
    "sequence": 1,
    "stale": False,
    "age_ms": 12,
    "capabilities": {"actions": True},
    "actions": [{"action_id": "light_on", "label": "照明をつける"}],
    "state_queries": {
        "room_light": {
            "available": True,
            "state": "on",
            "authority": "vision_snapshot_processor",
        }
    },
    "wait_result": {
        "target": "room_light",
        "matched": True,
        "after": "2026-05-07T14:15:00+09:00",
        "timeout_ms": 1500,
        "observed_at": "2026-05-07T14:15:00.320000+09:00",
        "elapsed_ms": 320,
        "reason": "matched",
    },
}

STATE_QUERY_FEEDBACK_REQUEST = {
    "type": "state_query_feedback",
    "target": "room_light",
    "state_query_id": "room_light",
    "idempotency_key": "state-query-feedback:conversation:snapshot:on",
    "snapshot_id": "env_001",
    "predicted_state": "unknown",
    "predicted_confidence_label": "low",
    "user_label": "on",
    "user_text": "ついてるよ",
    "authority": "user_feedback",
    "source": "dify",
    "feedback_reason": "user_correction_after_state_query",
}

STATE_QUERY_FEEDBACK_RESPONSE = {
    "ok": True,
    "feedback_id": "sqf_001",
    "received_snapshot_id": "env_001",
    "duplicate": False,
    "status": "accepted",
    "warnings": [],
}

HOME_CONTROL_REQUEST = {
    "source": "dify",
    "request_id": "req-001",
    "user_text": "照明をつけて",
}

HOME_CONTROL_PREVIEW_RESULT = {
    "ok": True,
    "action_id": "light_on",
    "executed": False,
    "preview": {
        "ha_service": "script.turn_on",
        "ha_script": "script.demo_light_on",
    },
}

HOME_CONTROL_EXECUTE_RESULT = {
    "ok": True,
    "action_id": "light_on",
    "execution_id": "2c9f9f6a-1f4b-43aa-89ef-4e1c7c73f9d2",
    "request_id": "req-001",
    "issued_at": "2026-05-07T14:15:00+00:00",
    "status": "submitted",
    "executed": True,
    "domain": "light",
    "service": "turn_on",
    "entity_id": "light.demo_room",
    "expected_state": "on",
    "expected_effect": {
        "domain": "light",
        "service": "turn_on",
        "entity_id": "light.demo_room",
        "expected_state": "on",
    },
}

LAYERED_EVENT = {
    "schema_version": "thought-core.event.v0",
    "event_id": "evt_layer_001",
    "turn_id": "turn_schema_001",
    "session_id": "living_room_main",
    "seq": 1,
    "timestamp": "2026-05-08T00:00:00Z",
    "source": "thought-core",
    "layer": "turn",
    "type": "assistant.message",
    "data": {"speech": "了解です。"},
}

TTS_SPEECH_CHUNK = {
    "event": "assistant.speech_delta",
    "delta": "了解、",
    "final": False,
    "turn_id": "turn_schema_001",
    "message_id": "evt_schema_001",
    "conversation_id": "turn_schema_001",
    "elapsed_s": 0.12,
}

TTS_FINAL_CHUNK = {
    "event": "turn.completed",
    "final": True,
    "turn_id": "turn_schema_001",
    "message_id": "evt_schema_done",
    "conversation_id": "turn_schema_001",
}

AITUBER_MESSAGE = {
    "messages": ["了解、電気をつけるね。"],
}

GESTURE_STATE = {
    "type": "gesture_state",
    "source": "mediapipe_camera_hub",
    "timestamp": 1778200000.0,
    "gestures": {
        "sword_sign": {
            "active": True,
            "confidence": 0.95,
        },
        "victory": {
            "active": False,
            "confidence": 0.1,
        },
    },
}

GESTURE_RECEIVER_STATUS = {
    "type": "gesture_receiver_status",
    "timestamp": 1778200000.0,
    "sequence": 1,
    "from": "127.0.0.1:50000",
    "response": {
        "voice_state": {
            "type": "voice_state",
            "timestamp": 1778200000.0,
            "phase": "armed",
            "mic_enabled": True,
            "recording": True,
        },
        "gate_decision": {
            "timestamp": 1778200000.0,
            "gesture_name": "sword_sign",
            "raw_active": True,
            "confidence": 0.95,
            "mic_enabled": True,
            "changed": False,
            "reason": "stable",
        },
        "voice_control_command": {
            "type": "voice_control_command",
            "timestamp": 1778200000.0,
            "action": "start_recording",
            "mic_enabled": True,
            "reason": "stable",
            "turn_id": "turn_schema_001",
        },
    },
}

GESTURE_DIAGNOSTIC_STATUS = {
    "type": "gesture_receiver_status",
    "timestamp": 1778200000.0,
    "sequence": 3,
    "from": "127.0.0.1:50000",
    "response": {
        "diagnostic": {
            "type": "gesture_status",
            "status": "running",
            "frame_id": 10,
            "fps": 30.0,
            "hand_detected": True,
            "primary_gesture": "sword_sign",
            "sword_sign": {
                "active": True,
                "confidence": 0.91,
            },
            "best_gesture": {
                "name": "sword_sign",
                "confidence": 0.91,
            },
            "camera": {
                "opened": True,
            },
        },
    },
}

REFLEX_STATUS_EVENT = {
    "event_id": "evt-reflex-schema-001",
    "type": "gesture.received",
    "timestamp": 1778200000.0,
    "source": "gesture_udp_receiver",
    "turn_id": "turn_schema_001",
    "payload": GESTURE_RECEIVER_STATUS,
}

SYSTEM_EVENT = {
    "schema_version": "system.event.v0",
    "event_id": "evt_system_schema_001",
    "ts": "2026-05-08T12:00:00+09:00",
    "trace_id": "trace_schema_001",
    "turn_id": "turn_schema_001",
    "service": "thought-core",
    "layer": "turn",
    "event": "tool.started",
    "level": "info",
    "payload": {
        "tool": "environment.observe",
        "tool_call_id": "tc_schema_001",
    },
}

MEMORY_ITEM = {
    "schema_version": "memory.item.v0",
    "memory_id": "mcand_schema_001",
    "memory_type": "failure_pattern",
    "scope": "failure_patterns",
    "status": "candidate",
    "content": {
        "pattern": "room_light observation may lag after light_off",
        "recommended_wait_ms": 2000,
    },
    "source": {
        "service": "thought-core",
        "trace_id": "trace_schema_001",
        "turn_id": "turn_schema_001",
        "event_id": "evt_system_schema_001",
    },
    "confidence": 0.82,
    "requires_user_confirmation": False,
    "created_at": "2026-05-08T12:00:01+09:00",
}

MEMORY_RETRIEVE_REQUEST = {
    "schema_version": "memory.retrieve.request.v0",
    "request_id": "memreq_schema_001",
    "requester": "svc.thought-core",
    "scopes": ["failure_patterns", "user_preferences"],
    "query": {
        "text": "電気を消して",
        "action_id": "light_off",
    },
    "limit": 5,
    "trace_id": "trace_schema_001",
    "turn_id": "turn_schema_001",
}

MEMORY_RETRIEVE_RESULT = {
    "schema_version": "memory.retrieve.result.v0",
    "request_id": "memreq_schema_001",
    "ok": True,
    "items": [MEMORY_ITEM],
    "warnings": [],
}

MEMORY_WRITE_CANDIDATE_REQUEST = {
    "schema_version": "memory.write_candidate.request.v0",
    "request_id": "memcandreq_schema_001",
    "requester": "svc.thought-core",
    "candidate": MEMORY_ITEM,
}

MEMORY_WRITE_CANDIDATE_RESULT = {
    "schema_version": "memory.write_candidate.result.v0",
    "request_id": "memcandreq_schema_001",
    "ok": True,
    "candidate_id": "mcand_schema_001",
    "status": "accepted",
    "warnings": [],
}

MEMORY_COMMIT_REQUEST = {
    "schema_version": "memory.commit.request.v0",
    "request_id": "memcommitreq_schema_001",
    "requester": "svc.memory-core",
    "candidate_id": "mcand_schema_001",
    "decision": "commit",
    "reason": "policy accepted failure pattern",
}

MEMORY_COMMIT_RESULT = {
    "schema_version": "memory.commit.result.v0",
    "request_id": "memcommitreq_schema_001",
    "ok": True,
    "memory_id": "mem_schema_001",
    "status": "committed",
}

AUTHORIZATION_REQUEST = {
    "schema_version": "access.authorization.request.v0",
    "request_id": "authreq_schema_001",
    "subject": "svc.thought-core",
    "capability": "memory.write.candidate",
    "resource": {
        "type": "memory_scope",
        "scope": "failure_patterns",
    },
    "context": {
        "trace_id": "trace_schema_001",
    },
}

AUTHORIZATION_DECISION = {
    "schema_version": "access.authorization.decision.v0",
    "request_id": "authreq_schema_001",
    "allowed": True,
    "reason": "subject has memory.write.candidate",
    "decided_by": "policy.access.v0",
    "audit_required": True,
}

ACCESS_AUDIT_EVENT = {
    "schema_version": "access.audit.event.v0",
    "event_id": "evt_access_schema_001",
    "ts": "2026-05-08T12:00:02+09:00",
    "subject": "svc.thought-core",
    "capability": "memory.write.candidate",
    "resource": {
        "type": "memory_scope",
        "scope": "failure_patterns",
    },
    "decision": "allowed",
    "reason": "candidate write is allowed",
}


class ContractSchemaTest(TestCase):
    def test_contract_json_files_are_valid_json(self) -> None:
        for path in sorted((REPO_ROOT / "contracts").rglob("*.json")):
            with self.subTest(path=path):
                self.assertIsInstance(_load_json(path), dict)

    def test_turn_request_schema_accepts_current_turn_shape(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "turn" / "turn-request.schema.json"
        errors = validate_schema(TURN, schema_path)
        self.assertEqual(errors, [])

    def test_turn_request_schema_rejects_required_field_gaps(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "turn" / "turn-request.schema.json"

        missing_text = {key: value for key, value in TURN.items() if key != "text"}
        self.assertTrue(validate_schema(missing_text, schema_path))

        blank_turn = {**TURN, "turn_id": "   "}
        self.assertTrue(validate_schema(blank_turn, schema_path))

        bad_context_refs = {**TURN, "context_refs": []}
        self.assertTrue(validate_schema(bad_context_refs, schema_path))

    def test_event_schema_accepts_current_thought_core_events(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "events" / "event.schema.json"
        events = current_test_events()

        self.assertGreater(len(events), 0)
        for event in events:
            with self.subTest(event_type=event["type"]):
                self.assertEqual(validate_schema(event, schema_path), [])

    def test_event_schema_accepts_layer_metadata(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "events" / "event.schema.json"

        self.assertEqual(validate_schema(LAYERED_EVENT, schema_path), [])
        self.assertTrue(validate_schema({**LAYERED_EVENT, "layer": "unknown"}, schema_path))

    def test_system_event_schema_accepts_journal_payload(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "events" / "system-event.schema.json"

        self.assertEqual(validate_schema(SYSTEM_EVENT, schema_path), [])
        self.assertTrue(validate_schema({**SYSTEM_EVENT, "layer": "unknown"}, schema_path))

    def test_tool_schemas_accept_current_thought_core_tool_events(self) -> None:
        call_schema = REPO_ROOT / "contracts" / "tools" / "tool-call.schema.json"
        result_schema = REPO_ROOT / "contracts" / "tools" / "tool-result.schema.json"
        pending: dict[str, str] = {}

        events = current_test_events()
        self.assertTrue(any(event["type"] == "tool.started" for event in events))

        for event in events:
            data = event["data"]
            if event["type"] == "tool.started":
                self.assertEqual(validate_schema(data, call_schema), [])
                pending[data["tool_call_id"]] = data["tool"]
            if event["type"] == "tool.result":
                self.assertEqual(validate_schema(data, result_schema), [])
                self.assertEqual(pending.pop(data["tool_call_id"]), data["tool"])

        self.assertEqual(pending, {})

    def test_turn_response_events_schema_accepts_current_response_shape(self) -> None:
        schema_path = (
            REPO_ROOT
            / "contracts"
            / "turn"
            / "turn-response-events.schema.json"
        )
        response = {"events": current_test_events()}

        self.assertEqual(validate_schema(response, schema_path), [])

    def test_environment_schemas_accept_representative_payloads(self) -> None:
        current_schema = (
            REPO_ROOT
            / "contracts"
            / "environment"
            / "environment-current.schema.json"
        )
        feedback_request_schema = (
            REPO_ROOT
            / "contracts"
            / "environment"
            / "state-query-feedback-request.schema.json"
        )
        feedback_response_schema = (
            REPO_ROOT
            / "contracts"
            / "environment"
            / "state-query-feedback-response.schema.json"
        )

        self.assertEqual(validate_schema(ENVIRONMENT_CURRENT, current_schema), [])
        self.assertEqual(
            validate_schema(STATE_QUERY_FEEDBACK_REQUEST, feedback_request_schema),
            [],
        )
        self.assertEqual(
            validate_schema(STATE_QUERY_FEEDBACK_RESPONSE, feedback_response_schema),
            [],
        )

    def test_home_control_schemas_accept_representative_payloads(self) -> None:
        request_schema = (
            REPO_ROOT / "contracts" / "home-control" / "action-request.schema.json"
        )
        preview_schema = (
            REPO_ROOT / "contracts" / "home-control" / "preview-result.schema.json"
        )
        execute_schema = (
            REPO_ROOT / "contracts" / "home-control" / "execute-result.schema.json"
        )

        self.assertEqual(validate_schema(HOME_CONTROL_REQUEST, request_schema), [])
        self.assertEqual(validate_schema(HOME_CONTROL_PREVIEW_RESULT, preview_schema), [])
        self.assertEqual(validate_schema(HOME_CONTROL_EXECUTE_RESULT, execute_schema), [])

    def test_home_control_request_schema_rejects_adapter_escape_fields(self) -> None:
        request_schema = (
            REPO_ROOT / "contracts" / "home-control" / "action-request.schema.json"
        )
        unsafe_request = {
            **HOME_CONTROL_REQUEST,
            "ha_script": "script.not_allowed",
            "entity_id": "lock.front_door",
        }

        self.assertTrue(validate_schema(unsafe_request, request_schema))

    def test_expression_schemas_accept_current_adapter_payloads(self) -> None:
        speech_schema = (
            REPO_ROOT / "contracts" / "expression" / "speech-chunk.schema.json"
        )
        aituber_schema = (
            REPO_ROOT / "contracts" / "expression" / "aituber-message.schema.json"
        )

        self.assertEqual(validate_schema(TTS_SPEECH_CHUNK, speech_schema), [])
        self.assertEqual(validate_schema(TTS_FINAL_CHUNK, speech_schema), [])
        self.assertEqual(validate_schema(AITUBER_MESSAGE, aituber_schema), [])

    def test_expression_message_schema_rejects_empty_chunks(self) -> None:
        aituber_schema = (
            REPO_ROOT / "contracts" / "expression" / "aituber-message.schema.json"
        )

        self.assertTrue(validate_schema({"messages": []}, aituber_schema))
        self.assertTrue(validate_schema({"messages": ["   "]}, aituber_schema))

    def test_reflex_schemas_accept_current_gesture_payloads(self) -> None:
        state_schema = REPO_ROOT / "contracts" / "reflex" / "gesture-state.schema.json"
        receiver_schema = (
            REPO_ROOT / "contracts" / "reflex" / "gesture-receiver-status.schema.json"
        )
        diagnostic_schema = (
            REPO_ROOT / "contracts" / "reflex" / "gesture-diagnostic-status.schema.json"
        )
        event_schema = (
            REPO_ROOT / "contracts" / "reflex" / "reflex-status-event.schema.json"
        )

        self.assertEqual(validate_schema(GESTURE_STATE, state_schema), [])
        self.assertEqual(validate_schema(GESTURE_RECEIVER_STATUS, receiver_schema), [])
        self.assertEqual(validate_schema(GESTURE_DIAGNOSTIC_STATUS, diagnostic_schema), [])
        self.assertEqual(validate_schema(REFLEX_STATUS_EVENT, event_schema), [])

    def test_reflex_gesture_state_rejects_invalid_confidence(self) -> None:
        state_schema = REPO_ROOT / "contracts" / "reflex" / "gesture-state.schema.json"
        invalid = {
            **GESTURE_STATE,
            "gestures": {
                "sword_sign": {
                    "active": True,
                    "confidence": 1.5,
                }
            },
        }

        self.assertTrue(validate_schema(invalid, state_schema))

    def test_memory_schemas_accept_representative_payloads(self) -> None:
        memory_dir = REPO_ROOT / "contracts" / "memory"

        for filename, payload in {
            "memory-item.schema.json": MEMORY_ITEM,
            "retrieve-request.schema.json": MEMORY_RETRIEVE_REQUEST,
            "retrieve-result.schema.json": MEMORY_RETRIEVE_RESULT,
            "write-candidate-request.schema.json": MEMORY_WRITE_CANDIDATE_REQUEST,
            "write-candidate-result.schema.json": MEMORY_WRITE_CANDIDATE_RESULT,
            "commit-request.schema.json": MEMORY_COMMIT_REQUEST,
            "commit-result.schema.json": MEMORY_COMMIT_RESULT,
        }.items():
            with self.subTest(filename=filename):
                self.assertEqual(validate_schema(payload, memory_dir / filename), [])

    def test_memory_candidate_rejects_secret_scope(self) -> None:
        schema_path = REPO_ROOT / "contracts" / "memory" / "memory-item.schema.json"
        secret_candidate = {
            **MEMORY_ITEM,
            "scope": "secrets",
        }

        self.assertTrue(validate_schema(secret_candidate, schema_path))

    def test_access_control_schemas_accept_representative_payloads(self) -> None:
        access_dir = REPO_ROOT / "contracts" / "access-control"

        for filename, payload in {
            "authorization-request.schema.json": AUTHORIZATION_REQUEST,
            "authorization-decision.schema.json": AUTHORIZATION_DECISION,
            "audit-event.schema.json": ACCESS_AUDIT_EVENT,
        }.items():
            with self.subTest(filename=filename):
                self.assertEqual(validate_schema(payload, access_dir / filename), [])


def validate_schema(value: Any, schema_path: Path) -> list[str]:
    schema = _load_json(schema_path)
    return _validate(value, schema, schema_path.parent, "$")


def current_test_events() -> list[dict[str, Any]]:
    return ThoughtLoop(tools=MockThoughtTools()).run_dicts(TURN)


def _validate(value: Any, schema: dict[str, Any], base_dir: Path, path: str) -> list[str]:
    if "$ref" in schema:
        ref_schema, ref_base_dir = _resolve_ref(schema["$ref"], base_dir)
        return _validate(value, ref_schema, ref_base_dir, path)

    errors: list[str] = []

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
        return errors

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        errors.append(f"{path}: expected type {expected_type}")
        return errors

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")

        min_properties = schema.get("minProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            errors.append(f"{path}: expected minProperties {min_properties}")

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, property_schema in properties.items():
                if key in value:
                    errors.extend(
                        _validate(value[key], property_schema, base_dir, f"{path}.{key}")
                    )
            additional_properties = schema.get("additionalProperties")
            if additional_properties is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(additional_properties, dict):
                for key, item in value.items():
                    if key not in properties:
                        errors.extend(
                            _validate(
                                item,
                                additional_properties,
                                base_dir,
                                f"{path}.{key}",
                            )
                        )

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: expected minItems {min_items}")

        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, base_dir, f"{path}[{index}]"))

    if isinstance(value, str):
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: expected enum value")

        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: expected minLength {min_length}")

        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: expected pattern {pattern!r}")

        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{path}: expected date-time")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: expected minimum {minimum}")

        maximum = schema.get("maximum")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: expected maximum {maximum}")

    return errors


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise AssertionError(f"unsupported schema type: {expected_type}")


def _resolve_ref(ref: str, base_dir: Path) -> tuple[dict[str, Any], Path]:
    if ref.startswith("#"):
        raise AssertionError(f"local refs are not supported in this test: {ref}")
    ref_path = (base_dir / ref).resolve()
    return _load_json(ref_path), ref_path.parent


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value
