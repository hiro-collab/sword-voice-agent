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
    "turn_id": "turn_feedback_loop",
    "session_id": "feedback_loop_session",
    "locale": "ja-JP",
    "context_refs": {},
}

CASES_PATH = Path(__file__).resolve().parent / "fixtures" / "thought_core_feedback_loop_cases.json"


class FeedbackLoopTools(MockThoughtTools):
    feedback_result: dict[str, object] | None = None

    def state_query_feedback(self, turn, payload):  # type: ignore[no-untyped-def]
        self.state_query_feedback_calls.append(dict(payload))
        result = dict(
            self.feedback_result
            or {
                "status": "accepted",
                "ok": True,
                "feedback_id": "sqf_feedback_loop",
                "duplicate": False,
            }
        )
        result.setdefault("target", payload.get("target") or payload.get("state_query_id"))
        result.setdefault("user_label", payload.get("user_label"))
        return result


class ThoughtCoreFeedbackLoopReplayTest(TestCase):
    def test_feedback_loop_replay_cases(self) -> None:
        pack = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        for case in pack["cases"]:
            with self.subTest(case=case["name"]):
                result = self._run_case(case)
                event_types = [event["type"] for event in result["events"]]
                speeches = "\n".join(
                    str(event.get("data", {}).get("speech") or "")
                    for event in result["events"]
                    if event["type"] == "assistant.message"
                )
                expect = case["expect"]

                for event_type in expect.get("event_types", []):
                    self.assertIn(event_type, event_types)
                for phrase in expect.get("speech_contains", []):
                    self.assertIn(phrase, speeches)
                for phrase in expect.get("speech_not_contains", []):
                    self.assertNotIn(phrase, speeches)
                self.assertEqual(
                    len(result["tools"].state_query_feedback_calls),
                    expect.get("feedback_calls", 0),
                )
                self.assertGreaterEqual(
                    len(result["tools"].short_memory_write_calls),
                    expect.get("short_memory_writes_min", 0),
                )

    def _run_case(self, case: dict[str, object]) -> dict[str, object]:
        tools = FeedbackLoopTools(light_on=bool(case.get("initial_light_on", False)))
        feedback_result = case.get("feedback_result")
        tools.feedback_result = feedback_result if isinstance(feedback_result, dict) else None
        loop = ThoughtLoop(tools=tools)
        events: list[dict[str, object]] = []
        for index, turn_spec in enumerate(case.get("turns", [])):
            if not isinstance(turn_spec, dict):
                continue
            context_refs = {
                **TURN["context_refs"],
                **(
                    turn_spec.get("context_refs")
                    if isinstance(turn_spec.get("context_refs"), dict)
                    else {}
                ),
            }
            events.extend(
                loop.run_dicts(
                    {
                        **TURN,
                        "text": str(turn_spec.get("text") or ""),
                        "turn_id": f"turn_{case['name']}_{index}",
                        "context_refs": context_refs,
                    }
                )
            )
        return {"events": events, "tools": tools}
