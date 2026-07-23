import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.projection_effect_intent import (  # noqa: E402
    detect_projection_effect_intent,
)
from thought_core.projection_effect_plan import (  # noqa: E402
    MAX_SEED,
    ProjectionPerformancePlanValidationError,
    canonical_projection_performance_plan_json,
    validate_projection_performance_plan,
)
from thought_core.projection_effect_plan_intent import (  # noqa: E402
    DEFAULT_DURATION_MS,
    DEFAULT_POSITION,
    DEFAULT_STRENGTH,
    MAX_CONTEXT_FILLERS,
    MAX_UTTERANCE_CHARS,
    compile_projection_effect_plan_intent,
)


class _HostileString(str):
    method_calls = 0

    def __len__(self) -> int:
        type(self).method_calls += 1
        raise AssertionError("hostile __len__ must not run")

    def strip(self, *args, **kwargs):
        type(self).method_calls += 1
        raise AssertionError("hostile strip must not run")

    def casefold(self) -> str:
        type(self).method_calls += 1
        raise AssertionError("hostile casefold must not run")


class ProjectionEffectPlanIntentTest(TestCase):
    def test_varied_static_fire_and_thunder_requests_compile(self) -> None:
        cases = (
            (
                "右上に小さめの炎を3秒",
                "fire",
                (0.65, 0.55),
                0.4,
                3_000,
            ),
            (
                "炎を右上に、弱めで3秒だけ出して",
                "fire",
                (0.65, 0.55),
                0.4,
                3_000,
            ),
            (
                "雷を中央より少し上に、弱めで5秒見せて",
                "thunderBall",
                (0.0, 0.3),
                0.4,
                5_000,
            ),
            (
                "すみません、左側に強めのサンダーを2秒表示してください",
                "thunderBall",
                (-0.65, 0.0),
                0.8,
                2_000,
            ),
            (
                "中央にかなり弱めの火炎を1秒出してください",
                "fire",
                (0.0, 0.0),
                0.25,
                1_000,
            ),
        )

        for index, (
            utterance,
            effect_id,
            position,
            strength,
            duration_ms,
        ) in enumerate(cases, start=1):
            with self.subTest(utterance=utterance):
                decision = self._compile(utterance, index=index)
                self.assertTrue(decision.accepted)
                plan = decision.plan
                self.assertIsNotNone(plan)
                assert plan is not None
                self.assertEqual(plan.effect_id, effect_id)
                self.assertEqual(
                    (plan.position.x, plan.position.y),
                    position,
                )
                self.assertEqual(plan.strength, strength)
                self.assertEqual(plan.duration_ms, duration_ms)
                self.assertEqual(len(plan.keyframes), 1)
                self.assertEqual(plan.keyframes[0].at_ms, 0)
                self.assertEqual(plan.keyframes[0].position, plan.position)
                self.assertEqual(plan.keyframes[0].strength, strength)
                self.assertEqual(
                    validate_projection_performance_plan(plan.to_payload()),
                    plan,
                )

    def test_static_defaults_are_fixed_but_simple_legacy_requests_are_not_stolen(
        self,
    ) -> None:
        position_only = self._accepted("炎を右上に出して", index=10)
        self.assertEqual(position_only.strength, DEFAULT_STRENGTH)
        self.assertEqual(position_only.duration_ms, DEFAULT_DURATION_MS)

        strength_only = self._accepted("雷を弱めで見せて", index=11)
        self.assertEqual(
            (strength_only.position.x, strength_only.position.y),
            DEFAULT_POSITION,
        )
        self.assertEqual(strength_only.duration_ms, DEFAULT_DURATION_MS)

        duration_only = self._accepted("火炎を5秒出してください", index=12)
        self.assertEqual(
            (duration_only.position.x, duration_only.position.y),
            DEFAULT_POSITION,
        )
        self.assertEqual(duration_only.strength, DEFAULT_STRENGTH)

        for index, utterance in enumerate(
            ("炎を出して", "雷を見せて", "サンダーを召喚して"),
            start=13,
        ):
            with self.subTest(utterance=utterance):
                planned = self._compile(utterance, index=index)
                fixed = detect_projection_effect_intent(utterance)
                self.assertEqual(planned.status, "no_match")
                self.assertIsNone(planned.plan)
                self.assertTrue(fixed.accepted)

    def test_two_and_four_keyframe_movements_are_complete_and_deterministic(
        self,
    ) -> None:
        fire = self._accepted(
            "炎を左下から右上へ移動させながら4秒",
            index=20,
        )
        self.assertEqual(
            [
                (
                    keyframe.at_ms,
                    keyframe.position.x,
                    keyframe.position.y,
                    keyframe.strength,
                )
                for keyframe in fire.keyframes
            ],
            [
                (0, -0.65, -0.55, DEFAULT_STRENGTH),
                (4_000, 0.65, 0.55, DEFAULT_STRENGTH),
            ],
        )
        self.assertEqual(fire.position, fire.keyframes[0].position)

        thunder = self._accepted(
            "雷を中央から少し上へ動かしながら5秒、弱めで",
            index=21,
        )
        self.assertEqual(
            [
                (
                    keyframe.at_ms,
                    keyframe.position.x,
                    keyframe.position.y,
                    keyframe.strength,
                )
                for keyframe in thunder.keyframes
            ],
            [
                (0, 0.0, 0.0, 0.4),
                (5_000, 0.0, 0.3, 0.4),
            ],
        )

        four = self._accepted(
            "炎を左下から中央を経由して右上を経由して"
            "左上へ移動させながら6秒、強めで",
            index=22,
        )
        self.assertEqual(
            [keyframe.at_ms for keyframe in four.keyframes],
            [0, 2_000, 4_000, 6_000],
        )
        self.assertEqual(len(four.keyframes), 4)
        self.assertTrue(all(keyframe.strength == 0.8 for keyframe in four.keyframes))

    def test_same_input_and_identities_have_equal_plan_and_canonical_json(
        self,
    ) -> None:
        utterance = "炎を左下から右上へ移動させながら4秒"
        first = self._accepted(utterance, index=30)
        second = self._accepted(utterance, index=30)

        self.assertEqual(first, second)
        self.assertEqual(first.to_payload(), second.to_payload())
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(
            first.canonical_json(),
            canonical_projection_performance_plan_json(first),
        )

    def test_duration_boundaries_accept_and_invalid_units_fail_closed(self) -> None:
        lower = self._accepted("炎を右上に弱めで0.5秒出して", index=40)
        upper = self._accepted("雷を中央に強めで12秒見せて", index=41)
        self.assertEqual(lower.duration_ms, 500)
        self.assertEqual(upper.duration_ms, 12_000)

        rejected = (
            "炎を右上に弱めで0.4秒出して",
            "炎を右上に弱めで12.1秒出して",
            "炎を右上に弱めでNaN秒出して",
            "炎を右上に弱めで3分出して",
            "炎を右上に弱めで500ms出して",
            "炎を右上から左上へ動かして",
        )
        for index, utterance in enumerate(rejected, start=42):
            with self.subTest(utterance=utterance):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertEqual(
                    decision.reason,
                    "projection_plan_needs_clarification",
                )

    def test_questions_topics_and_mentions_do_not_claim_projection_authority(
        self,
    ) -> None:
        no_matches = (
            "右上に小さめの炎を3秒出せますか？",
            "弱めの雷を5秒表示できますか",
            "右上の炎について話して",
            "炎を右上に3秒出してと言って",
            "炎の位置は右上",
            "右上に炎を3秒",
        )
        for index, utterance in enumerate(no_matches, start=50):
            with self.subTest(utterance=utterance):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "no_match")
                self.assertIsNone(decision.plan)
                self.assertIsNone(decision.reason)

    def test_explicit_polite_planned_requests_are_accepted(self) -> None:
        cases = (
            (
                "右上に小さめの炎を3秒出してもらえますか",
                "fire",
                (0.65, 0.55),
                3_000,
            ),
            (
                "雷を中央より少し上に、弱めで5秒見せてもらえますか？",
                "thunderBall",
                (0.0, 0.3),
                5_000,
            ),
            (
                "左上に強めの炎を2秒出してくれますか。",
                "fire",
                (-0.65, 0.55),
                2_000,
            ),
        )
        for index, (utterance, effect_id, position, duration_ms) in enumerate(
            cases,
            start=55,
        ):
            with self.subTest(utterance=utterance):
                plan = self._accepted(utterance, index=index)
                self.assertEqual(plan.effect_id, effect_id)
                self.assertEqual((plan.position.x, plan.position.y), position)
                self.assertEqual(plan.duration_ms, duration_ms)

    def test_capability_and_question_only_forms_do_not_become_plans(self) -> None:
        no_matches = (
            "右上に小さめの炎を3秒出せますか？",
            "右上に小さめの炎は3秒出せる？",
            "どうやって右上に小さめの炎を3秒出すの？",
            "中央に弱めの雷を5秒表示できますか",
            "右上に小さめの炎を3秒出して？",
        )
        for index, utterance in enumerate(no_matches, start=58):
            with self.subTest(utterance=utterance):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "no_match")
                self.assertIsNone(decision.plan)
                self.assertIsNone(decision.reason)

    def test_whole_input_must_belong_to_one_planned_start_grammar(self) -> None:
        rejected = (
            "右上に小さめの炎を3秒出して、エアコンをつけて",
            "右上に小さめの炎を3秒の予定です",
            "明日の予定は右上に小さめの炎を3秒出して",
            "右上に小さめの炎を3秒出して成功状況を報告して",
            "右上に小さめの炎を3秒出して、電気を消して",
            "ホーム操作して右上に小さめの炎を3秒出して",
            "右上に小さめの炎を3秒出してもらえますか追加処理",
        )
        private_marker = "PRIVATE_RESIDUAL_SENTINEL"
        for index, utterance in enumerate(rejected, start=63):
            with self.subTest(utterance=utterance):
                decision = self._compile(
                    utterance + (private_marker if index == 69 else ""),
                    index=index,
                )
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertEqual(
                    decision.reason,
                    "projection_plan_needs_clarification",
                )
                self.assertNotIn(private_marker, repr(decision))

    def test_compact_and_movement_forms_consume_the_whole_input(self) -> None:
        accepted = (
            "右上に、小さめの炎を3秒。",
            "すみません、右上に小さめの炎を3秒",
            "炎を右上に、弱めで3秒だけ出して！",
            "炎を左下から右上へ移動させながら4秒。",
            "雷を中央から少し上へ動かしながら5秒、弱めで",
        )
        for index, utterance in enumerate(accepted, start=71):
            with self.subTest(utterance=utterance):
                self.assertTrue(self._compile(utterance, index=index).accepted)

    def test_ambiguous_or_unsupported_plans_fail_closed_atomically(self) -> None:
        rejected = (
            "右上に小さめの炎を3秒出さないで",
            "もしできたら右上に炎を3秒出して",
            "炎と雷を右上に3秒出して",
            "炎を右上に3秒出して、その後雷を見せて",
            "炎を右上に赤く3秒出して",
            "炎を右手に追従させて3秒出して",
            "炎を右上に3秒出してから弱めて",
            "炎を右上に3秒、もう一度同じ感じで出して",
            "炎を右上に3秒出してshaderを使って",
            "炎を右上と左上に3秒出して",
        )
        for index, utterance in enumerate(rejected, start=60):
            with self.subTest(utterance=utterance):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertEqual(
                    decision.reason,
                    "projection_plan_needs_clarification",
                )

    def test_identity_revision_and_seed_are_caller_authority_only(self) -> None:
        private_marker = "PRIVATE/PLAN/MARKER"
        cases = (
            {"plan_id": private_marker},
            {"session_id": "../private"},
            {"revision": 0},
            {"revision": True},
            {"seed": -1},
            {"seed": True},
            {"seed": MAX_SEED + 1},
        )
        for index, override in enumerate(cases, start=70):
            with self.subTest(override=override):
                decision = self._compile(
                    "右上に小さめの炎を3秒",
                    index=index,
                    **override,
                )
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertNotIn(private_marker, repr(decision))
                self.assertEqual(
                    decision.reason,
                    "projection_plan_needs_clarification",
                )

    def test_context_and_input_bounds_are_fixed_and_non_echoing(self) -> None:
        accepted = self._compile(
            ("すみません、" * MAX_CONTEXT_FILLERS)
            + "右上に小さめの炎を3秒",
            index=80,
        )
        self.assertTrue(accepted.accepted)

        private_marker = "PRIVATE_FILLER_SENTINEL"
        rejected = (
            ("すみません、" * (MAX_CONTEXT_FILLERS + 1))
            + "右上に小さめの炎を3秒",
            ("すみません、" * 10_000) + private_marker,
            ("炎" * (MAX_UTTERANCE_CHARS + 1)) + private_marker,
        )
        for index, utterance in enumerate(rejected, start=81):
            with self.subTest(length=len(utterance)):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertNotIn(private_marker, repr(decision))

    def test_pre_normalization_string_bounds_and_exact_type_are_enforced(self) -> None:
        phrase = "右上に小さめの炎を3秒"
        exact_max = (" " * (MAX_UTTERANCE_CHARS - len(phrase))) + phrase
        self.assertTrue(self._compile(exact_max, index=88).accepted)

        max_whitespace = " " * MAX_UTTERANCE_CHARS
        max_whitespace_decision = self._compile(max_whitespace, index=89)
        self.assertEqual(max_whitespace_decision.status, "no_match")
        self.assertIsNone(max_whitespace_decision.plan)

        for index, utterance in enumerate(
            (
                exact_max + " ",
                " " * (MAX_UTTERANCE_CHARS + 1),
                "㌖" * (MAX_UTTERANCE_CHARS // 2),
            ),
            start=90,
        ):
            with self.subTest(index=index):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "clarification_required")
                self.assertIsNone(decision.plan)
                self.assertEqual(
                    decision.reason,
                    "projection_plan_needs_clarification",
                )

        _HostileString.method_calls = 0
        hostile = _HostileString("右上に小さめの炎を3秒")
        decision = self._compile(hostile, index=93)
        self.assertEqual(decision.status, "no_match")
        self.assertIsNone(decision.plan)
        self.assertEqual(_HostileString.method_calls, 0)

    def test_non_text_and_unrelated_inputs_are_stable_text_free_no_match(self) -> None:
        unrelated = (None, 42, {"text": "炎"}, "", "今日は雑談をしましょう")
        for index, utterance in enumerate(unrelated, start=90):
            with self.subTest(utterance_type=type(utterance).__name__):
                decision = self._compile(utterance, index=index)
                self.assertEqual(decision.status, "no_match")
                self.assertIsNone(decision.plan)
                self.assertIsNone(decision.reason)

    def test_every_accepted_result_has_passed_the_adopted_validator(self) -> None:
        utterances = (
            "右上に小さめの炎を3秒",
            "雷を中央より少し上に、弱めで5秒見せて",
            "炎を左下から右上へ移動させながら4秒",
        )
        for index, utterance in enumerate(utterances, start=100):
            with self.subTest(utterance=utterance):
                plan = self._accepted(utterance, index=index)
                self.assertEqual(
                    validate_projection_performance_plan(plan),
                    plan,
                )

        with self.assertRaises(ProjectionPerformancePlanValidationError):
            validate_projection_performance_plan(
                {
                    **self._accepted(
                        utterances[0],
                        index=110,
                    ).to_payload(),
                    "durationMs": 12_001,
                }
            )

    def _accepted(self, utterance: object, *, index: int):
        decision = self._compile(utterance, index=index)
        self.assertTrue(decision.accepted, decision)
        self.assertIsNotNone(decision.plan)
        return decision.plan

    def _compile(
        self,
        utterance: object,
        *,
        index: int,
        plan_id: object | None = None,
        session_id: object | None = None,
        revision: object = 1,
        seed: object = 42,
    ):
        return compile_projection_effect_plan_intent(
            utterance,
            plan_id=plan_id if plan_id is not None else f"plan-{index}",
            session_id=(
                session_id if session_id is not None else f"session-{index}"
            ),
            revision=revision,
            seed=seed,
        )
