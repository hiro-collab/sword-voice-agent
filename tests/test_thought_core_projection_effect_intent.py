import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.projection_effect_intent import (  # noqa: E402
    detect_projection_effect_intent,
)
from thought_core.responders import ResponderResult  # noqa: E402


class _FailingResponder:
    adapter_kind = "projection_effect_test_guard"
    provider = "test"
    model = "test"

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        raise AssertionError("projection effect intent must not call responder")


class _StaticResponder:
    adapter_kind = "projection_effect_general_control"
    provider = "test"
    model = "test"

    def __init__(self) -> None:
        self.calls = 0

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ResponderResult(
            speech="通常会話として応答します。",
            display="通常会話として応答します。",
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={},
        )


class ProjectionEffectIntentTest(TestCase):
    def test_boundary_truth_table(self) -> None:
        cases = (
            ("止めて", "requested"),
            ("リセットして", "requested"),
            ("止めて?", "clarified"),
            ("止めて？", "clarified"),
            ("リセットして?", "clarified"),
            ("リセットして？", "clarified"),
            ("止めて、リセットして", "clarified"),
            ("リセットして、止めて", "clarified"),
            ("止めて、止めて", "clarified"),
            ("リセットして、リセットして", "clarified"),
            ("音楽を止めて？", "general"),
            ("設定をリセットして?", "general"),
            ("音楽を止めて、設定をリセットして", "general"),
            ("音楽を止めて、音楽を止めて", "general"),
            ("設定をリセットして、設定をリセットして", "general"),
            ("写真を見せて", "general"),
            ("会話を開始して", "general"),
            ("炎を出して", "requested"),
            ("雷を見せて", "requested"),
            ("炎を出さないで", "clarified"),
            ("炎と雷を出して", "clarified"),
            ("炎を大きく出して", "clarified"),
        )

        for index, (text, expected_route) in enumerate(cases, start=100):
            with self.subTest(text=text):
                responder = _StaticResponder()
                events = ThoughtLoop(responder=responder).run_dicts(
                    self._turn(index, text)
                )
                requested_count = sum(
                    1
                    for event in events
                    if event["type"] == "projection.effect.requested"
                )
                completed_status = events[-1]["data"]["status"]

                if expected_route == "requested":
                    self.assertEqual(requested_count, 1)
                    self.assertEqual(responder.calls, 0)
                    self.assertEqual(completed_status, "projection_effect_requested")
                elif expected_route == "clarified":
                    self.assertEqual(requested_count, 0)
                    self.assertEqual(responder.calls, 0)
                    self.assertEqual(completed_status, "needs_clarification")
                else:
                    self.assertEqual(requested_count, 0)
                    self.assertEqual(responder.calls, 1)
                    self.assertEqual(completed_status, "llm_response")

    def test_bounded_phrases_emit_one_exact_privacy_safe_request(self) -> None:
        cases = {
            "炎を出して": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "fire",
            },
            "炎を見せて": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "fire",
            },
            "雷を出して": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "thunderBall",
            },
            "雷を見せて": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "thunderBall",
            },
            "止めて": {"schemaVersion": 1, "action": "stop"},
            "リセットして": {"schemaVersion": 1, "action": "reset"},
        }
        forbidden = {
            "text",
            "speech",
            "anchor",
            "position",
            "strength",
            "duration",
            "speechCompletion",
            "update",
            "params",
            "url",
            "code",
            "shader",
        }

        for index, (text, expected) in enumerate(cases.items(), start=1):
            with self.subTest(text=text):
                events = ThoughtLoop(responder=_FailingResponder()).run_dicts(
                    self._turn(index, text)
                )
                requested = [
                    event
                    for event in events
                    if event["type"] == "projection.effect.requested"
                ]
                tool_names = [
                    event["data"]["tool"]
                    for event in events
                    if event["type"] == "tool.started"
                ]

                self.assertEqual(len(requested), 1)
                self.assertEqual(requested[0]["data"], expected)
                self.assertTrue(forbidden.isdisjoint(requested[0]["data"]))
                self.assertNotIn("memory.retrieve", tool_names)
                self.assertFalse(
                    any(event["type"] == "responder.started" for event in events)
                )
                message = [
                    event for event in events if event["type"] == "assistant.message"
                ][-1]
                self.assertFalse(message["data"]["phrase_generation"]["used_llm"])
                self.assertEqual(
                    events[-1]["data"]["status"],
                    "projection_effect_requested",
                )

    def test_ambiguous_or_unbounded_requests_fail_closed_without_event(self) -> None:
        rejected = (
            "炎を出せますか？",
            "炎について話して",
            "炎を出さないで",
            "炎と雷を出して",
            "炎を出してから止めて",
            "止めて、リセットして",
            "リセットして、止めて",
            "止めて、止めて",
            "リセットして、リセットして",
            "炎を大きく出して",
            "雷の位置を変えて",
        )

        for index, text in enumerate(rejected, start=20):
            with self.subTest(text=text):
                events = ThoughtLoop(responder=_FailingResponder()).run_dicts(
                    self._turn(index, text)
                )

                self.assertFalse(
                    any(
                        event["type"] == "projection.effect.requested"
                        for event in events
                    )
                )
                self.assertFalse(
                    any(event["type"] == "responder.started" for event in events)
                )
                self.assertEqual(events[-1]["data"]["status"], "needs_clarification")
                message = [
                    event for event in events if event["type"] == "assistant.message"
                ][-1]
                self.assertEqual(
                    message["data"]["speech"],
                    "炎か雷の開始、停止、リセットのどれか一つを指定してください。",
                )
                self.assertFalse(message["data"]["phrase_generation"]["used_llm"])

    def test_unrelated_general_turn_keeps_existing_responder_route(self) -> None:
        responder = _StaticResponder()
        events = ThoughtLoop(responder=responder).run_dicts(
            self._turn(40, "今日は雑談をしましょう")
        )

        self.assertFalse(
            any(event["type"] == "projection.effect.requested" for event in events)
        )
        self.assertTrue(any(event["type"] == "responder.started" for event in events))
        self.assertEqual(responder.calls, 1)
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_unrelated_generic_verbs_do_not_hijack_general_responder(self) -> None:
        generic_requests = (
            "写真を見せて",
            "会話を開始して",
            "音楽を止めて、設定をリセットして",
            "音楽を止めて、音楽を止めて",
            "設定をリセットして、設定をリセットして",
        )
        for index, text in enumerate(generic_requests, start=50):
            with self.subTest(text=text):
                responder = _StaticResponder()
                events = ThoughtLoop(responder=responder).run_dicts(
                    self._turn(index, text)
                )

                self.assertFalse(
                    any(
                        event["type"] == "projection.effect.requested"
                        for event in events
                    )
                )
                self.assertEqual(responder.calls, 1)
                self.assertTrue(
                    any(event["type"] == "responder.started" for event in events)
                )
                self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_detector_and_schema_expose_only_the_fixed_payload_vocabulary(self) -> None:
        decision = detect_projection_effect_intent("雷を出して")
        schema = json.loads(
            (REPO_ROOT / "contracts/expression/projection-effect-intent.schema.json")
            .read_text(encoding="utf-8")
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(
            decision.event_payload(),
            {"schemaVersion": 1, "action": "start", "effectId": "thunderBall"},
        )
        serialized = json.dumps(schema, ensure_ascii=False)
        for forbidden in (
            "anchor",
            "position",
            "strength",
            "duration",
            "speechCompletion",
            "update",
            "params",
            "url",
            "code",
            "shader",
        ):
            self.assertNotIn(forbidden, serialized)

    def _turn(self, index: int, text: str) -> dict[str, object]:
        return {
            "text": text,
            "turn_id": f"projection_effect_turn_{index}",
            "session_id": f"projection_effect_session_{index}",
            "locale": "ja-JP",
            "context_refs": {},
        }
