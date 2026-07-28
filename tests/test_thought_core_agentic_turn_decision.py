import json
import math
import sys
from collections import UserDict
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
SCHEMA_PATH = REPO_ROOT / "contracts" / "turn" / "agentic-turn-decision.schema.json"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

import thought_core.agentic_turn_decision as agentic_turn_decision_module  # noqa: E402
from thought_core.agentic_turn_decision import (  # noqa: E402
    MAX_ARGUMENT_DEPTH,
    MAX_ARGUMENT_KEY_LENGTH,
    MAX_ARGUMENT_NODES,
    MAX_ARGUMENT_NUMBER_ABS,
    MAX_ARGUMENT_STRING_LENGTH,
    MAX_CAPABILITY_ID_LENGTH,
    MAX_CONTAINER_ITEMS,
    MAX_RESPONSE_LENGTH,
    validate_agentic_turn_decision,
)


class _HostileDict(dict):
    touches = 0

    def __len__(self) -> int:
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_DICT_LEN")

    def __iter__(self):  # type: ignore[no-untyped-def]
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_DICT_ITER")

    def __getitem__(self, key):  # type: ignore[no-untyped-def]
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_DICT_GET")


class _HostileList(list):
    touches = 0

    def __len__(self) -> int:
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_LIST_LEN")

    def __iter__(self):  # type: ignore[no-untyped-def]
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_LIST_ITER")


class _HostileString(str):
    touches = 0

    def __len__(self) -> int:
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_STRING_LEN")


class _HostileIterable:
    touches = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        type(self).touches += 1
        raise RuntimeError("PRIVATE_HOSTILE_ITER")


class _IntegerSubclass(int):
    pass


class _FloatSubclass(float):
    pass


