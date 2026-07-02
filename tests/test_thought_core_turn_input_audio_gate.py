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


SELF_OUTPUT_EXAMPLE = (
    AGENT_OS_ROOT
    / "contracts"
    / "audio_self_output_observation"
    / "examples"
    / "source_static_self_output_blocked.example.json"
)


class ThoughtCoreTurnInputAudioGateTest(TestCase):
    def test_audio_self_output_observation_never_materializes_turn_input(self) -> None:
        payload = json.loads(SELF_OUTPUT_EXAMPLE.read_text(encoding="utf-8"))
        payload.update(
            {
                "text": "synthetic accepted-looking text",
                "turn_id": "turn_audio_self_output_001",
                "session_id": "session_audio_self_output_001",
            }
        )

        with self.assertRaisesRegex(ValueError, "audio_self_output_observation"):
            TurnInput.from_mapping(payload)

    def test_blocked_audio_summary_never_materializes_turn_input(self) -> None:
        payload = {
            "text": "synthetic blocked audio text",
            "turn_id": "turn_blocked_audio_001",
            "session_id": "session_blocked_audio_001",
            "heard_text_class": "recognition_low_confidence",
            "self_output_gate_decision": "blocked_low_confidence",
            "may_start_user_turn": False,
            "pre_turn_result": {
                "turn_input_materialized": False,
                "normal_turn_adoption_blocked": True,
                "normal_turn_block_reason": "low_confidence",
                "thought_core_turn_input_ref": None,
            },
        }

        with self.assertRaisesRegex(ValueError, "turn_input_materialized_false"):
            TurnInput.from_mapping(payload)

    def test_user_candidate_pending_ai_talk_core_acceptance_is_not_a_turn_yet(self) -> None:
        payload = {
            "text": "synthetic candidate audio text",
            "turn_id": "turn_candidate_audio_001",
            "session_id": "session_candidate_audio_001",
            "self_output_gate_decision": "candidate_user_turn_needs_ai_talk_core_acceptance",
        }

        with self.assertRaisesRegex(ValueError, "non_materializing_audio_gate_decision"):
            TurnInput.from_mapping(payload)

    def test_plain_user_turn_after_separate_acceptance_still_materializes(self) -> None:
        turn = TurnInput.from_mapping(
            {
                "text": "synthetic accepted user input",
                "turn_id": "turn_plain_user_001",
                "session_id": "session_plain_user_001",
                "locale": "ja-JP",
                "context_refs": {"accepted_input_ref": "opaque_correlation_ref:accepted_user_001"},
            }
        )

        self.assertEqual(turn.turn_id, "turn_plain_user_001")
        self.assertEqual(turn.text, "synthetic accepted user input")
