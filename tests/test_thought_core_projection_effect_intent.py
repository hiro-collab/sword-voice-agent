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

    def __init__(self) -> None:
        self.calls = 0

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise RuntimeError("PRIVATE_PROJECTION_RESPONDER_FAILURE")


class _StaticResponder:
    adapter_kind = "projection_effect_general_control"
    provider = "test"
    model = "test"

    def __init__(self) -> None:
        self.calls = 0
        self.response_contexts: list[dict[str, object]] = []

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.response_contexts.append(dict(response_context or {}))
        compact = str(turn.text or "").replace(" ", "").replace("　", "")
        if any(marker in compact for marker in ("炎", "火炎", "ファイア")):
            speech = "会話の流れに合わせて、炎のエフェクトを出しますね。"
        elif any(marker in compact for marker in ("雷", "サンダー")):
            speech = "会話の流れに合わせて、雷のエフェクトを出しますね。"
        elif compact == "止めて":
            speech = "わかりました。エフェクトを止めます。"
        elif compact == "リセットして":
            speech = "わかりました。エフェクトをリセットします。"
        else:
            speech = f"会話に合わせた応答 {self.calls} です。"
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={},
        )


class _SuccessfulMutatingResponder:
    adapter_kind = "projection_effect_mutating_responder"
    provider = "test"
    model = "test"

    def __init__(self, *, speech: str, display: str | None = None) -> None:
        self.calls = 0
        self.speech = speech
        self.display = speech if display is None else display

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ResponderResult(
            speech=self.speech,
            display=self.display,
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
                    self.assertEqual(responder.calls, 1)
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
                responder = _StaticResponder()
                events = ThoughtLoop(responder=responder).run_dicts(
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
                self.assertEqual(responder.calls, 1)
                self.assertEqual(
                    sum(event["type"] == "responder.started" for event in events),
                    1,
                )
                self.assertEqual(
                    sum(event["type"] == "responder.completed" for event in events),
                    1,
                )
                message = [
                    event for event in events if event["type"] == "assistant.message"
                ][-1]
                self.assertTrue(message["data"]["phrase_generation"]["used_llm"])
                self.assertEqual(
                    events[-1]["data"]["status"],
                    "projection_effect_requested",
                )

    def test_accepted_request_uses_bounded_continuity_for_companion_wording_only(
        self,
    ) -> None:
        responder = _StaticResponder()
        loop = ThoughtLoop(responder=responder)
        history_marker = "今日は少し雑談しましょう。"
        loop.run_dicts(self._turn(10, history_marker))

        events = loop.run_dicts(
            {
                **self._turn(11, "それなら、炎を見せてもらえますか？"),
                "session_id": "projection_effect_session_10",
            }
        )

        requested = [
            event for event in events if event["type"] == "projection.effect.requested"
        ]
        completed = next(
            event for event in events if event["type"] == "responder.completed"
        )
        companion_context = responder.response_contexts[-1]
        serialized_completed = json.dumps(completed, ensure_ascii=False)

        self.assertEqual(
            requested[0]["data"],
            {"schemaVersion": 1, "action": "start", "effectId": "fire"},
        )
        self.assertEqual(responder.calls, 2)
        self.assertEqual(
            companion_context["current_stage"],
            "projection_effect_companion_response",
        )
        self.assertIn("conversation_continuity", companion_context)
        self.assertNotIn("conversation_continuity", completed["data"]["response_context"])
        self.assertIn(
            "conversation_continuity_summary",
            completed["data"]["response_context"],
        )
        self.assertNotIn(history_marker, serialized_completed)

    def test_responder_failure_uses_fixed_non_echoing_companion_without_retry(
        self,
    ) -> None:
        responder = _FailingResponder()
        events = ThoughtLoop(responder=responder).run_dicts(
            self._turn(12, "雷を見せて")
        )
        serialized_publication = json.dumps(
            [
                event
                for event in events
                if event["type"]
                in {
                    "responder.completed",
                    "thought_core.response_route_classified",
                    "projection.effect.requested",
                    "assistant.speech_delta",
                    "assistant.message",
                    "turn.completed",
                }
            ],
            ensure_ascii=False,
        )

        self.assertEqual(responder.calls, 1)
        self.assertEqual(
            sum(event["type"] == "projection.effect.requested" for event in events),
            1,
        )
        self.assertNotIn("PRIVATE_PROJECTION_RESPONDER_FAILURE", serialized_publication)
        message = [
            event for event in events if event["type"] == "assistant.message"
        ][-1]
        self.assertEqual(message["data"]["speech"], "雷のエフェクトを出します。")
        self.assertFalse(message["data"]["phrase_generation"]["used_llm"])
        self.assertEqual(events[-1]["data"]["status"], "projection_effect_requested")

    def test_safe_contextual_companions_remain_varied_and_publish_once(self) -> None:
        cases = (
            (
                "炎を出して",
                "そうですね。では、炎のエフェクトを出します。",
                {"schemaVersion": 1, "action": "start", "effectId": "fire"},
            ),
            (
                "雷を見せて",
                "会話の流れに合わせて、雷のエフェクトを出しますね。",
                {
                    "schemaVersion": 1,
                    "action": "start",
                    "effectId": "thunderBall",
                },
            ),
        )

        for index, (text, companion, expected_payload) in enumerate(
            cases,
            start=58,
        ):
            with self.subTest(text=text):
                responder = _SuccessfulMutatingResponder(speech=companion)
                events = ThoughtLoop(responder=responder).run_dicts(
                    self._turn(index, text)
                )
                requested = [
                    event
                    for event in events
                    if event["type"] == "projection.effect.requested"
                ]
                messages = [
                    event
                    for event in events
                    if event["type"] == "assistant.message"
                ]

                self.assertEqual(responder.calls, 1)
                self.assertEqual(len(requested), 1)
                self.assertEqual(requested[0]["data"], expected_payload)
                self.assertEqual(messages[-1]["data"]["speech"], companion)
                self.assertEqual(messages[-1]["data"]["display"], companion)
                self.assertTrue(
                    messages[-1]["data"]["phrase_generation"]["used_llm"]
                )
                self.assertEqual(
                    events[-1]["data"]["status"],
                    "projection_effect_requested",
                )

    def test_successful_untrusted_companion_fails_closed_to_fixed_fallback(
        self,
    ) -> None:
        private_marker = "PRIVATE_CONTINUITY_MARKER"
        cases = (
            (
                "private continuity echo",
                "炎のエフェクトを出します。",
                f"{private_marker} 炎のエフェクトを出します。",
            ),
            (
                "opposite effect",
                "雷のエフェクトを出します。",
                None,
            ),
            (
                "unsupported parameter",
                "炎を右側に5秒出します。",
                None,
            ),
            (
                "unproved completion claim",
                "炎のエフェクトを出します。",
                "炎のエフェクトはもう表示されました。",
            ),
        )

        for index, (label, speech, display) in enumerate(cases, start=60):
            with self.subTest(label=label):
                responder = _SuccessfulMutatingResponder(
                    speech=speech,
                    display=display,
                )
                loop = ThoughtLoop(responder=responder)
                session_id = f"projection_effect_postcondition_{index}"
                if label == "private continuity echo":
                    loop.conversation_continuity.begin_turn(
                        session_id=session_id,
                        turn_id=f"prior_{index}",
                        user_text=private_marker,
                    )
                    loop.conversation_continuity.record_assistant(
                        session_id=session_id,
                        turn_id=f"prior_{index}",
                        assistant_text="確認しました。",
                    )

                events = loop.run_dicts(
                    {
                        **self._turn(index, "炎を出して"),
                        "session_id": session_id,
                    }
                )
                requested = [
                    event
                    for event in events
                    if event["type"] == "projection.effect.requested"
                ]
                messages = [
                    event
                    for event in events
                    if event["type"] == "assistant.message"
                ]
                serialized_presentation = json.dumps(
                    [
                        event
                        for event in events
                        if event["type"]
                        in {
                            "responder.completed",
                            "thought_core.response_route_classified",
                            "assistant.speech_delta",
                            "assistant.message",
                            "turn.completed",
                        }
                    ],
                    ensure_ascii=False,
                )

                self.assertEqual(responder.calls, 1)
                self.assertEqual(len(requested), 1)
                self.assertEqual(
                    requested[0]["data"],
                    {
                        "schemaVersion": 1,
                        "action": "start",
                        "effectId": "fire",
                    },
                )
                self.assertEqual(
                    messages[-1]["data"]["speech"],
                    "炎のエフェクトを出します。",
                )
                self.assertEqual(
                    messages[-1]["data"]["display"],
                    "炎のエフェクトを出します。",
                )
                self.assertFalse(
                    messages[-1]["data"]["phrase_generation"]["used_llm"]
                )
                self.assertNotIn(private_marker, serialized_presentation)
                self.assertNotIn("雷のエフェクトを出します", serialized_presentation)
                self.assertNotIn("右側に5秒", serialized_presentation)
                self.assertNotIn("表示されました", serialized_presentation)
                self.assertEqual(
                    events[-1]["data"]["status"],
                    "projection_effect_requested",
                )

    def test_negated_or_conditional_companions_cannot_reverse_fixed_action(
        self,
    ) -> None:
        cases = (
            (
                "炎を出して",
                "炎は出しません。",
                "炎のエフェクトを出します。",
                {"schemaVersion": 1, "action": "start", "effectId": "fire"},
            ),
            (
                "炎を出して",
                "炎を出せたら出します。",
                "炎のエフェクトを出します。",
                {"schemaVersion": 1, "action": "start", "effectId": "fire"},
            ),
            (
                "止めて",
                "エフェクトは止めません。",
                "エフェクトを止めます。",
                {"schemaVersion": 1, "action": "stop"},
            ),
            (
                "リセットして",
                "エフェクトはリセットしません。",
                "エフェクトをリセットします。",
                {"schemaVersion": 1, "action": "reset"},
            ),
            (
                "炎を出して",
                "炎を出しますか？",
                "炎のエフェクトを出します。",
                {"schemaVersion": 1, "action": "start", "effectId": "fire"},
            ),
            (
                "止めて",
                "エフェクトを止めますか？",
                "エフェクトを止めます。",
                {"schemaVersion": 1, "action": "stop"},
            ),
            (
                "リセットして",
                "エフェクトをリセットしますか？",
                "エフェクトをリセットします。",
                {"schemaVersion": 1, "action": "reset"},
            ),
        )

        for index, (request, rejected, fallback, expected_payload) in enumerate(
            cases,
            start=64,
        ):
            with self.subTest(request=request, rejected=rejected):
                responder = _SuccessfulMutatingResponder(speech=rejected)
                events = ThoughtLoop(responder=responder).run_dicts(
                    self._turn(index, request)
                )
                requested = [
                    event
                    for event in events
                    if event["type"] == "projection.effect.requested"
                ]
                message = [
                    event
                    for event in events
                    if event["type"] == "assistant.message"
                ][-1]
                serialized_presentation = json.dumps(
                    [
                        event
                        for event in events
                        if event["type"]
                        in {
                            "responder.completed",
                            "thought_core.response_route_classified",
                            "assistant.speech_delta",
                            "assistant.message",
                            "turn.completed",
                        }
                    ],
                    ensure_ascii=False,
                )

                self.assertEqual(responder.calls, 1)
                self.assertEqual(len(requested), 1)
                self.assertEqual(requested[0]["data"], expected_payload)
                self.assertEqual(message["data"]["speech"], fallback)
                self.assertEqual(message["data"]["display"], fallback)
                self.assertFalse(message["data"]["phrase_generation"]["used_llm"])
                self.assertNotIn(rejected, serialized_presentation)
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
        accepted_events = ThoughtLoop(responder=_StaticResponder()).run_dicts(
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
