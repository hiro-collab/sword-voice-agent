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
    "text": "",
    "turn_id": "turn_motion_request_contract",
    "session_id": "motion_request_contract_session",
    "locale": "ja-JP",
    "context_refs": {},
}


class ThoughtCoreMotionRequestContractTest(TestCase):
    def test_dance_request_emits_motion_stimulus_without_home_action(self) -> None:
        events, tools = self._run("踊って")
        event = self._motion_event(events)
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        self.assertIsNotNone(event)
        assert payload is not None
        assert event is not None
        self.assertEqual(payload["schema_version"], "motion_stimulus.v0")
        self.assertEqual(payload["kind"], "dance_sequence")
        self.assertEqual(payload["source_class"], "user_command")
        self.assertEqual(payload["source_origin"], "thought_core")
        self.assertEqual(payload["request_mode"], "play")
        self.assertEqual(payload["phase"], "queued")
        self.assertEqual(payload["lifecycle_state"], "queued")
        self.assertEqual(payload["safe_visible_state"], "requested")
        self.assertEqual(payload["safe_display_name"], "Dance sequence")
        self.assertEqual(payload["target_model_type"], "vrm")
        self.assertTrue(payload["stimulus_id"].startswith("mot_stim_"))
        self.assertTrue(payload["motion_event_id"].startswith("mot_evt_"))
        self.assertTrue(payload["stimulus_instance_id"].startswith("mot_inst_"))
        self.assertEqual(payload["requested_at"], event["timestamp"])
        self.assertEqual(payload["trace"]["event_id"], event["event_id"])
        self.assertEqual(payload["trace"]["turn_id"], event["turn_id"])
        self.assertEqual(payload["trace"]["motion_event_id"], payload["motion_event_id"])
        self.assertEqual(payload["trace"]["stimulus_id"], payload["stimulus_id"])
        self.assertEqual(
            payload["trace"]["stimulus_instance_id"],
            payload["stimulus_instance_id"],
        )
        self.assertIn("body_root", payload["track_mask"])
        self.assertIn("spine", payload["requirements"]["required_tracks"])
        self.assertEqual(payload["redaction"]["proof_layer"], "source_static")
        self.assertEqual(payload["safety"]["raw_user_text_shared"], False)
        self.assertEqual(payload["safety"]["raw_prompt_shared"], False)
        self.assertEqual(payload["safety"]["raw_media_shared"], False)
        self.assertEqual(payload["safety"]["raw_path_shared"], False)
        self.assert_parent_required_fields(payload)
        self.assert_no_home_action(events, tools)

    def test_music_dance_request_carries_rhythm_hint_only(self) -> None:
        events, tools = self._run("音楽に合わせて踊って")
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["kind"], "dance_sequence")
        self.assertEqual(payload["safe_display_name"], "Music dance")
        self.assertEqual(payload["payload_ref"], "motion.thought_core.dance_sequence.v0")
        self.assertEqual(payload["safety"]["home_assistant_route"], False)
        self.assert_no_home_action(events, tools)

    def test_happy_motion_request_maps_to_expression_motion(self) -> None:
        events, tools = self._run("うれしそうに動いて")
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["kind"], "expression")
        self.assertEqual(payload["safe_display_name"], "Happy expression")
        self.assertEqual(payload["track_mask"], ["face", "head", "neck"])
        self.assertEqual(payload["requirements"]["required_tracks"], ["face"])
        self.assertEqual(payload["payload_ref"], "motion.thought_core.expression.v0")
        self.assertEqual(payload["safety"]["raw_user_text_shared"], False)
        self.assert_no_home_action(events, tools)

    def test_motion_payload_matches_local_parent_contract_schema(self) -> None:
        events, _tools = self._run("踊って")
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        assert payload is not None
        schema_path = (
            REPO_ROOT
            / "contracts"
            / "motion-stimulus"
            / "motion-stimulus.v0.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(_validate_schema(payload, schema), [])

    def test_home_action_does_not_emit_motion_request(self) -> None:
        events, tools = self._run("電気をつけて")

        self.assertIsNone(self._motion_payload(events))
        self.assertIn("action.proposed", [event["type"] for event in events])
        self.assertEqual(len(tools.execute_calls), 1)

    def assert_no_home_action(
        self,
        events: list[dict[str, object]],
        tools: MockThoughtTools,
    ) -> None:
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        self.assertNotIn("action.proposed", event_types)
        self.assertNotIn("home.preview", tool_names)
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tools.execute_calls, [])

    def assert_parent_required_fields(self, payload: dict[str, object]) -> None:
        required = {
            "schema_version",
            "motion_event_id",
            "stimulus_id",
            "stimulus_instance_id",
            "source_class",
            "source_origin",
            "requested_at",
            "kind",
            "request_mode",
            "phase",
            "lifecycle_state",
            "safe_visible_state",
            "safe_display_name",
            "target_model_type",
            "track_mask",
            "requirements",
            "trace",
            "redaction",
        }
        self.assertEqual(required - set(payload), set())

    def _run(self, text: str) -> tuple[list[dict[str, object]], MockThoughtTools]:
        tools = MockThoughtTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": text,
                "turn_id": f"turn_motion_request_{len(text)}",
            }
        )
        return events, tools

    def _motion_event(
        self,
        events: list[dict[str, object]],
    ) -> dict[str, object] | None:
        for event in events:
            if event["type"] == "motion.requested":
                return event
        return None

    def _motion_payload(
        self,
        events: list[dict[str, object]],
    ) -> dict[str, object] | None:
        for event in events:
            if event["type"] != "motion.requested":
                continue
            data = event.get("data")
            return data if isinstance(data, dict) else None
        return None


def _validate_schema(value: Any, schema: dict[str, Any]) -> list[str]:
    return _validate(value, schema, schema, "$")


def _validate(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str,
) -> list[str]:
    if "$ref" in schema:
        return _validate(value, _resolve_local_ref(str(schema["$ref"]), root_schema), root_schema, path)

    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        return [f"{path}: expected const {schema['const']!r}"]

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        return [f"{path}: expected type {expected_type}"]

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, property_schema in properties.items():
                if key in value:
                    errors.extend(_validate(value[key], property_schema, root_schema, f"{path}.{key}"))
            if schema.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}: unexpected property {key!r}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: expected minItems {min_items}")
        if schema.get("uniqueItems") is True and len({repr(item) for item in value}) != len(value):
            errors.append(f"{path}: expected uniqueItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(value, str):
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: expected enum value")
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: expected minLength {min_length}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: expected maxLength {max_length}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            errors.append(f"{path}: expected pattern {pattern!r}")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{path}: expected date-time")

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: expected minimum {minimum}")
        maximum = schema.get("maximum")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: expected maximum {maximum}")

    return errors


def _resolve_local_ref(ref: str, root_schema: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise AssertionError(f"unsupported ref: {ref}")
    current: Any = root_schema
    for part in ref[2:].split("/"):
        current = current[part]
    if not isinstance(current, dict):
        raise AssertionError(f"ref did not resolve to schema: {ref}")
    return current


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
        return (isinstance(value, int | float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return True
