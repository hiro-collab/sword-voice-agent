import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
SHARED_DANCE_VECTOR_ENV = "SWORD_M4_DANCE_LIFECYCLE_VECTOR_PATH"
MAX_SHARED_DANCE_VECTOR_BYTES = 128 * 1024
SHARED_DANCE_CASE_ORDER = [
    "dance_start_queued",
    "dance_active_accept",
    "dance_stop_before_start",
    "dance_stop_repeated",
    "dance_stop_active",
    "dance_late_result_after_stop",
    "dance_late_frame_after_stop",
    "dance_stale_result",
    "dance_settled_idle",
]
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


def load_shared_dance_lifecycle_vectors() -> dict[str, object] | None:
    configured = os.environ.get(SHARED_DANCE_VECTOR_ENV, "").strip()
    if not configured:
        return None
    path = Path(configured).resolve(strict=True)
    if len(str(path)) > 4096 or path.suffix != ".json" or not path.is_file():
        raise AssertionError("shared dance vector path must be a bounded JSON file")
    if not 0 < path.stat().st_size <= MAX_SHARED_DANCE_VECTOR_BYTES:
        raise AssertionError("shared dance vector file size is out of bounds")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("shared dance vector root must be an object")
    if payload.get("schema_version") != "m4_dance_lifecycle_fault_vectors.v0":
        raise AssertionError("shared dance vector schema_version is invalid")
    if payload.get("fixture_kind") != "non_schema_test_vectors":
        raise AssertionError("shared dance vector must remain non-schema")
    if payload.get("case_order") != SHARED_DANCE_CASE_ORDER:
        raise AssertionError("shared dance vector case order is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != len(SHARED_DANCE_CASE_ORDER):
        raise AssertionError("shared dance vector case count is invalid")
    return payload


class ThoughtCoreMotionRequestContractTest(TestCase):
    def test_shared_dance_lifecycle_vectors_when_configured(self) -> None:
        vectors = load_shared_dance_lifecycle_vectors()
        if vectors is None:
            return
        raw_cases = vectors["cases"]
        self.assertIsInstance(raw_cases, list)
        cases = {case["case_id"]: case for case in raw_cases if isinstance(case, dict)}
        self.assertEqual(list(cases), SHARED_DANCE_CASE_ORDER)
        self.assertEqual(len(cases), len(SHARED_DANCE_CASE_ORDER))

        required_safe_fields = {
            "case_id",
            "dance_session_ref",
            "sequence_number",
            "prior_state",
            "candidate_event_kind",
            "lifecycle_state",
            "candidate_state",
            "expected_receiver_result_class",
            "expected_state",
            "core_contract_class",
        }
        for case_id in SHARED_DANCE_CASE_ORDER:
            with self.subTest(case=case_id):
                self.assertEqual(required_safe_fields - set(cases[case_id]), set())

        start = cases["dance_start_queued"]
        self.assertEqual(start["request_mode"], "play")
        self.assertEqual(start["lifecycle_state"], "queued")
        self.assertEqual(start["expected_receiver_result_class"], "accepted_queued")
        self.assertEqual(start["core_contract_class"], "play_request_accepted")
        start_payload = self._motion_payload(self._run("踊って")[0])
        self.assertIsNotNone(start_payload)
        assert start_payload is not None
        self.assertEqual(start_payload["request_mode"], start["request_mode"])
        self.assertEqual(start_payload["lifecycle_state"], start["lifecycle_state"])

        stop_payload = self._motion_payload(self._run("踊りをやめて")[0])
        self.assertIsNotNone(stop_payload)
        assert stop_payload is not None
        for case_id, expected_class in (
            ("dance_stop_before_start", "stop_idempotent"),
            ("dance_stop_repeated", "stop_idempotent"),
            ("dance_stop_active", "stop_to_idle"),
        ):
            case = cases[case_id]
            with self.subTest(case=case_id):
                self.assertEqual(case["candidate_event_kind"], "stimulus")
                self.assertEqual(case["request_mode"], "stop")
                self.assertEqual(case["lifecycle_state"], "stopped")
                self.assertEqual(case["core_contract_class"], expected_class)
                self.assertEqual(stop_payload["request_mode"], "stop")
                self.assertEqual(stop_payload["lifecycle_state"], "queued")
                self.assertEqual(stop_payload["fallback_state"], "stop_to_idle")

        receiver_only = {
            "dance_active_accept": "accepted_active",
            "dance_late_result_after_stop": "rejected_late_after_stop",
            "dance_late_frame_after_stop": "rejected_late_after_stop",
            "dance_stale_result": "rejected_stale",
            "dance_settled_idle": "accepted_settled_idle",
        }
        for case_id, expected_result_class in receiver_only.items():
            case = cases[case_id]
            with self.subTest(case=case_id):
                self.assertIn(case["candidate_event_kind"], {"runtime_result", "frame"})
                self.assertEqual(
                    case["expected_receiver_result_class"], expected_result_class
                )

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
        self.assertEqual(payload["loop"], False)
        self.assertEqual(payload["loop_count"], 1)
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

    def test_evidence_backed_semantic_motion_phrases_emit_exact_bounded_contracts(self) -> None:
        cases = (
            (
                "全身を見せて",
                "show_full_body",
                "posture",
                "Show full body",
            ),
            ("挨拶して", "greeting", "gesture", "Greeting gesture"),
            ("Vサインして", "peace_sign", "gesture", "Peace sign gesture"),
            ("撃つポーズして", "shoot_pose", "gesture", "Shooting pose"),
            ("一回転して", "spin", "gesture", "Spin motion"),
            ("モデルポーズして", "model_pose", "posture", "Model pose"),
            ("屈伸運動して", "squat", "gesture", "Squat motion"),
        )
        for text, semantic, kind, display_name in cases:
            with self.subTest(text=text, semantic=semantic):
                events, tools = self._run(text)
                payload = self._motion_payload(events)

                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(payload["kind"], kind)
                self.assertEqual(payload["request_mode"], "play")
                self.assertEqual(payload["safe_display_name"], display_name)
                self.assertEqual(
                    payload["payload_ref"],
                    f"motion.thought_core.semantic_motion.{semantic}.v0",
                )
                self.assertEqual(payload["loop"], False)
                self.assertEqual(payload["loop_count"], 0)
                self.assertEqual(payload["interrupt_policy"], "replace_same_track")
                self.assertEqual(payload["fallback_state"], "neutral_idle")
                self.assertIn("body_root", payload["track_mask"])
                self.assertEqual(
                    payload["requirements"]["required_tracks"],
                    ["body_root", "spine"],
                )
                self.assertEqual(payload["safety"]["raw_user_text_shared"], False)
                self.assertEqual(payload["safety"]["raw_path_shared"], False)
                self.assertEqual(payload["safety"]["home_assistant_route"], False)
                self.assertNotIn(text, json.dumps(payload, ensure_ascii=False))
                self.assert_no_home_action(events, tools)

    def test_generic_greeting_and_unsafe_shoot_wording_do_not_mint_motion(self) -> None:
        for text in ("こんにちは", "元気？", "撃って"):
            with self.subTest(text=text):
                events, tools = self._run(text)
                self.assertIsNone(self._motion_payload(events))
                self.assert_no_home_action(events, tools)

    def test_motion_understanding_publication_is_bounded_and_non_echoing(self) -> None:
        private_markers = (
            "path-private-marker.vrma",
            "license-private-marker",
            "model-private-marker",
            "local-private-marker",
        )
        text = "挨拶して " + " ".join(private_markers)

        events, tools = self._run(text)
        understood = next(
            event for event in events if event["type"] == "input.understood"
        )
        published = json.dumps(events, ensure_ascii=False)

        self.assertEqual(understood["data"]["kind"], "motion_request")
        self.assertEqual(understood["data"]["target"], "semantic_motion")
        self.assertEqual(understood["data"]["desired_state"], "greeting")
        self.assertEqual(understood["data"]["reason"], "bounded_motion_request")
        self.assertEqual(
            understood["data"]["metadata"],
            {
                "publication_class": "bounded_motion_semantic_summary",
                "motion_kind": "semantic_motion",
                "motion_semantic": "greeting",
            },
        )
        self.assertNotIn(text, published)
        for marker in private_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, published)
        self.assertIsNotNone(self._motion_payload(events))
        self.assert_no_home_action(events, tools)

    def test_dance_stop_requests_emit_stop_contract_without_home_action(self) -> None:
        for text in (
            "踊りをやめて",
            "踊るのを止めて",
            "踊りを停止して",
            "stop dancing",
            "cancel dance",
        ):
            with self.subTest(text=text):
                events, tools = self._run(text)
                understood = next(
                    event for event in events if event["type"] == "input.understood"
                )
                payload = self._motion_payload(events)
                visible_speech = "\n".join(
                    str(event["data"].get("speech") or "")
                    for event in events
                    if event["type"] == "assistant.message"
                )

                self.assertEqual(understood["data"]["kind"], "motion_request")
                self.assertEqual(understood["data"]["target"], "cancel")
                self.assertEqual(understood["data"]["desired_state"], "stop")
                self.assertEqual(understood["data"]["reason"], "bounded_motion_request")
                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(payload["schema_version"], "motion_stimulus.v0")
                self.assertEqual(payload["kind"], "stop")
                self.assertEqual(payload["request_mode"], "stop")
                self.assertEqual(payload["payload_ref"], "motion.thought_core.stop.v0")
                self.assertEqual(payload["safe_display_name"], "Stop motion")
                self.assertEqual(payload["duration_ms"], 0)
                self.assertEqual(payload["loop"], False)
                self.assertEqual(payload["loop_count"], 0)
                self.assertEqual(payload["interrupt_policy"], "stop")
                self.assertEqual(payload["fallback_state"], "stop_to_idle")
                self.assertEqual(payload["stop_reason"], "user_requested")
                self.assertNotEqual(payload["kind"], "dance_sequence")
                self.assertNotEqual(payload["request_mode"], "play")
                self.assertNotIn("止まりました", visible_speech)
                self.assertNotIn("停止しました", visible_speech)
                self.assert_no_home_action(events, tools)

    def test_music_dance_request_carries_rhythm_hint_only(self) -> None:
        events, tools = self._run("音楽に合わせて踊って")
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["kind"], "dance_sequence")
        self.assertEqual(payload["safe_display_name"], "Music dance")
        self.assertEqual(payload["payload_ref"], "motion.thought_core.dance_sequence.v0")
        self.assertEqual(payload["loop"], False)
        self.assertEqual(payload["loop_count"], 1)
        self.assertEqual(payload["safety"]["home_assistant_route"], False)
        self.assert_no_home_action(events, tools)

    def test_happy_motion_request_maps_to_expression_visible_contract(self) -> None:
        events, tools = self._run("うれしそうに動いて")
        payload = self._motion_payload(events)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["kind"], "expression")
        self.assertEqual(payload["request_mode"], "apply")
        self.assertEqual(payload["loop"], False)
        self.assertEqual(payload["loop_count"], 0)
        self.assertEqual(payload["safe_display_name"], "Happy expression")
        self.assertEqual(
            payload["track_mask"],
            {"scope": "face_head", "channels": ["expression_weight"]},
        )
        self.assertEqual(payload["requirements"]["required_tracks"], ["face"])
        self.assertEqual(
            payload["requirements"]["expression_profile_ref"],
            "motion.runtime.vrm_expression_weights.v0",
        )
        self.assertEqual(
            payload["requirements"]["expected_visible_change"],
            "face_expression",
        )
        self.assertEqual(payload["requirements"]["expected_roi"], "avatar_face_head")
        self.assertEqual(
            payload["payload_ref"],
            "motion.thought_core.expression_visible.v0",
        )
        self.assertNotEqual(payload["payload_ref"], "motion.thought_core.expression.v0")
        self.assertEqual(payload["safety"]["raw_user_text_shared"], False)
        self.assert_no_home_action(events, tools)

    def test_motion_payloads_match_local_and_root_contract_schemas(self) -> None:
        schema_paths = [
            REPO_ROOT
            / "contracts"
            / "motion-stimulus"
            / "motion-stimulus.v0.schema.json",
            REPO_ROOT.parent.parent
            / "contracts"
            / "motion_stimulus"
            / "motion_stimulus.v0.schema.json",
        ]
        for text in (
            "踊って",
            "うれしそうに動いて",
            "踊りをやめて",
            "全身を見せて",
            "挨拶して",
            "Vサインして",
            "撃つポーズして",
            "一回転して",
            "モデルポーズして",
            "屈伸運動して",
        ):
            events, _tools = self._run(text)
            payload = self._motion_payload(events)

            self.assertIsNotNone(payload)
            assert payload is not None
            for schema_path in schema_paths:
                with self.subTest(text=text, schema_path=str(schema_path)):
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

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        for option_schema in one_of:
            if not isinstance(option_schema, dict):
                continue
            if not _validate(value, option_schema, root_schema, path):
                matches += 1
        if matches != 1:
            return [f"{path}: expected exactly one oneOf match"]
        return []

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
