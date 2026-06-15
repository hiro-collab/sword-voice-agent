import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "",
    "turn_id": "turn_user_visible_response_consistency",
    "session_id": "user_visible_response_consistency_session",
    "locale": "ja-JP",
    "context_refs": {},
}


class ThoughtCoreUserVisibleResponseConsistencyTest(TestCase):
    def test_light_action_result_uses_same_final_speech_and_display(self) -> None:
        tools = MockThoughtTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気をつけて",
                "turn_id": "turn_light_action_result_consistency",
                "context_refs": {
                    "mock_after_action_wait_matched": "false",
                },
            }
        )
        final_delta = self._last_event_data(events, "assistant.speech_delta")
        final_message = self._last_event_data(events, "assistant.message")
        execute_result = next(
            event["data"]["result"]
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        )

        self.assertEqual(final_delta["message_id"], final_message["message_id"])
        self.assertEqual(final_delta["delta"], final_message["speech"])
        self.assertEqual(final_message["display"], final_message["speech"])
        self.assertIn("映像側の更新はまだ取れていない", final_message["speech"])
        self.assertNotIn("/ 映像確認", final_message["display"])
        self.assertFalse(execute_result["real_execution"])
        self.assertFalse(execute_result["verified_by_bridge"])
        self.assertIn("実家電には送っていません", final_message["speech"])

    def test_ordinary_local_fallback_uses_same_visible_response_object(self) -> None:
        events = ThoughtLoop().run_dicts(
            {
                **TURN,
                "text": "こんにちは、少し相談したい",
                "turn_id": "turn_local_fallback_consistency",
            }
        )
        route = next(
            event
            for event in events
            if event["type"] == "thought_core.response_route_classified"
        )
        final_delta = self._last_event_data(events, "assistant.speech_delta")
        final_message = self._last_event_data(events, "assistant.message")

        self.assertEqual(route["data"]["response_route"], "ordinary_conversation")
        self.assertTrue(route["data"]["fallback_used"])
        self.assertFalse(route["data"]["used_llm"])
        self.assertEqual(final_delta["message_id"], final_message["message_id"])
        self.assertEqual(final_delta["delta"], final_message["speech"])
        self.assertEqual(final_message["display"], final_message["speech"])
        self.assertIn("入力は受け取りました", final_message["speech"])

    def _last_event_data(
        self,
        events: list[dict[str, object]],
        event_type: str,
    ) -> dict[str, object]:
        matches = [event for event in events if event["type"] == event_type]
        self.assertGreater(matches, [])
        data = matches[-1].get("data")
        self.assertIsInstance(data, dict)
        assert isinstance(data, dict)
        return data