class AgenticTurnDecisionTest(TestCase):
    def test_rejections_report_one_fixed_validation_subcode_without_private_text(self) -> None:
        cases = (
            (None, None, "candidate_not_object"),
            (
                {**self._valid("conversation"), "schemaVersion": 2},
                None,
                "decision_shape_invalid",
            ),
            (
                self._response_mutation("speech", ""),
                None,
                "response_invalid",
            ),
            (
                self._capability_extra("provider_payload", "PRIVATE_PROVIDER_TEXT"),
                lambda capability_id, arguments: True,
                "capability_shape_invalid",
            ),
            (
                self._valid("capability"),
                lambda capability_id, arguments: False,
                "catalog_rejected",
            ),
        )
        for candidate, catalog, expected in cases:
            with self.subTest(expected=expected):
                result = validate_agentic_turn_decision(
                    candidate,
                    capability_catalog_validator=catalog,
                )
                self._assert_fixed_rejection(result)
                self.assertEqual(result.validation_subcode, expected)
                self.assertNotIn("PRIVATE_PROVIDER_TEXT", repr(result))

        with patch.object(
            agentic_turn_decision_module,
            "_validate_response",
            side_effect=RuntimeError("PRIVATE_VALIDATOR_EXCEPTION"),
        ):
            internal = validate_agentic_turn_decision(self._valid("conversation"))
        self._assert_fixed_rejection(internal)
        self.assertEqual(internal.validation_subcode, "validation_internal")
        self.assertNotIn("PRIVATE_VALIDATOR_EXCEPTION", repr(internal))

    def test_every_kind_preserves_multiple_free_unicode_replies(self) -> None:
        replies = {
            "conversation": (
                ("今日は風が気持ちいいね。", "風の話をしています 🍃"),
                ("その見方、面白いです。", "別の観点も考えています。"),
            ),
            "clarification": (
                ("どちらの部屋ですか？", "対象の部屋を確認しています。"),
                ("明るさはどのくらいにしますか。", "明るさの指定が必要です。"),
            ),
            "hold": (
                ("安全を確認するまで待ちます。", "確認待ちです。"),
                ("今は操作せず、ここで止めておきます。", "操作を保留しました。"),
            ),
            "capability": (
                ("リビングの照明を少し暗くします。", "照明を40%にする提案です。"),
                ("炎の演出を短く表示します 🔥", "炎エフェクトを準備しています。"),
            ),
        }

        observed = set()
        for kind, variants in replies.items():
            for speech, display in variants:
                with self.subTest(kind=kind, speech=speech):
                    candidate = self._valid(kind, speech=speech, display=display)
                    result = validate_agentic_turn_decision(
                        candidate,
                        capability_catalog_validator=lambda capability_id, arguments: True,
                    )
                    self.assertTrue(result.accepted)
                    self.assertEqual(result.status, "accepted")
                    self.assertIsNone(result.reason)
                    self.assertIsNone(result.validation_subcode)
                    self.assertIsNotNone(result.decision)
                    self.assertEqual(result.decision.kind, kind)  # type: ignore[union-attr]
                    self.assertEqual(result.decision.response.speech, speech)  # type: ignore[union-attr]
                    self.assertEqual(result.decision.response.display, display)  # type: ignore[union-attr]
                    observed.add((kind, speech, display))
        self.assertEqual(len(observed), 8)

    def test_response_boundaries_and_exact_shape(self) -> None:
        for text in ("界", "界" * MAX_RESPONSE_LENGTH):
            result = validate_agentic_turn_decision(
                self._valid("conversation", speech=text, display=text)
            )
            self.assertTrue(result.accepted)

        mutations = {
            "speech_empty": self._response_mutation("speech", ""),
            "speech_blank": self._response_mutation("speech", " \t\n"),
            "speech_long": self._response_mutation(
                "speech", "界" * (MAX_RESPONSE_LENGTH + 1)
            ),
            "speech_bool": self._response_mutation("speech", True),
            "display_empty": self._response_mutation("display", ""),
            "display_blank": self._response_mutation("display", "　"),
            "display_long": self._response_mutation(
                "display", "界" * (MAX_RESPONSE_LENGTH + 1)
            ),
            "display_list": self._response_mutation("display", ["private"]),
            "response_missing": self._top_without("response"),
            "response_not_object": {**self._valid("conversation"), "response": []},
            "response_extra": self._response_extra("rawText", "PRIVATE_RAW_TEXT"),
            "response_missing_display": self._response_without("display"),
        }
        self._assert_rejected_matrix(mutations)

    def test_kind_required_forbidden_and_extra_field_matrix(self) -> None:
        mutations: dict[str, object] = {
            "schema_missing": self._top_without("schemaVersion"),
            "schema_zero": {**self._valid("conversation"), "schemaVersion": 0},
            "schema_two": {**self._valid("conversation"), "schemaVersion": 2},
            "schema_bool": {**self._valid("conversation"), "schemaVersion": True},
            "schema_float": {**self._valid("conversation"), "schemaVersion": 1.0},
            "kind_missing": self._top_without("kind"),
            "kind_unknown": {**self._valid("conversation"), "kind": "action"},
            "kind_bool": {**self._valid("conversation"), "kind": True},
            "extra_raw_utterance": {
                **self._valid("conversation"),
                "rawUtterance": "PRIVATE_USER_TEXT",
            },
            "extra_history": {
                **self._valid("conversation"),
                "history": ["PRIVATE_HISTORY"],
            },
            "extra_provider": {
                **self._valid("conversation"),
                "providerPayload": {"native": "PRIVATE_PROVIDER_TEXT"},
            },
        }
        for kind in ("conversation", "clarification", "hold"):
            candidate = self._valid(kind)
            candidate["capability"] = self._call()
            mutations[f"{kind}_forbids_capability"] = candidate
        capability_missing = self._valid("capability")
        del capability_missing["capability"]
        mutations["capability_requires_call"] = capability_missing
        self._assert_rejected_matrix(mutations)

    def test_capability_id_and_call_shape_matrix(self) -> None:
        valid_ids = (
            "a",
            "home.light:set-v1",
            "A" * MAX_CAPABILITY_ID_LENGTH,
        )
        for capability_id in valid_ids:
            with self.subTest(capability_id=capability_id):
                result = validate_agentic_turn_decision(
                    self._valid("capability", capability_id=capability_id),
                    capability_catalog_validator=lambda found, arguments: (
                        found == capability_id
                    ),
                )
                self.assertTrue(result.accepted)

        mutations = {
            "id_empty": self._capability_mutation("id", ""),
            "id_long": self._capability_mutation(
                "id", "a" * (MAX_CAPABILITY_ID_LENGTH + 1)
            ),
            "id_space": self._capability_mutation("id", "home light"),
            "id_slash": self._capability_mutation("id", "home/light"),
            "id_unicode": self._capability_mutation("id", "照明"),
            "id_bool": self._capability_mutation("id", True),
            "call_missing_id": self._capability_without("id"),
            "call_missing_arguments": self._capability_without("arguments"),
            "call_extra": self._capability_extra("sequence", []),
            "call_array": {**self._valid("capability"), "capability": []},
            "arguments_null": self._capability_mutation("arguments", None),
            "two_calls": {
                **self._valid("capability"),
                "capability": [self._call(), self._call()],
            },
        }
        for label, candidate in mutations.items():
            with self.subTest(label=label):
                result = validate_agentic_turn_decision(
                    candidate,
                    capability_catalog_validator=lambda capability_id, arguments: True,
                )
                self._assert_fixed_rejection(result)

    def test_arguments_accept_exact_json_boundaries_and_are_deeply_immutable(self) -> None:
        arguments = {
            "empty": "",
            "text": "界" * MAX_ARGUMENT_STRING_LENGTH,
            "minimum": -MAX_ARGUMENT_NUMBER_ABS,
            "maximum": MAX_ARGUMENT_NUMBER_ABS,
            "fraction": 0.25,
            "enabled": True,
            "disabled": False,
            "missing": None,
            "nested": {
                "path": ["右", 1, True],
                "meta": {"done": True},
            },
        }
        candidate = self._valid("capability", arguments=arguments)
        callback_observations: list[tuple[object, ...]] = []

        def catalog(capability_id, snapshot):  # type: ignore[no-untyped-def]
            callback_observations.append(
                (
                    capability_id,
                    type(snapshot) is not dict,
                    type(snapshot["nested"]) is not dict,
                    type(snapshot["nested"]["path"]) is not list,
                )
            )
            return True

        first = validate_agentic_turn_decision(
            candidate,
            capability_catalog_validator=catalog,
        )
        second = validate_agentic_turn_decision(
            candidate,
            capability_catalog_validator=catalog,
        )

        self.assertTrue(first.accepted)
        self.assertEqual(first, second)
        self.assertEqual(
            callback_observations,
            [
                ("home.light.set", True, True, True),
                ("home.light.set", True, True, True),
            ],
        )
        decision = first.decision
        self.assertIsNotNone(decision)
        self.assertIsInstance(decision.capability.arguments, MappingProxyType)  # type: ignore[union-attr]
        self.assertIsInstance(decision.capability.arguments["nested"], MappingProxyType)  # type: ignore[union-attr]
        self.assertIsInstance(
            decision.capability.arguments["nested"]["meta"],  # type: ignore[index,union-attr]
            MappingProxyType,
        )
        self.assertIsInstance(
            decision.capability.arguments["nested"]["path"],  # type: ignore[index,union-attr]
            tuple,
        )
        with self.assertRaises(FrozenInstanceError):
            decision.kind = "hold"  # type: ignore[misc,union-attr]
        with self.assertRaises(TypeError):
            decision.capability.arguments["enabled"] = False  # type: ignore[index,union-attr]
        with self.assertRaises(TypeError):
            decision.capability.arguments["nested"]["new"] = 1  # type: ignore[index,union-attr]

        arguments["enabled"] = False
        arguments["nested"]["path"].append("later")  # type: ignore[index,union-attr]
        self.assertIs(decision.capability.arguments["enabled"], True)  # type: ignore[union-attr]
        self.assertEqual(
            decision.capability.arguments["nested"]["path"],  # type: ignore[index,union-attr]
            ("右", 1, True),
        )

    def test_argument_depth_nodes_container_key_string_and_number_bounds(self) -> None:
        valid_depth = {"a": {"b": ["depth-four"]}}
        valid_64_nodes = {
            **{f"a{index}": [0, 1, 2] for index in range(15)},
            "last": [0, 1],
        }
        valid_values = (
            {},
            {f"k{index}": index for index in range(MAX_CONTAINER_ITEMS)},
            {"items": list(range(MAX_CONTAINER_ITEMS))},
            {"k" * MAX_ARGUMENT_KEY_LENGTH: "v"},
            valid_depth,
            valid_64_nodes,
        )
        for index, arguments in enumerate(valid_values):
            with self.subTest(valid=index):
                result = validate_agentic_turn_decision(
                    self._valid("capability", arguments=arguments),
                    capability_catalog_validator=lambda capability_id, snapshot: True,
                )
                self.assertTrue(result.accepted)

        invalid_65_nodes = {
            f"a{index}": [0, 1, 2] for index in range(MAX_CONTAINER_ITEMS)
        }
        mutations = {
            "root_not_object": [],
            "root_tuple": (),
            "object_17": {
                f"k{index}": index for index in range(MAX_CONTAINER_ITEMS + 1)
            },
            "array_17": {"items": list(range(MAX_CONTAINER_ITEMS + 1))},
            "empty_key": {"": 1},
            "long_key": {"k" * (MAX_ARGUMENT_KEY_LENGTH + 1): 1},
            "long_string": {"value": "x" * (MAX_ARGUMENT_STRING_LENGTH + 1)},
            "integer_low": {"value": -MAX_ARGUMENT_NUMBER_ABS - 1},
            "integer_high": {"value": MAX_ARGUMENT_NUMBER_ABS + 1},
            "float_low": {"value": -MAX_ARGUMENT_NUMBER_ABS - 0.5},
            "float_high": {"value": MAX_ARGUMENT_NUMBER_ABS + 0.5},
            "nan": {"value": math.nan},
            "positive_inf": {"value": math.inf},
            "negative_inf": {"value": -math.inf},
            "integer_subclass": {"value": _IntegerSubclass(1)},
            "float_subclass": {"value": _FloatSubclass(0.5)},
            "depth_5": {"a": {"b": [{"too": "deep"}]}},
            "nodes_65": invalid_65_nodes,
        }
        self.assertEqual(MAX_ARGUMENT_DEPTH, 4)
        self.assertEqual(MAX_ARGUMENT_NODES, 64)
        for label, arguments in mutations.items():
            with self.subTest(label=label):
                result = validate_agentic_turn_decision(
                    self._valid("capability", arguments=arguments),
                    capability_catalog_validator=lambda capability_id, snapshot: True,
                )
                self._assert_fixed_rejection(result)

    def test_exact_builtin_types_reject_before_hostile_expansion_or_catalog(self) -> None:
        _HostileDict.touches = 0
        _HostileList.touches = 0
        _HostileString.touches = 0
        _HostileIterable.touches = 0
        catalog_calls = 0

        def catalog(capability_id, arguments):  # type: ignore[no-untyped-def]
            nonlocal catalog_calls
            catalog_calls += 1
            return True

        hostile_top = _HostileDict(self._valid("conversation"))
        hostile_arguments = (
            _HostileDict({"private": 1}),
            {"items": _HostileList([1])},
            {_HostileString("private"): 1},
            {"value": _HostileString("private")},
            {"value": _HostileIterable()},
            UserDict({"private": 1}),
        )
        self._assert_fixed_rejection(validate_agentic_turn_decision(hostile_top))
        for arguments in hostile_arguments:
            result = validate_agentic_turn_decision(
                self._valid("capability", arguments=arguments),
                capability_catalog_validator=catalog,
            )
            self._assert_fixed_rejection(result)

        self.assertEqual(_HostileDict.touches, 0)
        self.assertEqual(_HostileList.touches, 0)
        self.assertEqual(_HostileString.touches, 0)
        self.assertEqual(_HostileIterable.touches, 0)
        self.assertEqual(catalog_calls, 0)

    def test_catalog_is_once_only_and_requires_exact_true(self) -> None:
        candidate = self._valid("capability")
        outcomes: tuple[object, ...] = (False, None, 1, "success", object())
        for outcome in outcomes:
            calls = 0

            def catalog(capability_id, arguments, resolved=outcome):  # type: ignore[no-untyped-def]
                nonlocal calls
                calls += 1
                return resolved

            with self.subTest(outcome=repr(outcome)):
                result = validate_agentic_turn_decision(
                    candidate,
                    capability_catalog_validator=catalog,
                )
                self._assert_fixed_rejection(result)
                self.assertEqual(calls, 1)

        accepted_calls = 0

        def accepted_catalog(capability_id, arguments):  # type: ignore[no-untyped-def]
            nonlocal accepted_calls
            accepted_calls += 1
            return True

        accepted = validate_agentic_turn_decision(
            candidate,
            capability_catalog_validator=accepted_catalog,
        )
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted_calls, 1)

        self._assert_fixed_rejection(validate_agentic_turn_decision(candidate))

    def test_catalog_exception_and_caught_mutation_attempt_fail_closed(self) -> None:
        candidate = self._valid(
            "capability",
            arguments={"nested": {"items": [1, 2]}},
        )

        def raises_private(capability_id, arguments):  # type: ignore[no-untyped-def]
            raise RuntimeError("PRIVATE_NATIVE_CATALOG_TEXT")

        def mutates_root(capability_id, arguments):  # type: ignore[no-untyped-def]
            try:
                arguments["nested"] = {}
            except TypeError:
                pass
            return True

        def mutates_nested_object(capability_id, arguments):  # type: ignore[no-untyped-def]
            try:
                arguments["nested"]["new"] = "PRIVATE"
            except TypeError:
                pass
            return True

        def mutates_nested_array(capability_id, arguments):  # type: ignore[no-untyped-def]
            try:
                arguments["nested"]["items"].append(3)
            except TypeError:
                pass
            return True

        for callback in (
            raises_private,
            mutates_root,
            mutates_nested_object,
            mutates_nested_array,
        ):
            with self.subTest(callback=callback.__name__):
                result = validate_agentic_turn_decision(
                    candidate,
                    capability_catalog_validator=callback,
                )
                self._assert_fixed_rejection(result)
                public = json.dumps(
                    {
                        "status": result.status,
                        "reason": result.reason,
                        "decision": result.decision,
                    },
                    sort_keys=True,
                )
                self.assertEqual(
                    public,
                    '{"decision": null, "reason": "invalid_decision", "status": "rejected"}',
                )
                self.assertNotIn("PRIVATE", public)

    def test_catalog_cannot_change_the_validated_snapshot_through_input_alias(self) -> None:
        arguments = {"room": "living", "level": 0.4}
        candidate = self._valid("capability", arguments=arguments)

        def mutates_original_alias(capability_id, snapshot):  # type: ignore[no-untyped-def]
            arguments["level"] = MAX_ARGUMENT_NUMBER_ABS + 1
            arguments["late"] = "PRIVATE_LATE_MUTATION"
            return True

        result = validate_agentic_turn_decision(
            candidate,
            capability_catalog_validator=mutates_original_alias,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.decision.capability.arguments["level"], 0.4)  # type: ignore[union-attr]
        self.assertNotIn("late", result.decision.capability.arguments)  # type: ignore[union-attr]

    def test_catalog_and_decision_share_one_snapshot_after_alias_expansion(self) -> None:
        arguments = {"room": "living", "level": 0.4}
        candidate = self._valid("capability", arguments=arguments)
        original_canonical_json = agentic_turn_decision_module._canonical_json
        catalog_observation: dict[str, object] = {}
        catalog_calls = 0

        def snapshot_then_expand_alias(value, *, depth, budget):  # type: ignore[no-untyped-def]
            snapshot = original_canonical_json(value, depth=depth, budget=budget)
            if depth == 1:
                arguments["level"] = MAX_ARGUMENT_NUMBER_ABS + 1
                arguments["late"] = [
                    "PRIVATE_LATE_MUTATION"
                    for _ in range(MAX_CONTAINER_ITEMS + 1)
                ]
            return snapshot

        def catalog(capability_id, snapshot):  # type: ignore[no-untyped-def]
            nonlocal catalog_calls
            catalog_calls += 1
            catalog_observation.update(snapshot)
            return True

        with patch.object(
            agentic_turn_decision_module,
            "_canonical_json",
            side_effect=snapshot_then_expand_alias,
        ):
            result = validate_agentic_turn_decision(
                candidate,
                capability_catalog_validator=catalog,
            )

        self.assertTrue(result.accepted)
        decision_arguments = result.decision.capability.arguments  # type: ignore[union-attr]
        self.assertEqual(catalog_calls, 1)
        self.assertEqual(catalog_observation, dict(decision_arguments))
        self.assertEqual(catalog_observation, {"room": "living", "level": 0.4})
        self.assertNotIn("late", decision_arguments)
        self.assertNotIn("PRIVATE", repr(result))

    def test_result_objects_are_frozen_and_rejections_repeat_equal(self) -> None:
        accepted = validate_agentic_turn_decision(self._valid("conversation"))
        rejected_one = validate_agentic_turn_decision({"private": "PRIVATE_TEXT"})
        rejected_two = validate_agentic_turn_decision({"private": "OTHER_TEXT"})

        self.assertEqual(rejected_one, rejected_two)
        with self.assertRaises(FrozenInstanceError):
            accepted.status = "rejected"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            rejected_one.reason = None  # type: ignore[misc]

    def test_schema_parses_and_carries_structural_v1_controls(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {"schemaVersion", "kind", "response"},
        )
        self.assertEqual(schema["properties"]["schemaVersion"], {"const": 1})
        self.assertEqual(
            schema["properties"]["kind"]["enum"],
            ["conversation", "clarification", "hold", "capability"],
        )
        conditional = schema["allOf"][0]
        self.assertEqual(
            conditional["if"]["properties"]["kind"],
            {"const": "capability"},
        )
        self.assertEqual(conditional["then"]["required"], ["capability"])
        self.assertEqual(
            conditional["else"]["not"]["required"],
            ["capability"],
        )
        response = schema["$defs"]["response"]
        self.assertFalse(response["additionalProperties"])
        self.assertEqual(set(response["required"]), {"speech", "display"})
        response_text = schema["$defs"]["responseText"]
        self.assertEqual(response_text["minLength"], 1)
        self.assertEqual(response_text["maxLength"], MAX_RESPONSE_LENGTH)
        self.assertEqual(response_text["pattern"], "\\S")
        call = schema["$defs"]["capabilityCall"]
        self.assertFalse(call["additionalProperties"])
        self.assertEqual(set(call["required"]), {"id", "arguments"})
        self.assertEqual(call["properties"]["id"]["maxLength"], MAX_CAPABILITY_ID_LENGTH)
        arguments = schema["$defs"]["arguments"]
        self.assertEqual(arguments["maxProperties"], MAX_CONTAINER_ITEMS)
        self.assertEqual(
            arguments["propertyNames"]["maxLength"],
            MAX_ARGUMENT_KEY_LENGTH,
        )
        scalar = schema["$defs"]["jsonScalar"]["anyOf"]
        string_schema = next(item for item in scalar if item["type"] == "string")
        self.assertEqual(string_schema["maxLength"], MAX_ARGUMENT_STRING_LENGTH)
        for number_type in ("integer", "number"):
            number_schema = next(item for item in scalar if item["type"] == number_type)
            self.assertEqual(number_schema["minimum"], -MAX_ARGUMENT_NUMBER_ABS)
            self.assertEqual(number_schema["maximum"], MAX_ARGUMENT_NUMBER_ABS)
        self.assertEqual(
            schema["$defs"]["jsonDepth4"],
            {"$ref": "#/$defs/jsonScalar"},
        )
        self.assertIn("not a permanent limit", schema["description"])

    def _valid(
        self,
        kind: str,
        *,
        speech: object = "自然な応答です。",
        display: object = "応答を表示しています。",
        capability_id: object = "home.light.set",
        arguments: object | None = None,
    ) -> dict[str, object]:
        candidate: dict[str, object] = {
            "schemaVersion": 1,
            "kind": kind,
            "response": {"speech": speech, "display": display},
        }
        if kind == "capability":
            candidate["capability"] = self._call(
                capability_id=capability_id,
                arguments={"room": "living", "level": 0.4}
                if arguments is None
                else arguments,
            )
        return candidate

    def _call(
        self,
        *,
        capability_id: object = "home.light.set",
        arguments: object | None = None,
    ) -> dict[str, object]:
        return {
            "id": capability_id,
            "arguments": {} if arguments is None else arguments,
        }

    def _top_without(self, field: str) -> dict[str, object]:
        candidate = self._valid("conversation")
        del candidate[field]
        return candidate

    def _response_mutation(self, field: str, value: object) -> dict[str, object]:
        candidate = self._valid("conversation")
        response = dict(candidate["response"])  # type: ignore[arg-type]
        response[field] = value
        candidate["response"] = response
        return candidate

    def _response_extra(self, field: str, value: object) -> dict[str, object]:
        return self._response_mutation(field, value)

    def _response_without(self, field: str) -> dict[str, object]:
        candidate = self._valid("conversation")
        response = dict(candidate["response"])  # type: ignore[arg-type]
        del response[field]
        candidate["response"] = response
        return candidate

    def _capability_mutation(self, field: str, value: object) -> dict[str, object]:
        candidate = self._valid("capability")
        capability = dict(candidate["capability"])  # type: ignore[arg-type]
        capability[field] = value
        candidate["capability"] = capability
        return candidate

    def _capability_extra(self, field: str, value: object) -> dict[str, object]:
        return self._capability_mutation(field, value)

    def _capability_without(self, field: str) -> dict[str, object]:
        candidate = self._valid("capability")
        capability = dict(candidate["capability"])  # type: ignore[arg-type]
        del capability[field]
        candidate["capability"] = capability
        return candidate

    def _assert_rejected_matrix(self, mutations: dict[str, object]) -> None:
        for label, candidate in mutations.items():
            with self.subTest(label=label):
                self._assert_fixed_rejection(validate_agentic_turn_decision(candidate))

    def _assert_fixed_rejection(self, result: object) -> None:
        self.assertEqual(result.status, "rejected")  # type: ignore[attr-defined]
        self.assertEqual(result.reason, "invalid_decision")  # type: ignore[attr-defined]
        self.assertIsNone(result.decision)  # type: ignore[attr-defined]
        self.assertFalse(result.accepted)  # type: ignore[attr-defined]


if __name__ == "__main__":
    import unittest

    unittest.main()
