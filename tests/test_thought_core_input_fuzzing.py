import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


BASE_TURN = {
    "text": "こんにちは",
    "turn_id": "turn_input_fuzz",
    "session_id": "input_fuzz_session",
    "locale": "ja-JP",
    "context_refs": {},
}


class ThoughtCoreInputFuzzingTest(TestCase):
    def test_malformed_turn_metadata_fails_closed_without_side_effects(self) -> None:
        malformed_turns = [
            ("empty_text", {**BASE_TURN, "text": ""}),
            ("blank_text", {**BASE_TURN, "text": " \t\n"}),
            ("list_text", {**BASE_TURN, "text": ["こんにちは"]}),
            ("dict_session_id", {**BASE_TURN, "session_id": {"id": "session"}}),
            ("list_context_refs", {**BASE_TURN, "context_refs": ["env"]}),
            ("list_locale", {**BASE_TURN, "locale": ["ja-JP"]}),
        ]
        tools = MockThoughtTools()
        loop = ThoughtLoop(tools=tools)

        for label, turn in malformed_turns:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    loop.run_dicts(turn)

        self.assert_side_effect_free(tools)

    def test_unicode_and_oversized_inputs_downgrade_without_llm(self) -> None:
        fuzz_texts = [
            "🙂\u200b\u2060\u3000",
            "あ" * 12000,
            "hello\n\n" + ("x" * 4096),
        ]

        for index, text in enumerate(fuzz_texts):
            with self.subTest(index=index):
                tools = MockThoughtTools()
                events = ThoughtLoop(tools=tools).run_dicts(
                    {
                        **BASE_TURN,
                        "text": text,
                        "turn_id": f"turn_input_fuzz_unicode_{index}",
                    }
                )

                understood = event_of_type(events, "input.understood")
                self.assertIn(understood["data"]["kind"], {"general", "state_query"})
                self.assert_local_fallback_route(events)
                self.assert_side_effect_free(tools)

    def test_malformed_context_refs_are_ignored_without_llm_or_leak(self) -> None:
        marker = "synthetic_context_marker_should_not_escape"
        tools = MockThoughtTools()
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **BASE_TURN,
                "text": "日常会話として扱って",
                "turn_id": "turn_input_fuzz_context_refs",
                "context_refs": {
                    "prior_memory_ref": {
                        "unexpected": ["nested", {"marker": marker}],
                    },
                    "input_payload_class": ["not-a-string"],
                    "self_mirror_observation_ref": "\nforged-header",
                },
            }
        )

        understood = event_of_type(events, "input.understood")
        self.assertEqual(understood["data"]["kind"], "general")
        self.assert_local_fallback_route(events)
        self.assert_side_effect_free(tools)
        self.assertNotIn(marker, json.dumps(events, ensure_ascii=False))

    def assert_local_fallback_route(self, events: list[dict[str, object]]) -> None:
        route = event_of_type(events, "thought_core.response_route_classified")
        route_data = route["data"]
        self.assertTrue(route_data["fallback_used"])
        self.assertFalse(route_data["used_llm"])
        self.assertEqual(route_data["provider_route"], "thought-core")

        completed = events[-1]
        self.assertEqual(completed["type"], "turn.completed")
        self.assertFalse(completed["data"].get("used_llm"))

    def assert_side_effect_free(self, tools: MockThoughtTools) -> None:
        self.assertEqual(tools.execute_calls, [])
        self.assertEqual(tools.state_query_feedback_calls, [])
        self.assertEqual(tools.short_memory_write_calls, [])
        self.assertEqual(tools.memory_write_calls, [])


def event_of_type(
    events: list[dict[str, object]],
    event_type: str,
) -> dict[str, object]:
    for event in events:
        if event["type"] == event_type:
            return event
    raise AssertionError(f"missing event type: {event_type}")
