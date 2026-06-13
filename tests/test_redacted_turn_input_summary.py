from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from unittest import TestCase
from uuid import uuid4

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoff
from sword_voice_agent.adapters.redacted_turn_input import (
    RedactedTurnInputError,
    build_redacted_turn_input_summary,
)
from sword_voice_agent.apps.build_redacted_turn_input_summary import build_parser, run


class RedactedTurnInputSummaryTests(TestCase):
    def test_builds_summary_without_raw_handoff_or_response_text(self) -> None:
        handoff = AiTalkCoreHandoff(
            transcript="unsafe_source_value_001",
            command="unsafe_source_value_002",
            prompt_text="unsafe_source_value_003",
            source="web",
            turn_id="turn_source_no_live_001",
        )
        result = {
            "events": [
                {
                    "event_type": "assistant.message",
                    "turn_id": "turn_source_no_live_001",
                    "session_id": "session_1",
                    "event_id": "event_message_001",
                    "data": {"speech": "unsafe_source_value_004"},
                },
                {
                    "event_type": "turn.completed",
                    "turn_id": "turn_source_no_live_001",
                    "session_id": "session_1",
                    "event_id": "event_completed_001",
                    "data": {"status": "success"},
                },
            ],
            "response": {
                "text": "unsafe_source_value_005",
                "conversation_id": "turn_source_no_live_001",
            },
        }

        summary = build_redacted_turn_input_summary(
            handoff,
            thought_core_result=result,
            generated_at="2026-06-13T00:00:00Z",
        )
        encoded = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["schema_version"], "redacted_turn_input.v0")
        self.assertEqual(summary["thought_core"]["turn_id"], "turn_source_no_live_001")
        self.assertTrue(summary["stt"]["transcript_present"])
        self.assertTrue(summary["stt"]["final_result"])
        self.assertTrue(summary["thought_core"]["completion_seen"])
        self.assertEqual(
            summary["thought_core"]["completion_event_ref"],
            "thought:event_completed_001",
        )
        for marker in (
            "unsafe_source_value_001",
            "unsafe_source_value_002",
            "unsafe_source_value_003",
            "unsafe_source_value_004",
            "unsafe_source_value_005",
        ):
            self.assertNotIn(marker, encoded)
        for section in ("stt", "redaction", "safety"):
            self.assertIn(section, summary)
        self.assertFalse(summary["redaction"]["raw_transcript_included"])
        self.assertFalse(summary["redaction"]["prompt_text_included"])
        self.assertFalse(summary["redaction"]["response_text_included"])
        self.assertFalse(summary["safety"]["ordinary_conversation_quality_claimed"])
        self.assertFalse(summary["safety"]["source_adoption_or_git_claimed"])

    def test_requires_completion_metadata_for_final_stt_summary(self) -> None:
        handoff = AiTalkCoreHandoff(
            transcript="unsafe_source_value_006",
            command="unsafe_source_value_007",
            source="web",
            turn_id="turn_missing_completion",
        )

        with self.assertRaisesRegex(RedactedTurnInputError, "completion metadata"):
            build_redacted_turn_input_summary(handoff)

    def test_can_use_explicit_redacted_completion_ref(self) -> None:
        handoff = AiTalkCoreHandoff(
            transcript="unsafe_source_value_008",
            command="unsafe_source_value_009",
            source="web",
        )

        summary = build_redacted_turn_input_summary(
            handoff,
            turn_id="turn_explicit_ref",
            completion_seen=True,
            completion_event_ref="thought:turn_explicit_ref_completed",
        )

        self.assertEqual(summary["thought_core"]["turn_id"], "turn_explicit_ref")
        self.assertEqual(
            summary["thought_core"]["completion_event_ref"],
            "thought:turn_explicit_ref_completed",
        )

    def test_cli_run_writes_only_redacted_summary(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            handoff_json = root / "handoff.json"
            result_json = root / "result.json"
            output_json = root / "summary.json"
            handoff_json.write_text(
                json.dumps(
                    {
                        "transcript": "unsafe_source_value_010",
                        "command": "unsafe_source_value_011",
                        "turn_id": "turn_cli_summary",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result_json.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "event_type": "turn.completed",
                                "turn_id": "turn_cli_summary",
                                "session_id": "session_1",
                                "event_id": "event_cli_completed",
                            }
                        ],
                        "response": {"text": "unsafe_source_value_012"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            args = build_parser().parse_args(
                [
                    "--handoff-json",
                    str(handoff_json),
                    "--thought-core-result-json",
                    str(result_json),
                    "--output-json",
                    str(output_json),
                ]
            )

            summary = run(args)
            saved = json.loads(output_json.read_text(encoding="utf-8"))

        encoded = json.dumps(saved, ensure_ascii=False)
        self.assertEqual(saved, summary)
        self.assertNotIn("unsafe_source_value_010", encoded)
        self.assertNotIn("unsafe_source_value_011", encoded)
        self.assertNotIn("unsafe_source_value_012", encoded)
        self.assertEqual(saved["handoff"]["handoff_source"], "ai_talk_core")
        self.assertEqual(saved["handoff"]["handoff_field"], "redacted_transcript_ref")


@contextmanager
def workspace_tempdir():
    root = Path(__file__).resolve().parent / "_tmp" / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
