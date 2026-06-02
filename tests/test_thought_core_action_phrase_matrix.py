import json
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
    "turn_id": "turn_action_phrase_matrix",
    "session_id": "action_phrase_matrix_session",
    "locale": "ja-JP",
    "context_refs": {},
}

CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "thought_core_action_phrase_cases.json"
VISIBLE_SPEECH_TYPES = {"assistant.message", "feedback.requested"}
MECHANICAL_FALLBACK_PHRASES = (
    "会話応答の境界",
    "今は会話応答",
    "local_fallback",
)


class ThoughtCoreActionPhraseMatrixTest(TestCase):
    def test_all_action_phrase_matrix_cases(self) -> None:
        pack = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        for case in pack["cases"]:
            with self.subTest(case=case["name"]):
                result = self._run_case(case)
                events = result["events"]
                tools = result["tools"]
                event_types = [event["type"] for event in events]
                visible_speech = "\n".join(self._visible_speeches(events))
                expect = case["expect"]

                for event_type in expect.get("event_types", []):
                    self.assertIn(event_type, event_types)
                for event_type in expect.get("event_not_types", []):
                    self.assertNotIn(event_type, event_types)
                for phrase in expect.get("speech_contains", []):
                    self.assertIn(str(phrase), visible_speech)
                for phrase in expect.get("speech_not_contains", []):
                    self.assertNotIn(str(phrase), visible_speech)
                if not expect.get("allow_mechanical_fallback", False):
                    for phrase in MECHANICAL_FALLBACK_PHRASES:
                        self.assertNotIn(str(phrase), visible_speech)
                for phrase, max_count in expect.get(
                    "speech_occurrences_max",
                    {},
                ).items():
                    self.assertLessEqual(
                        visible_speech.count(str(phrase)),
                        int(max_count),
                        f"{phrase!r} appears too often in visible speech",
                    )

                if "execute_calls" in expect:
                    self.assertEqual(len(tools.execute_calls), int(expect["execute_calls"]))

                expected_action_id = expect.get("action_id")
                if expected_action_id:
                    action = self._last_action(events)
                    self.assertIsNotNone(action)
                    assert action is not None
                    self.assertEqual(action.get("action_id"), expected_action_id)
                    self.assertEqual(action.get("target"), expect.get("target"))
                    self.assertEqual(
                        action.get("expected_state"),
                        expect.get("expected_state"),
                    )
                    if tools.execute_calls:
                        self.assertEqual(
                            tools.execute_calls[0].get("action_id"),
                            expected_action_id,
                        )

                if "completed_status" in expect:
                    completed = self._last_completed(events)
                    self.assertIsNotNone(completed)
                    assert completed is not None
                    self.assertEqual(completed.get("status"), expect["completed_status"])

    def _run_case(self, case: dict[str, object]) -> dict[str, object]:
        tools = MockThoughtTools(light_on=bool(case.get("initial_light_on", False)))
        appliance_states = case.get("initial_appliance_states")
        if isinstance(appliance_states, dict):
            tools.appliance_states.update(
                {str(key): str(value) for key, value in appliance_states.items()}
            )
        loop = ThoughtLoop(tools=tools)
        context_refs = case.get("context_refs")
        events = loop.run_dicts(
            {
                **TURN,
                "text": str(case.get("text") or ""),
                "turn_id": f"turn_{case['name']}",
                "context_refs": context_refs if isinstance(context_refs, dict) else {},
            }
        )
        return {"events": events, "tools": tools}

    def _visible_speeches(self, events: list[dict[str, object]]) -> list[str]:
        speeches: list[str] = []
        for event in events:
            if event["type"] not in VISIBLE_SPEECH_TYPES:
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            speech = data.get("speech")
            if isinstance(speech, str) and speech.strip():
                speeches.append(speech)
        return speeches

    def _last_action(self, events: list[dict[str, object]]) -> dict[str, object] | None:
        for event in reversed(events):
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            action = data.get("action")
            if isinstance(action, dict):
                return action
        return None

    def _last_completed(self, events: list[dict[str, object]]) -> dict[str, object] | None:
        for event in reversed(events):
            if event["type"] != "turn.completed":
                continue
            data = event.get("data")
            return data if isinstance(data, dict) else None
        return None
