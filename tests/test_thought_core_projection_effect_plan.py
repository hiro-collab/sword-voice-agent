import copy
import json
import math
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
SCHEMA_PATH = (
    REPO_ROOT
    / "contracts"
    / "expression"
    / "projection-performance-plan.schema.json"
)
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.projection_effect_plan import (  # noqa: E402
    MAX_DURATION_MS,
    MAX_ID_LENGTH,
    MAX_KEYFRAMES,
    MAX_REVISION,
    MAX_SEED,
    MIN_DURATION_MS,
    ProjectionPerformancePlanValidationError,
    canonical_projection_performance_plan_json,
    validate_projection_performance_plan,
)


class _HostileKeyframeContainer:
    def __init__(self) -> None:
        self.len_calls = 0
        self.iter_calls = 0

    def __len__(self) -> int:
        self.len_calls += 1
        raise RuntimeError("PRIVATE_CONTAINER_LENGTH")

    def __iter__(self):  # type: ignore[no-untyped-def]
        self.iter_calls += 1
        raise RuntimeError("PRIVATE_CONTAINER_ITERATION")


class _WitnessValue:
    access_count = 0

    def __getattribute__(self, name: str):  # type: ignore[no-untyped-def]
        type(self).access_count += 1
        raise RuntimeError("PRIVATE_WITNESS_ACCESS")


