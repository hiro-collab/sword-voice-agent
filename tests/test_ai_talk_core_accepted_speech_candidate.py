from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.adapters.ai_talk_core import (
    ACCEPTED_USER_SPEECH_CANDIDATE_INPUT_GATE_SCHEMA,
    AiTalkCoreHandoffError,
    build_accepted_user_speech_turn_envelope,
    load_accepted_user_speech_candidate_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_EXAMPLE = (
    REPO_ROOT.parents[1]
    / "contracts"
    / "accepted_user_speech_candidate_input_gate"
    / "examples"
    / "source_static_accepted_private_user_speech_candidate.example.json"
)


class AiTalkCoreAcceptedSpeechCandidateTest(TestCase):
    def test_builds_canonical_candidate_with_separate_private_turn(self) -> None:
        candidate = json.loads(CANONICAL_EXAMPLE.read_text(encoding="utf-8"))
        envelope = build_accepted_user_speech_turn_envelope(
            candidate,
            {
                "text": "synthetic private user speech",
                "turn_id": "turn_audio_001",
                "session_id": "living_room_main",
                "locale": "ja-JP",
                "context_refs": {"conversation_attempt_ref": "attempt:opaque_001"},
            },
        )

        self.assertEqual(
            envelope["accepted_user_speech_candidate"]["schema_version"],
            ACCEPTED_USER_SPEECH_CANDIDATE_INPUT_GATE_SCHEMA,
        )
        self.assertNotIn("text", envelope["accepted_user_speech_candidate"])
        self.assertEqual(envelope["private_turn"]["turn_id"], "turn_audio_001")

    def test_rejects_noncanonical_candidate_schema(self) -> None:
        with self.assertRaises(AiTalkCoreHandoffError):
            build_accepted_user_speech_turn_envelope(
                {"schema_version": "unsupported_candidate_schema.v0"},
                {"text": "synthetic private user speech"},
            )

    def test_loader_rejects_noncanonical_candidate_schema(self) -> None:
        with temporary_json_file(
            {"schema_version": "unsupported_candidate_schema.v0"}
        ) as path:
            with self.assertRaises(AiTalkCoreHandoffError):
                load_accepted_user_speech_candidate_json(path)


@contextmanager
def temporary_json_file(payload):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        yield path
