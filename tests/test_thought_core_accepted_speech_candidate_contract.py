from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_OS_ROOT = REPO_ROOT.parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.schema import TurnInput  # noqa: E402


CONTRACT_ROOT = AGENT_OS_ROOT / "contracts" / "accepted_user_speech_candidate_input_gate"
CURRENT_ROUTE_ID = (
    "ACCEPTED-USER-SPEECH-CANDIDATE-INPUT-GATE-THOUGHT-CORE-CONTRACT-"
    "RR00301-SOURCE-STATIC-02"
)
ACCEPTED_EXAMPLE = (
    CONTRACT_ROOT / "examples" / "source_static_accepted_prepared_sample_candidate.example.json"
)
ACCEPTED_PRIVATE_USER_SPEECH_EXAMPLE = (
    CONTRACT_ROOT / "examples" / "source_static_accepted_private_user_speech_candidate.example.json"
)
BLOCKED_EXAMPLES = (
    CONTRACT_ROOT / "examples" / "source_static_blocked_self_output_candidate.example.json",
    CONTRACT_ROOT / "examples" / "source_static_blocked_low_confidence_candidate.example.json",
    CONTRACT_ROOT / "examples" / "source_static_blocked_redaction_only_summary.example.json",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ThoughtCoreAcceptedSpeechCandidateContractTest(TestCase):
    def test_contract_examples_parse_and_keep_required_publication_boundary(self) -> None:
        schema = _load_json(CONTRACT_ROOT / "accepted_user_speech_candidate_input_gate.v0.schema.json")
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "accepted_user_speech_candidate_input_gate.v0",
        )
        self.assertEqual(schema["properties"]["route_id"]["const"], CURRENT_ROUTE_ID)

        accepted = _load_json(ACCEPTED_EXAMPLE)
        self.assertEqual(accepted["route_id"], CURRENT_ROUTE_ID)
        self.assertEqual(
            accepted["text_publication"]["text_publication_policy"],
            "prepared_sample_text_allowed",
        )
        self.assertEqual(
            accepted["text_publication"]["text_provenance_class"],
            "prepared_local_sample_set",
        )
        self.assertEqual(
            accepted["text_publication"]["non_sample_or_live_text_policy"],
            "protected_or_redacted",
        )
        self.assertTrue(accepted["acceptance_decision"]["shared_artifact_contains_text"])
        self.assertFalse(accepted["raw_private_publication_flags"])

        accepted_private = _load_json(ACCEPTED_PRIVATE_USER_SPEECH_EXAMPLE)
        self.assertEqual(accepted_private["route_id"], CURRENT_ROUTE_ID)
        self.assertEqual(accepted_private["speaker_role"], "user_candidate")
        self.assertFalse(
            accepted_private["acceptance_decision"]["shared_artifact_contains_text"]
        )
        self.assertEqual(
            accepted_private["text_publication"]["text_publication_policy"],
            "text_redacted_or_absent",
        )
        self.assertEqual(
            accepted_private["text_publication"]["non_sample_or_live_text_policy"],
            "protected_or_redacted",
        )
        self.assertTrue(
            accepted_private["acceptance_decision"][
                "may_materialize_thought_core_turninput"
            ]
        )
        self.assertFalse(accepted_private["raw_private_publication_flags"])

        for example_path in BLOCKED_EXAMPLES:
            payload = _load_json(example_path)
            self.assertEqual(payload["route_id"], CURRENT_ROUTE_ID)
            self.assertEqual(
                payload["text_publication"]["text_publication_policy"],
                "text_redacted_or_absent",
            )
            self.assertFalse(payload["acceptance_decision"]["shared_artifact_contains_text"])
            self.assertFalse(payload["acceptance_decision"]["may_materialize_thought_core_turninput"])
            self.assertFalse(payload["raw_private_publication_flags"])

    def test_accepted_candidate_shared_artifact_is_not_direct_turn_input(self) -> None:
        candidate = _load_json(ACCEPTED_EXAMPLE)
        candidate.update(
            {
                "text": candidate["text_publication"]["recognized_text"],
                "turn_id": "turn_direct_candidate_001",
                "session_id": "session_direct_candidate_001",
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "accepted_speech_candidate_requires_private_materialization",
        ):
            TurnInput.from_mapping(candidate)

    def test_accepted_candidate_materializes_only_with_private_turn_handoff(self) -> None:
        candidate = _load_json(ACCEPTED_EXAMPLE)
        turn = TurnInput.from_accepted_speech_candidate(
            candidate,
            {
                "text": candidate["text_publication"]["recognized_text"],
                "turn_id": "turn_prepared_sample_001",
                "session_id": "session_prepared_sample_001",
                "locale": "ja-JP",
                "context_refs": {"recognition_summary_ref": "event:prepared_sample_001"},
            },
        )

        self.assertEqual(turn.text, "こんにちは")
        self.assertEqual(turn.turn_id, "turn_prepared_sample_001")
        self.assertEqual(
            turn.context_refs["accepted_user_speech_candidate_ref"],
            "ausc_source_static_prepared_sample_001",
        )
        self.assertEqual(
            turn.context_refs["input_gate_contract"],
            "accepted_user_speech_candidate_input_gate.v0",
        )

    def test_user_speech_content_is_not_prefiltered_before_thought_core(self) -> None:
        candidate = _load_json(ACCEPTED_PRIVATE_USER_SPEECH_EXAMPLE)
        turn = TurnInput.from_accepted_speech_candidate(
            candidate,
            {
                "text": "掃除機をつけて",
                "turn_id": "turn_private_user_speech_001",
                "session_id": "session_private_user_speech_001",
                "locale": "ja-JP",
                "context_refs": {"recognition_summary_ref": "event:private_user_001"},
            },
        )

        self.assertEqual(turn.text, "掃除機をつけて")
        self.assertEqual(
            turn.context_refs["accepted_user_speech_candidate_ref"],
            "ausc_source_static_private_user_speech_001",
        )
        self.assertEqual(
            turn.context_refs["input_gate_contract"],
            "accepted_user_speech_candidate_input_gate.v0",
        )

    def test_blocked_candidates_do_not_materialize_even_with_private_turn_handoff(self) -> None:
        for example_path in BLOCKED_EXAMPLES:
            candidate = _load_json(example_path)
            with self.subTest(example=example_path.name):
                with self.assertRaisesRegex(ValueError, "acceptance_status_not_accepted"):
                    TurnInput.from_accepted_speech_candidate(
                        candidate,
                        {
                            "text": "synthetic private handoff text",
                            "turn_id": "turn_blocked_candidate_001",
                            "session_id": "session_blocked_candidate_001",
                        },
                    )

    def test_non_sample_shared_text_is_rejected_before_turn_materialization(self) -> None:
        candidate = _load_json(ACCEPTED_EXAMPLE)
        candidate["text_publication"] = dict(candidate["text_publication"])
        candidate["text_publication"]["text_provenance_class"] = "redacted_or_absent"

        with self.assertRaisesRegex(ValueError, "shared_text_without_prepared_sample_provenance"):
            TurnInput.from_accepted_speech_candidate(
                candidate,
                {
                    "text": "synthetic private handoff text",
                    "turn_id": "turn_non_sample_text_001",
                    "session_id": "session_non_sample_text_001",
                },
            )

    def test_accepted_decision_cannot_override_contradictory_provenance(self) -> None:
        base_candidate = _load_json(ACCEPTED_PRIVATE_USER_SPEECH_EXAMPLE)
        private_turn = {
            "text": "掃除機をつけて",
            "turn_id": "turn_contradictory_candidate_001",
            "session_id": "session_contradictory_candidate_001",
        }
        variants = (
            (
                "speaker_role",
                {"speaker_role": "system_self_output"},
                "speaker_role_not_user_candidate",
            ),
            (
                "source_kind",
                {"source_kind": "system_self_output_observation"},
                "source_kind_not_user_speech_compatible",
            ),
            (
                "candidate_route",
                {"candidate_route": "self_output_observation"},
                "candidate_route_not_user_speech_compatible",
            ),
            (
                "input_gate_decision_owner",
                {
                    "input_gate": {
                        **base_candidate["input_gate"],
                        "input_gate_decision_owner": "other_gate",
                    }
                },
                "input_gate_decision_owner_not_ai_talk_core",
            ),
            (
                "input_gate_decision_class",
                {
                    "input_gate": {
                        **base_candidate["input_gate"],
                        "input_gate_decision_class": "blocked_self_output",
                    }
                },
                "input_gate_decision_class_not_accepted",
            ),
            (
                "normal_turn_block_reason",
                {
                    "input_gate": {
                        **base_candidate["input_gate"],
                        "normal_turn_block_reason": "self_output",
                    }
                },
                "normal_turn_block_reason_not_null",
            ),
        )

        for label, mutation, expected_reason in variants:
            candidate = dict(base_candidate)
            candidate.update(mutation)
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, expected_reason):
                    TurnInput.from_accepted_speech_candidate(candidate, private_turn)