class ProjectionPerformancePlanTest(TestCase):
    def test_valid_fire_and_thunder_plans_accept_min_max_boundaries(self) -> None:
        fire = self._valid_plan(
            effect_id="fire",
            duration_ms=MIN_DURATION_MS,
            revision=1,
            seed=0,
            position={"x": -1, "y": 1},
            strength=0,
            keyframes=[
                {"atMs": 0, "position": {"x": -1, "y": 1}, "strength": 0}
            ],
        )
        thunder = self._valid_plan(
            effect_id="thunderBall",
            duration_ms=MAX_DURATION_MS,
            revision=MAX_REVISION,
            seed=MAX_SEED,
            position={"x": 1, "y": -1},
            strength=1,
            keyframes=[
                {"atMs": 0, "position": {"x": -1, "y": -1}, "strength": 0},
                {"atMs": 4000, "position": {"x": -0.25, "y": 0.5}, "strength": 0.25},
                {"atMs": 8000, "position": {"x": 0.5, "y": -0.25}, "strength": 0.75},
                {"atMs": 12000, "position": {"x": 1, "y": 1}, "strength": 1},
            ],
        )

        fire_plan = validate_projection_performance_plan(fire)
        thunder_plan = validate_projection_performance_plan(thunder)

        self.assertEqual(fire_plan.effect_id, "fire")
        self.assertEqual(len(fire_plan.keyframes), 1)
        self.assertEqual(thunder_plan.effect_id, "thunderBall")
        self.assertEqual(len(thunder_plan.keyframes), MAX_KEYFRAMES)
        self.assertEqual(thunder_plan.to_payload(), thunder)

    def test_plan_is_immutable_and_canonical_serialization_is_deterministic(self) -> None:
        candidate = self._valid_plan()
        reordered = {
            "keyframes": [
                {
                    "strength": 0.5,
                    "position": {"y": 0.25, "x": -0.25},
                    "atMs": 0,
                }
            ],
            "seed": 42,
            "durationMs": 3000,
            "strength": 0.5,
            "position": {"y": 0.25, "x": -0.25},
            "effectId": "fire",
            "action": "start",
            "revision": 1,
            "sessionId": "session-1",
            "planId": "plan-1",
            "schemaVersion": 1,
        }

        plan = validate_projection_performance_plan(candidate)
        with self.assertRaises(FrozenInstanceError):
            plan.strength = 0.8  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            plan.position.x = 0.8  # type: ignore[misc]

        canonical = canonical_projection_performance_plan_json(candidate)
        self.assertEqual(canonical, canonical_projection_performance_plan_json(reordered))
        self.assertEqual(canonical, plan.canonical_json())
        self.assertEqual(json.loads(canonical), candidate)
        self.assertNotIn("text", canonical)
        self.assertNotIn("history", canonical)

    def test_forged_instances_cannot_bypass_any_public_serialization_surface(
        self,
    ) -> None:
        plan = validate_projection_performance_plan(self._valid_plan())
        private_marker = "PRIVATE_PLAN_MARKER/secret"
        forged_plans = {
            "private_plan_id": replace(plan, plan_id=private_marker),
            "invalid_action": replace(plan, action="update"),  # type: ignore[arg-type]
            "invalid_effect": replace(plan, effect_id="ice"),  # type: ignore[arg-type]
            "empty_keyframes": replace(plan, keyframes=()),
            "malformed_keyframe": replace(
                plan,
                keyframes=(private_marker,),  # type: ignore[arg-type]
            ),
            "invalid_keyframe": replace(
                plan,
                keyframes=(replace(plan.keyframes[0], at_ms=-1),),
            ),
            "nonfinite_strength": replace(plan, strength=math.nan),
        }

        expected = plan.canonical_json()
        self.assertEqual(
            expected,
            canonical_projection_performance_plan_json(plan),
        )
        self.assertEqual(json.loads(expected), plan.to_payload())

        for label, forged in forged_plans.items():
            self._assert_public_serialization_rejection(
                forged,
                label=label,
                forbidden=(private_marker, "update", "ice"),
            )

    def test_forged_instance_shape_is_bounded_before_keyframe_expansion(
        self,
    ) -> None:
        plan = validate_projection_performance_plan(self._valid_plan())
        witness = _WitnessValue()
        hostile_container = _HostileKeyframeContainer()
        large_keyframes = (plan.keyframes[0],) * 10_000
        forged_plans = {
            "max_plus_one": replace(
                plan,
                keyframes=(witness,) * (MAX_KEYFRAMES + 1),  # type: ignore[arg-type]
            ),
            "materially_large": replace(plan, keyframes=large_keyframes),
            "wrong_container_list": replace(
                plan,
                keyframes=list(plan.keyframes),  # type: ignore[arg-type]
            ),
            "hostile_container": replace(
                plan,
                keyframes=hostile_container,  # type: ignore[arg-type]
            ),
            "wrong_element": replace(
                plan,
                keyframes=(witness,),  # type: ignore[arg-type]
            ),
            "wrong_plan_position": replace(
                plan,
                position=witness,  # type: ignore[arg-type]
            ),
            "wrong_keyframe_position": replace(
                plan,
                keyframes=(
                    replace(
                        plan.keyframes[0],
                        position=witness,  # type: ignore[arg-type]
                    ),
                ),
            ),
        }

        for label, forged in forged_plans.items():
            self._assert_public_serialization_rejection(
                forged,
                label=label,
                forbidden=(
                    "PRIVATE_CONTAINER_LENGTH",
                    "PRIVATE_CONTAINER_ITERATION",
                    "PRIVATE_WITNESS_ACCESS",
                ),
            )

        self.assertEqual(hostile_container.len_calls, 0)
        self.assertEqual(hostile_container.iter_calls, 0)
        self.assertEqual(type(witness).access_count, 0)

        valid_one = validate_projection_performance_plan(self._valid_plan())
        valid_four = validate_projection_performance_plan(
            self._valid_plan(
                duration_ms=12000,
                keyframes=[
                    {
                        "atMs": index * 4000,
                        "position": {"x": index / 3, "y": -(index / 3)},
                        "strength": index / 3,
                    }
                    for index in range(4)
                ],
            )
        )
        for valid in (valid_one, valid_four):
            self.assertEqual(
                valid.canonical_json(),
                canonical_projection_performance_plan_json(valid),
            )
            self.assertEqual(json.loads(valid.canonical_json()), valid.to_payload())

    def test_rejects_invalid_top_level_values_and_forbidden_authority_fields(self) -> None:
        mutations: list[tuple[str, object]] = [
            ("schema_version", self._mutate("schemaVersion", 2)),
            ("schema_bool", self._mutate("schemaVersion", True)),
            ("schema_float", self._mutate("schemaVersion", 1.0)),
            ("empty_plan_id", self._mutate("planId", "")),
            ("oversized_plan_id", self._mutate("planId", "p" * (MAX_ID_LENGTH + 1))),
            ("textual_plan_id", self._mutate("planId", "plan with speech")),
            ("bad_session_id", self._mutate("sessionId", "../private")),
            ("revision_zero", self._mutate("revision", 0)),
            ("revision_bool", self._mutate("revision", True)),
            ("revision_too_large", self._mutate("revision", MAX_REVISION + 1)),
            ("update", self._mutate("action", "update")),
            ("stop", self._mutate("action", "stop")),
            ("action_bool", self._mutate("action", True)),
            ("legacy_thunder", self._mutate("effectId", "thunder")),
            ("effect_bool", self._mutate("effectId", True)),
            ("multiple_effects", self._mutate("effectId", ["fire", "thunderBall"])),
            ("duration_low", self._mutate("durationMs", MIN_DURATION_MS - 1)),
            ("duration_high", self._mutate("durationMs", MAX_DURATION_MS + 1)),
            ("duration_float", self._mutate("durationMs", 3000.0)),
            ("duration_bool", self._mutate("durationMs", True)),
            ("seed_negative", self._mutate("seed", -1)),
            ("seed_bool", self._mutate("seed", False)),
            ("seed_too_large", self._mutate("seed", MAX_SEED + 1)),
            ("position_nonfinite", self._mutate_position("x", math.inf)),
            ("position_nan", self._mutate_position("y", math.nan)),
            ("position_bool", self._mutate_position("x", True)),
            ("position_low", self._mutate_position("x", -1.01)),
            ("position_high", self._mutate_position("y", 1.01)),
            ("strength_nonfinite", self._mutate("strength", math.inf)),
            ("strength_bool", self._mutate("strength", True)),
            ("strength_low", self._mutate("strength", -0.01)),
            ("strength_high", self._mutate("strength", 1.01)),
            ("keyframes_empty", self._mutate("keyframes", [])),
            (
                "keyframes_oversized",
                self._mutate(
                    "keyframes",
                    [
                        {
                            "atMs": index,
                            "position": {"x": 0, "y": 0},
                            "strength": 0.5,
                        }
                        for index in range(MAX_KEYFRAMES + 1)
                    ],
                ),
            ),
            ("keyframes_object", self._mutate("keyframes", {})),
        ]
        for forbidden in (
            "text",
            "speech",
            "history",
            "url",
            "code",
            "shader",
            "params",
            "kbText",
            "sequence",
            "effects",
            "replace",
            "reducedMotion",
            "quality",
            "emergency",
        ):
            candidate = self._valid_plan()
            candidate[forbidden] = "PRIVATE_INPUT_MARKER"
            mutations.append((f"forbidden_{forbidden}", candidate))

        for label, candidate in mutations:
            with self.subTest(label=label):
                self._assert_fixed_rejection(candidate)

    def test_rejects_invalid_keyframes_atomically(self) -> None:
        valid_frame = {
            "atMs": 0,
            "position": {"x": 0, "y": 0},
            "strength": 0.5,
        }
        mutations = {
            "duplicate": [
                valid_frame,
                {"atMs": 0, "position": {"x": 0.1, "y": 0.1}, "strength": 0.6},
            ],
            "out_of_order": [
                {"atMs": 100, "position": {"x": 0, "y": 0}, "strength": 0.5},
                {"atMs": 50, "position": {"x": 0, "y": 0}, "strength": 0.5},
            ],
            "negative": [
                {"atMs": -1, "position": {"x": 0, "y": 0}, "strength": 0.5}
            ],
            "outside_duration": [
                {"atMs": 3001, "position": {"x": 0, "y": 0}, "strength": 0.5}
            ],
            "at_bool": [
                {"atMs": True, "position": {"x": 0, "y": 0}, "strength": 0.5}
            ],
            "incomplete_position": [
                {"atMs": 0, "position": {"x": 0}, "strength": 0.5}
            ],
            "incomplete_state": [{"atMs": 0, "position": {"x": 0, "y": 0}}],
            "unknown_nested": [
                {
                    "atMs": 0,
                    "position": {"x": 0, "y": 0},
                    "strength": 0.5,
                    "text": "PRIVATE_INPUT_MARKER",
                }
            ],
            "unknown_position": [
                {
                    "atMs": 0,
                    "position": {"x": 0, "y": 0, "z": 0},
                    "strength": 0.5,
                }
            ],
        }

        for label, keyframes in mutations.items():
            with self.subTest(label=label):
                self._assert_fixed_rejection(self._mutate("keyframes", keyframes))

    def test_invalid_result_is_fixed_non_echoing_and_has_no_partial_plan(self) -> None:
        candidate = self._valid_plan()
        candidate["rawConversation"] = (
            "PRIVATE_MARKER https://private.invalid shader:void main(){}"
        )

        with self.assertRaises(ProjectionPerformancePlanValidationError) as raised:
            validate_projection_performance_plan(candidate)

        error = raised.exception
        self.assertEqual(
            error.public_payload(),
            {
                "class": "projection_performance_plan_invalid",
                "reason": "invalid_performance_plan",
            },
        )
        serialized_error = json.dumps(
            {
                "args": error.args,
                "vars": vars(error),
                "public": error.public_payload(),
                "text": str(error),
            },
            sort_keys=True,
        )
        self.assertNotIn("PRIVATE_MARKER", serialized_error)
        self.assertNotIn("private.invalid", serialized_error)
        self.assertNotIn("shader", serialized_error)
        self.assertFalse(hasattr(error, "partial_plan"))

    def test_json_schema_mirrors_python_allowlist_and_bounds(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(schema["properties"]["schemaVersion"], {"const": 1})
        self.assertEqual(schema["properties"]["action"], {"const": "start"})
        self.assertEqual(
            schema["properties"]["effectId"]["enum"], ["fire", "thunderBall"]
        )
        for id_field in ("planId", "sessionId"):
            self.assertEqual(schema["properties"][id_field]["maxLength"], MAX_ID_LENGTH)
            self.assertEqual(
                schema["properties"][id_field]["pattern"],
                "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
            )
        self.assertEqual(schema["properties"]["revision"]["minimum"], 1)
        self.assertEqual(schema["properties"]["revision"]["maximum"], MAX_REVISION)
        self.assertEqual(
            schema["properties"]["durationMs"],
            {"type": "integer", "minimum": MIN_DURATION_MS, "maximum": MAX_DURATION_MS},
        )
        self.assertEqual(schema["properties"]["seed"]["maximum"], MAX_SEED)
        self.assertEqual(schema["properties"]["keyframes"]["minItems"], 1)
        self.assertEqual(
            schema["properties"]["keyframes"]["maxItems"], MAX_KEYFRAMES
        )
        self.assertFalse(schema["$defs"]["position"]["additionalProperties"])
        self.assertFalse(schema["$defs"]["keyframe"]["additionalProperties"])
        self.assertEqual(
            set(schema["$defs"]["keyframe"]["required"]),
            set(schema["$defs"]["keyframe"]["properties"]),
        )

    def _valid_plan(
        self,
        *,
        effect_id: str = "fire",
        duration_ms: int = 3000,
        revision: int = 1,
        seed: int = 42,
        position: dict[str, object] | None = None,
        strength: int | float = 0.5,
        keyframes: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        resolved_position = position or {"x": -0.25, "y": 0.25}
        resolved_keyframes = keyframes or [
            {
                "atMs": 0,
                "position": copy.deepcopy(resolved_position),
                "strength": strength,
            }
        ]
        return {
            "schemaVersion": 1,
            "planId": "plan-1",
            "sessionId": "session-1",
            "revision": revision,
            "action": "start",
            "effectId": effect_id,
            "position": resolved_position,
            "strength": strength,
            "durationMs": duration_ms,
            "seed": seed,
            "keyframes": resolved_keyframes,
        }

    def _mutate(self, field: str, value: object) -> dict[str, object]:
        candidate = self._valid_plan()
        candidate[field] = value
        return candidate

    def _mutate_position(self, field: str, value: object) -> dict[str, object]:
        candidate = self._valid_plan()
        position = dict(candidate["position"])  # type: ignore[arg-type]
        position[field] = value
        candidate["position"] = position
        return candidate

    def _assert_fixed_rejection(self, candidate: object) -> None:
        with self.assertRaises(ProjectionPerformancePlanValidationError) as raised:
            validate_projection_performance_plan(candidate)
        self.assertEqual(
            raised.exception.public_payload(),
            {
                "class": "projection_performance_plan_invalid",
                "reason": "invalid_performance_plan",
            },
        )
        self.assertEqual(str(raised.exception), "invalid_performance_plan")

    def _assert_public_serialization_rejection(
        self,
        plan: object,
        *,
        label: str,
        forbidden: tuple[str, ...],
    ) -> None:
        surfaces = (
            lambda: plan.to_payload(),  # type: ignore[attr-defined]
            lambda: plan.canonical_json(),  # type: ignore[attr-defined]
            lambda: canonical_projection_performance_plan_json(plan),
        )
        for index, surface in enumerate(surfaces):
            with self.subTest(label=label, surface=index):
                with self.assertRaises(
                    ProjectionPerformancePlanValidationError
                ) as raised:
                    surface()
                self.assertEqual(
                    raised.exception.public_payload(),
                    {
                        "class": "projection_performance_plan_invalid",
                        "reason": "invalid_performance_plan",
                    },
                )
                error_text = json.dumps(
                    {
                        "args": raised.exception.args,
                        "public": raised.exception.public_payload(),
                        "text": str(raised.exception),
                    },
                    sort_keys=True,
                )
                for marker in forbidden:
                    self.assertNotIn(marker, error_text)
