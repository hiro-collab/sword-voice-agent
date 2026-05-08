import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402


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
        events = ThoughtLoop().run_dicts(TURN)

        self.assertGreater(len(events), 0)
        for event in events:
            with self.subTest(event_type=event["type"]):
                self.assertEqual(validate_schema(event, schema_path), [])

    def test_turn_response_events_schema_accepts_current_response_shape(self) -> None:
        schema_path = (
            REPO_ROOT
            / "contracts"
            / "turn"
            / "turn-response-events.schema.json"
        )
        response = {"events": ThoughtLoop().run_dicts(TURN)}

        self.assertEqual(validate_schema(response, schema_path), [])


def validate_schema(value: Any, schema_path: Path) -> list[str]:
    schema = _load_json(schema_path)
    return _validate(value, schema, schema_path.parent, "$")


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

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, property_schema in properties.items():
                if key in value:
                    errors.extend(
                        _validate(value[key], property_schema, base_dir, f"{path}.{key}")
                    )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, base_dir, f"{path}[{index}]"))

    if isinstance(value, str):
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

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: expected minimum {minimum}")

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
