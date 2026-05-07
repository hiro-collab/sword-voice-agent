from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sword_voice_agent.adapters.ai_talk_core import AiTalkCoreHandoffError
from sword_voice_agent.apps.send_handoff_to_thought_core import (
    build_parser,
    format_event_line,
    run,
)
from sword_voice_agent.adapters.thought_core import ThoughtCoreStreamEvent
from sword_voice_agent.protocol.messages import AgentResponse


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class SendHandoffToThoughtCoreTest(TestCase):
    def test_dry_run_builds_turn_payload_without_calling_thought_core(self) -> None:
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--dry-run",
                "--context",
                "mode=test",
                "--context-ref",
                "voice_turn=voice-1",
                "--session-id",
                "living_room_main",
                "--turn-id",
                "turn-test",
            ]
        )

        result = run(args)

        self.assertIsNone(result["response"])
        self.assertEqual(result["request"]["text"], "冷蔵庫の材料から買い物リストを提案する")
        self.assertEqual(result["request"]["context"]["mode"], "test")
        self.assertEqual(result["turn_payload"]["turn_id"], "turn-test")
        self.assertEqual(result["turn_payload"]["session_id"], "living_room_main")
        self.assertEqual(result["turn_payload"]["context_refs"]["voice_turn"], "voice-1")

    def test_can_include_transcript_context(self) -> None:
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--dry-run",
                "--include-transcript-context",
            ]
        )

        result = run(args)

        self.assertEqual(
            result["request"]["context"]["transcript"],
            "買い物リストを作って",
        )

    def test_dry_run_can_send_direct_text_without_handoff(self) -> None:
        args = build_parser().parse_args(
            [
                "--text",
                "電気つけて",
                "--dry-run",
                "--session-id",
                "living_room_main",
                "--turn-id",
                "turn-manual",
            ]
        )

        result = run(args)

        self.assertEqual(result["request"]["text"], "電気つけて")
        self.assertEqual(result["request"]["context"]["source"], "manual")
        self.assertEqual(result["turn_payload"]["turn_id"], "turn-manual")
        self.assertEqual(result["turn_payload"]["session_id"], "living_room_main")

    @patch("sword_voice_agent.apps.send_handoff_to_thought_core.ThoughtCoreClient")
    def test_run_calls_thought_core_client(self, client_class: MagicMock) -> None:
        client = MagicMock()
        event = ThoughtCoreStreamEvent(
            event_type="assistant.message",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=1,
            data={"speech": "了解です"},
        )

        def fake_streaming(turn_payload, *, on_event=None):
            if on_event is not None:
                on_event(event)
            return AgentResponse(
                text="了解です",
                conversation_id=turn_payload["turn_id"],
                raw={"ok": True},
            )

        client.send_turn_streaming.side_effect = fake_streaming
        client_class.from_env.return_value = client
        args = build_parser().parse_args(
            [
                "--handoff-json",
                str(FIXTURES / "handoff.json"),
                "--turn-id",
                "turn-test",
                "--session-id",
                "living_room_main",
            ]
        )

        result = run(args)

        self.assertEqual(result["response"]["text"], "了解です")
        self.assertEqual(result["events"][0]["event_type"], "assistant.message")
        client.send_turn_streaming.assert_called_once()

    def test_rejects_placeholder_root(self) -> None:
        args = build_parser().parse_args(
            [
                "--ai-talk-core-root",
                "<ai_talk_core_root>",
                "--dry-run",
            ]
        )

        with self.assertRaises(AiTalkCoreHandoffError):
            run(args)

    def test_format_event_line_includes_speech_or_tool(self) -> None:
        speech = ThoughtCoreStreamEvent(
            event_type="assistant.speech_delta",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=3,
            data={"delta": "了解"},
        )
        tool = ThoughtCoreStreamEvent(
            event_type="tool.started",
            turn_id="turn-test",
            session_id="living_room_main",
            seq=4,
            data={"tool": "environment.observe"},
        )

        self.assertEqual(format_event_line(speech), "3 assistant.speech_delta: 了解")
        self.assertEqual(format_event_line(tool), "4 tool.started: environment.observe")
