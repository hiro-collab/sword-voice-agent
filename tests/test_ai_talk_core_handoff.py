from pathlib import Path
from unittest import TestCase

from sword_voice_agent.adapters.ai_talk_core import (
    AiTalkCoreHandoffError,
    load_handoff_from_root,
    load_handoff_json,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class AiTalkCoreHandoffTest(TestCase):
    def test_loads_handoff_from_ai_talk_core_cache(self) -> None:
        handoff = load_handoff_from_root(FIXTURES / "ai_talk_core_root", source="web")

        self.assertEqual(handoff.transcript, "今日の作業を記録して")
        self.assertEqual(handoff.command, "作業記録をMarkdownに整理する")
        self.assertEqual(handoff.prompt_text, "Voice transcript:\n今日の作業を記録して\n")
        self.assertEqual(handoff.source, "web")

    def test_builds_agent_request_from_handoff(self) -> None:
        handoff = load_handoff_json(FIXTURES / "handoff.json", source="web")

        request = handoff.to_agent_request(
            field="command",
            user="demo-user",
            conversation_id="conv-1",
            context={"project": "sword-voice-agent"},
        )

        self.assertEqual(request.text, "冷蔵庫の材料から買い物リストを提案する")
        self.assertEqual(request.user, "demo-user")
        self.assertEqual(request.conversation_id, "conv-1")
        self.assertEqual(request.context["source"], "ai_talk_core")
        self.assertEqual(request.context["trigger"], "sword_sign")
        self.assertEqual(request.context["project"], "sword-voice-agent")
        self.assertEqual(request.context["transcript"], "買い物リストを作って")

    def test_rejects_empty_prompt_field(self) -> None:
        handoff = load_handoff_json(FIXTURES / "handoff.json")

        with self.assertRaises(AiTalkCoreHandoffError):
            handoff.to_agent_request(field="prompt")
