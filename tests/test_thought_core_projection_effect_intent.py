import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.projection_effect_intent import (  # noqa: E402
    MAX_CONTEXT_FILLERS,
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
            ("すみません、炎を出してください", "requested"),
            ("雷を見せてもらえますか？", "requested"),
            ("じゃあ、ちょっと火炎を表示してほしい。", "requested"),
            ("サンダーを召喚してくれますか？", "requested"),
            ("ファイアが見たい", "requested"),
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

    def test_compositional_wishes_emit_one_exact_privacy_safe_request(self) -> None:
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
            "すみません、炎を出してください": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "fire",
            },
            "雷を見せてもらえますか？": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "thunderBall",
            },
            "じゃあ、ちょっと火炎を表示してほしい。": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "fire",
            },
            "サンダーを召喚してくれますか？": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "thunderBall",
            },
            "ファイアが見たい": {
                "schemaVersion": 1,
                "action": "start",
                "effectId": "fire",
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
                self.assertEqual(set(requested[0]["data"]), set(expected))
                self.assertTrue(forbidden.isdisjoint(requested[0]["data"]))
                self.assertNotIn(text, json.dumps(requested[0]["data"], ensure_ascii=False))
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
            "雷を見せられますか？",
            "雷が出せる？",
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
            "炎を出して、それから雷を見せて",
            "炎を見せてと言って",
            "ファイアとサンダーを見せて",
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

    def test_context_filler_limit_is_bounded_and_non_echoing(self) -> None:
        accepted_text = ("すみません、" * MAX_CONTEXT_FILLERS) + "炎を出して"
        accepted_events = ThoughtLoop(responder=_FailingResponder()).run_dicts(
            self._turn(70, accepted_text)
        )
        accepted_requests = [
            event
            for event in accepted_events
            if event["type"] == "projection.effect.requested"
        ]
        self.assertEqual(len(accepted_requests), 1)
        self.assertEqual(
            accepted_requests[0]["data"],
            {"schemaVersion": 1, "action": "start", "effectId": "fire"},
        )

        private_marker = "PRIVATE_FILLER_SENTINEL"
        rejected_inputs = (
            ("すみません、" * (MAX_CONTEXT_FILLERS + 1)) + "炎を出して",
            ("すみません、" * 10000) + f"炎を出して{private_marker}",
        )
        for index, text in enumerate(rejected_inputs, start=71):
            with self.subTest(filler_count=text.count("すみません")):
                events = ThoughtLoop(responder=_FailingResponder()).run_dicts(
                    self._turn(index, text)
                )
                event_types = [event["type"] for event in events]
                projection_publication = [
                    event
                    for event in events
                    if event["type"]
                    in {
                        "projection.effect.requested",
                        "assistant.speech_delta",
                        "assistant.message",
                        "turn.completed",
                    }
                ]
                serialized_publication = json.dumps(
                    projection_publication, ensure_ascii=False
                )

                self.assertNotIn("projection.effect.requested", event_types)
                self.assertNotIn("responder.started", event_types)
                self.assertNotIn("tool.started", event_types)
                self.assertNotIn(text, serialized_publication)
                self.assertNotIn(private_marker, serialized_publication)
                self.assertEqual(events[-1]["type"], "turn.completed")
                self.assertEqual(events[-1]["data"]["status"], "needs_clarification")
                self.assertEqual(
                    events[-1]["data"]["reason"],
                    "projection_effect_request_not_bounded",
                )
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
