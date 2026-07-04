import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.schema import TurnInput  # noqa: E402

from sword_voice_agent.adapters.ai_talk_core import (  # noqa: E402
    ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA_VERSION,
    AcceptedUserSpeechCandidate,
    AiTalkCoreAcceptedSpeechCandidateError,
)
from sword_voice_agent.adapters.thought_core import build_turn_payload  # noqa: E402


ACCEPTED_CANDIDATE = {
    "schema_version": ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA_VERSION,
    "candidate_id": "speech_candidate_001",
    "accepted_text": "電気をつけて",
    "turn_id": "turn_audio_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "source": "ai_talk_core",
    "acceptance_status": "accepted",
    "may_start_user_turn": True,
    "turn_adoption_authority": True,
    "raw_private_publication_flags": False,
    "context_refs": {
        "recognition_summary": "safe_ref_recognition_001",
        "prepared_sample": "safe_ref_sample_001",
    },
}


class AiTalkCoreAcceptedSpeechCandidateTest(TestCase):
    def test_accepted_candidate_materializes_plain_thought_core_turn(self) -> None:
        candidate = AcceptedUserSpeechCandidate.from_mapping(ACCEPTED_CANDIDATE)

        request = candidate.to_agent_request(user="operator")
        payload = build_turn_payload(request)
        turn = TurnInput.from_mapping(payload)

        self.assertEqual(turn.text, "電気をつけて")
        self.assertEqual(turn.turn_id, "turn_audio_001")
        self.assertEqual(turn.session_id, "living_room_main")
        self.assertEqual(turn.locale, "ja-JP")
        self.assertEqual(
            turn.context_refs["accepted_user_speech_candidate_ref"],
            "speech_candidate_001",
        )

    def test_redacted_summary_without_accepted_text_is_not_a_turn(self) -> None:
        payload = {
            "schema_version": ACCEPTED_USER_SPEECH_CANDIDATE_SCHEMA_VERSION,
            "candidate_id": "speech_candidate_001",
            "turn_id": "turn_audio_001",
            "session_id": "living_room_main",
            "acceptance_status": "accepted",
            "may_start_user_turn": True,
            "turn_adoption_authority": True,
            "raw_private_publication_flags": False,
            "recognition_summary_class": "stable_browser_stt_summary_only",
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)

    def test_pending_candidate_gate_cannot_materialize_turn(self) -> None:
        payload = {
            **ACCEPTED_CANDIDATE,
            "self_output_gate_decision": "candidate_user_turn_needs_ai_talk_core_acceptance",
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)

    def test_missing_turn_authority_cannot_materialize_turn(self) -> None:
        payload = {
            **ACCEPTED_CANDIDATE,
            "turn_adoption_authority": False,
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)

    def test_raw_private_candidate_cannot_materialize_turn(self) -> None:
        payload = {
            **ACCEPTED_CANDIDATE,
            "raw_private_publication_flags": True,
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)

    def test_missing_raw_private_flag_cannot_materialize_turn(self) -> None:
        payload = {
            key: value
            for key, value in ACCEPTED_CANDIDATE.items()
            if key != "raw_private_publication_flags"
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)

    def test_non_false_raw_private_flag_cannot_materialize_turn(self) -> None:
        payload = {
            **ACCEPTED_CANDIDATE,
            "raw_private_publication_flags": "false",
        }

        with self.assertRaises(AiTalkCoreAcceptedSpeechCandidateError):
            AcceptedUserSpeechCandidate.from_mapping(payload)
