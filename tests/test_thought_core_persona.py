from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.persona import CheerfulOssanPersona, normalize_persona_tags  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "電気つけて",
    "turn_id": "turn_persona_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "context_refs": {},
}


class ThoughtCorePersonaTest(TestCase):
    def test_cheerful_ossan_persona_adds_emotion_and_motion_tags(self) -> None:
        persona = CheerfulOssanPersona()

        message = persona.apply(
            "うん、状態を見てみるね。",
            emotion="focused",
            motion="think",
        )

        self.assertEqual(message.profile, "cheerful_ossan_v0")
        self.assertEqual(message.emotion, "neutral")
        self.assertEqual(message.motion, "think")
        self.assertEqual(message.speech, "[neutral][motion:think]おう、状態を見てみるぜ。")

    def test_cheerful_ossan_persona_preserves_existing_tags(self) -> None:
        self.assertEqual(
            normalize_persona_tags("[happy][Bow]ありがとな！"),
            "[happy][motion:bow]ありがとな！",
        )
        persona = CheerfulOssanPersona()

        message = persona.apply(
            "[happy][Bow]ありがとな！",
            emotion="neutral",
            motion="idle",
        )

        self.assertEqual(message.speech, "[happy][motion:bow]ありがとな！")

    def test_thought_loop_can_emit_persona_tagged_messages(self) -> None:
        events = ThoughtLoop(
            tools=MockThoughtTools(light_on=False),
            persona=CheerfulOssanPersona(),
        ).run_dicts(TURN)
        messages = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertGreater(len(messages), 0)
        self.assertTrue(messages[0].startswith("[neutral]"))
        self.assertIn("操作できるか", messages[0])
