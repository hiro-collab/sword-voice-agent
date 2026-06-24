import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error, request
from urllib.parse import parse_qs, urlsplit

from unittest import TestCase
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.reasoning import LocalActionReasoner  # noqa: E402
from thought_core.responders import ResponderResult, _response_context_prompt  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.server import create_server  # noqa: E402
from thought_core.tools import (  # noqa: E402
    HomeControlHttpTools,
    HomeControlToolConfig,
    MockThoughtTools,
    detect_home_action_intent,
)


TURN = {
    "text": "電気つけて",
    "turn_id": "turn_test_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "context_refs": {
        "environment_snapshot": "env_abc123",
        "voice_turn": "voice_789",
    },
}

GENERAL_TURN = {
    **TURN,
    "text": "今日は少し雑談しましょう。",
    "turn_id": "turn_general_001",
}


class StaticResponder:
    adapter_kind = "test_responder"
    provider = "test"
    model = "test-model"

    def __init__(self, *, used_llm: bool = True) -> None:
        self.used_llm = used_llm

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        return ResponderResult(
            speech="聞こえています。応答境界も動いています。",
            display="聞こえています。応答境界も動いています。",
            status="llm_response" if self.used_llm else "local_fallback",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=self.used_llm,
            metadata={"turn_text_len": len(turn.text)},
        )


class RecordingContextResponder:
    adapter_kind = "recording_context"
    provider = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.response_contexts: list[dict[str, object]] = []

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        self.response_contexts.append(dict(response_context or {}))
        count = len(self.response_contexts)
        speech = f"応答境界の確認 {count} です。"
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={"response_context_seen": bool(response_context)},
        )


class VisiblePhraseResponder:
    adapter_kind = "visible_phrase_test_responder"
    provider = "test"
    model = "test-model"

    def __init__(self) -> None:
        self.response_contexts: list[dict[str, object]] = []

    def respond(self, turn, *, response_context=None):  # type: ignore[no-untyped-def]
        context = dict(response_context or {})
        self.response_contexts.append(context)
        draft = str(context.get("semantic_draft") or "")
        if "つけ" in draft:
            speech = "LLM判断で、リビングの電気を点灯として扱います。"
        elif "ついた" in draft:
            speech = "LLM判断で、点灯後の状態まで確認しました。"
        else:
            speech = "LLM判断で、現在の状況に合わせて返します。"
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={"semantic_draft_seen": bool(draft)},
        )


class ThoughtCoreContractTest(TestCase):
    def test_common_metadata_is_carried_by_all_events(self) -> None:
        events = ThoughtLoop().run_dicts(TURN)

        self.assertGreater(len(events), 0)
        for expected_seq, event in enumerate(events, start=1):
            self.assertEqual(event["schema_version"], "thought-core.event.v0")
            self.assertTrue(event["event_id"].startswith("evt_"))
            self.assertEqual(event["turn_id"], "turn_test_001")
            self.assertEqual(event["session_id"], "living_room_main")
            self.assertEqual(event["seq"], expected_seq)
            self.assertEqual(event["source"], "thought-core")
            self.assertIn("timestamp", event)

    def test_event_order_for_minimal_light_demo(self) -> None:
        event_types = [event["type"] for event in ThoughtLoop().run_dicts(TURN)]

        self.assertEqual(
            event_types,
            [
                "input.acknowledged",
                "assistant.speech_delta",
                "assistant.message",
                "input.understood",
                "thought.stage",
                "tool.started",
                "tool.result",
                "memory.retrieved",
                "thought.stage",
                "tool.started",
                "tool.result",
                "observation.received",
                "thought.stage",
                "target_state.imagined",
                "thought.stage",
                "tool.started",
                "tool.result",
                "thought.stage",
                "command.planned",
                "action.proposed",
                "assistant.speech_delta",
                "assistant.message",
                "tool.started",
                "tool.result",
                "thought.stage",
                "tool.started",
                "tool.result",
                "observation.received",
                "thought.stage",
                "action.reviewed",
                "assistant.speech_delta",
                "assistant.message",
                "state_query.feedback_detected",
                "tool.started",
                "tool.result",
                "state_query.feedback_saved",
                "turn.completed",
            ],
        )

    def test_light_off_uses_home_tool_path(self) -> None:
        tools = MockThoughtTools(light_on=True)
        turn = {
            **TURN,
            "text": "リビングの電気を消して",
            "turn_id": "turn_light_off",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        action_event = next(event for event in events if event["type"] == "action.proposed")
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertNotIn("responder.started", event_types)
        self.assertEqual(action_event["data"]["action"]["action_id"], "light_off")
        self.assertEqual(action_event["data"]["action"]["expected_state"], "off")
        self.assertIn("了解、リビングの電気を消すね。", speeches)
        self.assertIn(
            "テストモード上では、リビングの電気を消した想定です。実家電には送っていません。",
            speeches,
        )
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_curtain_alias_routes_to_inner_door_actions(self) -> None:
        cases = {
            "カーテンを開けて": ("door_open", "open"),
            "カーテンを閉めて": ("door_close", "closed"),
            "カーテンを止めて": ("door_stop", "stopped"),
        }
        for text, (action_id, expected_state) in cases.items():
            with self.subTest(text=text):
                intent = detect_home_action_intent(text)

                self.assertIsNotNone(intent)
                self.assertEqual(intent.action_id, action_id)
                self.assertEqual(intent.target, "door")
                self.assertEqual(intent.expected_state, expected_state)

    def test_retrieved_memory_is_attached_to_action_context(self) -> None:
        tools = MockThoughtTools(light_on=False)
        turn = {
            **TURN,
            "text": "リビングの電気をつけて",
            "turn_id": "turn_memory_context",
            "context_refs": {
                **TURN["context_refs"],
                "mock_memory_items": [
                    {
                        "scope": "failure_patterns",
                        "memory_type": "failure_pattern",
                        "content": {
                            "action_id": "light_on",
                            "target": "light",
                            "expected_state": "on",
                            "summary": "照明の反映には少し待つ必要がある。",
                        },
                    }
                ],
            },
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        memory_event = next(event for event in events if event["type"] == "memory.retrieved")
        action_event = next(event for event in events if event["type"] == "action.proposed")
        memory_context = action_event["data"]["action"]["memory_context"]

        self.assertEqual(memory_event["data"]["item_count"], 1)
        self.assertEqual(memory_context["item_count"], 1)
        self.assertEqual(
            memory_context["items"][0]["content"]["summary"],
            "照明の反映には少し待つ必要がある。",
        )

    def test_short_memory_retrieve_reads_recent_tail_only(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_root = Path(tmp)
            short_memory_path = memory_root / "short_memory.jsonl"
            short_memory_path.parent.mkdir(parents=True, exist_ok=True)
            with short_memory_path.open("w", encoding="utf-8") as stream:
                for index in range(2500):
                    stream.write(
                        json.dumps(
                            {
                                "session_id": "other-session",
                                "turn_id": f"old-{index}",
                                "summary": "old-" + ("x" * 600),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                for index in range(4):
                    stream.write(
                        json.dumps(
                            {
                                "session_id": "living_room_main",
                                "turn_id": f"recent-{index}",
                                "summary": f"recent-{index}",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            self.assertGreater(short_memory_path.stat().st_size, 1_048_576)
            tools = HomeControlHttpTools(
                HomeControlToolConfig(
                    bridge_base_url="http://127.0.0.1:1",
                    api_token="token",
                    memory_root=str(memory_root),
                    memory_policy_root=str(memory_root / "policies"),
                    memory_retrieve_limit=3,
                )
            )

            result = tools.memory_retrieve(TurnInput.from_mapping(TURN))

        summaries = [item["content"]["summary"] for item in result["items"]]
        self.assertEqual(summaries, ["recent-3", "recent-2", "recent-1"])

    def test_target_state_projection_only_constrains_required_values(self) -> None:
        class NoisyEnvironmentTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                observation = super().environment_observe(turn, reason=reason)
                observation["environment"].setdefault("appliances", {})["unrelated"] = {
                    "state": "surprising",
                    "source": "mock",
                }
                observation["facts"].setdefault("devices", []).append(
                    {
                        "id": "unrelated",
                        "kind": "sensor",
                        "name": "無関係な値",
                        "state": "surprising",
                    }
                )
                return observation

        events = ThoughtLoop(tools=NoisyEnvironmentTools(light_on=False)).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_target_projection",
            }
        )
        target_event = next(
            event for event in events if event["type"] == "target_state.imagined"
        )
        plan_event = next(event for event in events if event["type"] == "command.planned")
        review_event = next(event for event in events if event["type"] == "action.reviewed")
        target_state = target_event["data"]["target_state"]

        self.assertEqual(
            target_state["wildcard_policy"],
            "unspecified_values_are_any",
        )
        self.assertEqual(target_state["bindings"][0]["target"], "light")
        self.assertEqual(target_state["bindings"][0]["value"], "on")
        self.assertEqual(plan_event["data"]["target_state_diff"]["status"], "mismatch")
        self.assertEqual(review_event["data"]["target_state_diff"]["status"], "matched")
        self.assertEqual(review_event["data"]["review_basis"], "target_state")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_target_state_already_satisfied_skips_execute(self) -> None:
        tools = MockThoughtTools(light_on=True)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_already_satisfied",
            }
        )
        plan_event = next(event for event in events if event["type"] == "command.planned")
        skipped_event = next(event for event in events if event["type"] == "action.skipped")
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] in {"tool.started", "tool.result"}
        ]

        self.assertEqual(plan_event["data"]["command_plan"]["status"], "already_satisfied")
        self.assertEqual(skipped_event["data"]["reason"], "target_state_already_satisfied")
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tools.execute_calls, [])
        self.assertEqual(events[-1]["data"]["status"], "noop")

    def test_action_review_can_use_injected_llm_reasoner_boundary(self) -> None:
        class FakeLlmReasoner:
            adapter_kind = "fake_llm_reasoner"
            provider = "test"
            model = "fake-model"

            def __init__(self) -> None:
                self.local = LocalActionReasoner()

            def imagine_target_state(self, turn, observation):  # type: ignore[no-untyped-def]
                return self.local.imagine_target_state(turn, observation)

            def plan_command(self, turn, observation, target_state, preview):  # type: ignore[no-untyped-def]
                return self.local.plan_command(turn, observation, target_state, preview)

            def review_target_state(  # type: ignore[no-untyped-def]
                self,
                turn,
                action,
                target_state,
                observation,
                execute_result,
            ):
                review = self.local.review_target_state(
                    turn,
                    action,
                    target_state,
                    observation,
                    execute_result,
                )
                review["reason"] = "fake_llm_target_state_review"
                review["judge"] = {
                    "adapter_kind": self.adapter_kind,
                    "provider": self.provider,
                    "model": self.model,
                    "used_llm": True,
                }
                return review

        events = ThoughtLoop(
            tools=MockThoughtTools(light_on=False),
            action_reasoner=FakeLlmReasoner(),
        ).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_fake_llm_reasoner",
            }
        )
        target_event = next(
            event for event in events if event["type"] == "target_state.imagined"
        )
        review_event = next(event for event in events if event["type"] == "action.reviewed")

        self.assertEqual(
            target_event["data"]["reasoner"]["adapter_kind"],
            "fake_llm_reasoner",
        )
        self.assertEqual(review_event["data"]["reason"], "fake_llm_target_state_review")
        self.assertTrue(review_event["data"]["judge"]["used_llm"])
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_room_light_state_query_uses_environment_without_home_execute(self) -> None:
        tools = MockThoughtTools(light_on=True)
        turn = {
            **TURN,
            "text": "電気ついてる？",
            "turn_id": "turn_room_light_state_query",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertIn("environment.state_query_answer", event_types)
        self.assertEqual(understood["data"]["kind"], "state_query")
        self.assertTrue(understood["data"]["is_question"])
        self.assertNotIn("responder.started", event_types)
        self.assertEqual(tool_names, ["memory.retrieve", "environment.observe"])
        self.assertEqual(tools.execute_calls, [])
        self.assertIn("カメラ推定", speeches[-1])
        self.assertEqual(events[-1]["data"]["status"], "state_answer")
        self.assertEqual(events[-1]["data"]["state"], "on")

    def test_room_light_state_query_saves_followup_user_feedback(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools)
        query_turn = {
            **TURN,
            "text": "電気ついてる？",
            "turn_id": "turn_room_light_query_feedback",
            "context_refs": {
                **TURN["context_refs"],
                "mock_room_light_state": "unknown",
                "mock_room_light_confidence_label": "low",
            },
        }
        feedback_turn = {
            **TURN,
            "text": "ついてるよ",
            "turn_id": "turn_room_light_user_feedback",
        }

        query_events = loop.run_dicts(query_turn)
        feedback_events = loop.run_dicts(feedback_turn)
        feedback_event_types = [event["type"] for event in feedback_events]
        understood = next(
            event for event in feedback_events if event["type"] == "input.understood"
        )

        self.assertIn("state_query.feedback_pending", [event["type"] for event in query_events])
        self.assertEqual(understood["data"]["kind"], "state_feedback")
        self.assertEqual(understood["data"]["asserted_state"], "on")
        self.assertIn("state_query.feedback_saved", feedback_event_types)
        self.assertEqual(feedback_events[-1]["data"]["status"], "state_feedback")
        self.assertEqual(tools.state_query_feedback_calls[0]["user_label"], "on")
        self.assertEqual(
            tools.state_query_feedback_calls[0]["feedback_reason"],
            "user_correction_after_state_query",
        )

    def test_pending_room_light_feedback_can_continue_as_home_command(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools)
        query_turn = {
            **TURN,
            "text": "電気ついてる？",
            "turn_id": "turn_room_light_query_before_command",
            "context_refs": {
                **TURN["context_refs"],
                "mock_room_light_state": "unknown",
                "mock_room_light_confidence_label": "low",
            },
        }
        command_turn = {
            **TURN,
            "text": "はい、電気を消して",
            "turn_id": "turn_feedback_then_light_off",
        }

        loop.run_dicts(query_turn)
        events = loop.run_dicts(command_turn)
        event_types = [event["type"] for event in events]
        action_event = next(event for event in events if event["type"] == "action.proposed")
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertEqual(understood["data"]["kind"], "state_feedback")
        self.assertTrue(understood["data"]["continued_as_command"])
        self.assertIn("state_query.feedback_saved", event_types)
        self.assertEqual(tools.state_query_feedback_calls[0]["user_label"], "on")
        self.assertEqual(action_event["data"]["action"]["action_id"], "light_off")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_direct_room_light_state_statement_is_saved_as_feedback(self) -> None:
        tools = MockThoughtTools(light_on=True)
        turn = {
            **TURN,
            "text": "現在、部屋の電気は消灯状態です",
            "turn_id": "turn_direct_room_light_feedback",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)

        self.assertIn("state_query.feedback_saved", [event["type"] for event in events])
        self.assertEqual(events[-1]["data"]["status"], "state_feedback")
        self.assertEqual(tools.state_query_feedback_calls[0]["user_label"], "off")
        self.assertEqual(
            tools.state_query_feedback_calls[0]["feedback_reason"],
            "user_reported_room_light_state",
        )

    def test_direct_room_light_feedback_keeps_probability_evidence(self) -> None:
        class ProbabilityRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                observation = super().environment_observe(turn, reason=reason)
                room_light = observation["environment"]["state_queries"]["room_light"]
                room_light["evidence"].update(
                    {
                        "electric_on_probability": 0.72,
                        "daylight_present_probability": 0.11,
                        "dark_probability": 0.08,
                        "confidence": 0.64,
                    }
                )
                observation["facts"]["state_queries"]["room_light"] = room_light
                return observation

        tools = ProbabilityRoomLightTools(light_on=True)
        turn = {
            **TURN,
            "text": "今電気はついています",
            "turn_id": "turn_direct_room_light_probability_feedback",
        }

        ThoughtLoop(tools=tools).run_dicts(turn)

        evidence = tools.state_query_feedback_calls[0]["pending"]["evidence"]
        self.assertEqual(evidence["electric_on_probability"], 0.72)
        self.assertEqual(evidence["daylight_present_probability"], 0.11)
        self.assertEqual(evidence["dark_probability"], 0.08)
        self.assertEqual(evidence["confidence"], 0.64)

    def test_direct_room_light_feedback_reports_save_failure(self) -> None:
        class FailingFeedbackTools(MockThoughtTools):
            def state_query_feedback(self, turn, payload):  # type: ignore[no-untyped-def]
                self.state_query_feedback_calls.append(dict(payload))
                return {
                    "status": "failed",
                    "ok": False,
                    "error": "environment_feedback_unconfigured",
                }

        tools = FailingFeedbackTools(light_on=True)
        turn = {
            **TURN,
            "text": "今電気はついています",
            "turn_id": "turn_direct_room_light_feedback_failure",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        saved = next(event for event in events if event["type"] == "state_query.feedback_saved")
        fallback = next(
            event for event in events if event["type"] == "state_query.feedback_fallback_saved"
        )
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertFalse(saved["data"]["ok"])
        self.assertEqual(saved["data"]["error"], "environment_feedback_unconfigured")
        self.assertTrue(fallback["data"]["written"])
        self.assertEqual(fallback["data"]["user_label"], "on")
        self.assertTrue(any("この会話の記憶には反映" in speech for speech in speeches))
        self.assertTrue(any("学習ログ本体はあとで同期が必要" in speech for speech in speeches))
        self.assertFalse(any("まだ反映できていません" in speech for speech in speeches))

    def test_direct_room_light_feedback_does_not_resume_stale_action_review(self) -> None:
        tools = MockThoughtTools(light_on=False)
        turn = {
            **TURN,
            "text": "はい、電気は消えています",
            "turn_id": "turn_direct_feedback_with_stale_review_memory",
            "context_refs": {
                **TURN["context_refs"],
                "mock_memory_items": [
                    {
                        "scope": "short_memory",
                        "memory_type": "action_retry",
                        "content": {
                            "action": {
                                "action_id": "light_on",
                                "target": "light",
                                "target_name": "リビングの電気",
                                "expected_state": "on",
                                "pre_action_phrase": "リビングの電気をつける",
                            },
                            "retry_budget": {
                                "status": "review_budget_opened",
                                "settle_ms": 1500,
                                "observation_attempts": 2,
                                "auto_retries": 1,
                                "progress": {
                                    "observations_done": 1,
                                    "execute_attempts": 1,
                                },
                            },
                            "last_review": {
                                "status": "mismatch",
                                "evidence": {
                                    "state": "off",
                                    "confidence_label": "high",
                                },
                            },
                            "turn_id": "old_light_on_turn",
                            "issued_at": "2026-05-08T00:00:00+00:00",
                        },
                    }
                ],
            },
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertEqual(understood["data"]["kind"], "state_feedback")
        self.assertIn("state_query.feedback_saved", event_types)
        self.assertNotIn("action.retrying", event_types)
        self.assertNotIn("action.proposed", event_types)
        self.assertEqual(tools.execute_calls, [])
        self.assertEqual(tools.state_query_feedback_calls[0]["user_label"], "off")
        self.assertEqual(events[-1]["data"]["status"], "state_feedback")

    def test_room_light_question_does_not_become_pending_feedback(self) -> None:
        tools = MockThoughtTools(light_on=False)
        loop = ThoughtLoop(tools=tools)
        action_turn = {
            **TURN,
            "text": "リビングの電気をつけて",
            "turn_id": "turn_pending_feedback_before_question",
            "context_refs": {
                **TURN["context_refs"],
                "mock_after_action_room_light_state": "off",
                "mock_after_action_room_light_confidence_label": "high",
                "mock_after_action_wait_matched": "true",
            },
        }
        question_turn = {
            **TURN,
            "text": "今電気はついてるでしょうか",
            "turn_id": "turn_pending_feedback_state_question",
        }

        action_events = loop.run_dicts(action_turn)
        question_events = loop.run_dicts(question_turn)
        question_event_types = [event["type"] for event in question_events]
        understood = next(
            event for event in question_events if event["type"] == "input.understood"
        )

        self.assertTrue(action_events[-1]["data"]["post_action_feedback_pending"])
        self.assertEqual(understood["data"]["kind"], "state_query")
        self.assertTrue(understood["data"]["is_question"])
        self.assertIn("environment.state_query_answer", question_event_types)
        self.assertNotIn("state_query.feedback_saved", question_event_types)
        self.assertEqual(question_events[-1]["data"]["status"], "state_answer")
        self.assertEqual(tools.state_query_feedback_calls, [])
        self.assertEqual(len(tools.execute_calls), 1)
        issue_keys = {
            event["data"].get("speech_context", {}).get("issue_key")
            for event in question_events
            if event["type"] in {"assistant.speech_delta", "assistant.message"}
        }
        self.assertTrue(any(":state:" in str(key) for key in issue_keys))
        self.assertFalse(any(":home:" in str(key) for key in issue_keys))

    def test_light_action_saves_verified_post_action_feedback(self) -> None:
        tools = MockThoughtTools(light_on=False)
        turn = {
            **TURN,
            "text": "リビングの電気をつけて",
            "turn_id": "turn_light_action_learning",
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)

        self.assertIn("state_query.feedback_saved", [event["type"] for event in events])
        self.assertEqual(events[-1]["data"]["post_action_feedback_saved"], True)
        self.assertEqual(tools.state_query_feedback_calls[0]["user_label"], "on")
        self.assertEqual(
            tools.state_query_feedback_calls[0]["source_context"],
            "post_light_action",
        )

    def test_light_action_adds_room_light_feedback_when_vision_mismatches(self) -> None:
        tools = MockThoughtTools(light_on=False)
        turn = {
            **TURN,
            "text": "リビングの電気をつけて",
            "turn_id": "turn_room_light_feedback",
            "context_refs": {
                **TURN["context_refs"],
                "mock_after_action_room_light_state": "off",
                "mock_after_action_room_light_confidence_label": "high",
                "mock_after_action_wait_matched": "true",
            },
        }

        events = ThoughtLoop(tools=tools).run_dicts(turn)
        event_types = [event["type"] for event in events]
        message = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]
        pending = next(
            event for event in events if event["type"] == "state_query.feedback_pending"
        )

        self.assertIn("state_query.feedback_pending", event_types)
        self.assertIn("映像", message)
        self.assertEqual(pending["data"]["state_query_id"], "room_light")
        self.assertEqual(pending["data"]["expected_state"], "on")
        self.assertEqual(pending["data"]["predicted_state"], "off")
        self.assertTrue(events[-1]["data"]["post_action_feedback_pending"])
        self.assertEqual(events[-1]["data"]["room_light_wait_matched"], True)

    def test_environment_noop_action_skips_home_execute(self) -> None:
        class NoopLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                observation = super().environment_observe(turn, reason=reason)
                observation["environment"]["actions"] = [
                    {
                        "action_id": "light_off",
                        "aliases": ["電気を消して"],
                        "label": "ライトを消す",
                        "appliance_id": "light",
                        "target_label": "電気",
                        "verb": "消す",
                        "pre_action_phrase": "電気を消す",
                        "expected_state": "off",
                        "available": False,
                        "noop": True,
                        "reason": "already_off",
                        "reason_text": "電気はすでに消えています",
                    }
                ]
                return observation

        tools = NoopLightTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気を消して",
                "turn_id": "turn_light_noop",
            }
        )
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        message = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]

        self.assertIn("action.skipped", [event["type"] for event in events])
        self.assertEqual(tool_names, ["memory.retrieve", "environment.observe", "home.preview"])
        self.assertEqual(tools.execute_calls, [])
        self.assertIn("すでに消えています", message)
        self.assertEqual(events[-1]["data"]["status"], "noop")

    def test_environment_action_readiness_metadata_reaches_home_action(self) -> None:
        class ReadinessMetadataTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                observation = super().environment_observe(turn, reason=reason)
                observation["environment"]["action_readiness"] = {
                    "schema_version": "home_control_action_readiness.v0",
                    "test_now_count": 1,
                    "blocked_candidate_count": 0,
                    "proof_ceilings": {"ha_visible_vacuum_return_checkstate_layer": 1},
                }
                observation["environment"]["actions"] = [
                    {
                        "action_id": "vacuum_return",
                        "aliases": ["掃除機を戻して"],
                        "label": "掃除機を戻す",
                        "appliance_id": "vacuum",
                        "target_label": "掃除機",
                        "verb": "戻す",
                        "pre_action_phrase": "掃除機を戻す",
                        "expected_state": "returning",
                        "control_type": "stateful_command",
                        "state_authority": "home_assistant",
                        "verification_mode": "ha_state",
                        "state_tracking": "tracked",
                        "proof_ceiling": "ha_visible_vacuum_return_checkstate_layer",
                        "live_test_readiness": "test_now",
                        "live_test_blockers": [],
                        "restore_action_id": "",
                        "stop_action_id": "",
                        "terminal_action": True,
                        "safety_requirements": [],
                    }
                ]
                return observation

        tools = ReadinessMetadataTools()
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "掃除機を戻して",
                "turn_id": "turn_vacuum_return_readiness_metadata",
            }
        )
        action_event = next(event for event in events if event["type"] == "action.proposed")
        proposed = action_event["data"]["action"]

        self.assertEqual(proposed["action_id"], "vacuum_return")
        self.assertEqual(proposed["state_tracking"], "tracked")
        self.assertEqual(proposed["verification_mode"], "ha_state")
        self.assertEqual(proposed["state_authority"], "home_assistant")
        self.assertEqual(
            proposed["proof_ceiling"],
            "ha_visible_vacuum_return_checkstate_layer",
        )
        self.assertEqual(proposed["live_test_readiness"], "test_now")
        self.assertTrue(proposed["terminal_action"])
        self.assertEqual(tools.execute_calls[0]["proof_ceiling"], proposed["proof_ceiling"])
        self.assertEqual(
            tools.execute_calls[0]["live_test_readiness"],
            proposed["live_test_readiness"],
        )

    def test_vacuum_return_uses_canonical_action_id_dictionary(self) -> None:
        tools = MockThoughtTools()
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "掃除機を戻して",
                "turn_id": "turn_vacuum_return",
            }
        )
        action_event = next(event for event in events if event["type"] == "action.proposed")
        message = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]

        self.assertEqual(action_event["data"]["action"]["action_id"], "vacuum_return")
        self.assertIn("掃除機を戻した", message)
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_mock_action_success_speech_is_not_live_device_claim(self) -> None:
        tools = MockThoughtTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気をつけて",
                "turn_id": "turn_mock_action_claim_boundary",
            }
        )
        visible_speech = " ".join(
            str(event["data"].get("speech") or "")
            for event in events
            if event["type"] == "assistant.message"
        )
        execute_result = next(
            event["data"]["result"]
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        )

        self.assertIn("テストモード", visible_speech)
        self.assertIn("実家電には送っていません", visible_speech)
        self.assertNotIn("リビングの電気をつけたよ。", visible_speech)
        self.assertFalse(execute_result["real_execution"])
        self.assertFalse(execute_result["verified_by_bridge"])
        self.assertEqual(execute_result["adapter"], "mock")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_all_cataloged_home_action_examples_route_to_action_ids(self) -> None:
        catalog = json.loads(
            (REPO_ROOT / "catalogs" / "actions" / "home-actions.json").read_text(
                encoding="utf-8"
            )
        )

        for action_id, action in catalog["actions"].items():
            phrase = action["intent_examples"][0]
            with self.subTest(action_id=action_id, phrase=phrase):
                intent = detect_home_action_intent(phrase)

                self.assertIsNotNone(intent)
                assert intent is not None
                self.assertEqual(intent.action_id, action_id)
                self.assertEqual(intent.target, action["target"])
                self.assertEqual(intent.expected_state, action["expected_state"])

    def test_conflicting_or_multi_home_action_turns_do_not_partially_execute(self) -> None:
        cases = (
            ("リビングの電気をつけて、それから消して", True),
            ("電気をつけて、扇風機もつけて", False),
        )

        for text, light_on in cases:
            with self.subTest(text=text):
                tools = MockThoughtTools(light_on=light_on)
                events = ThoughtLoop(tools=tools, responder=StaticResponder()).run_dicts(
                    {
                        **TURN,
                        "text": text,
                        "turn_id": f"turn_multi_action_guard_{len(text)}",
                    }
                )
                event_types = [event["type"] for event in events]
                tool_names = [
                    event["data"]["tool"]
                    for event in events
                    if event["type"] == "tool.started"
                ]
                understood = next(
                    event for event in events if event["type"] == "input.understood"
                )

                self.assertEqual(understood["data"]["kind"], "general")
                self.assertNotIn("action.proposed", event_types)
                self.assertNotIn("command.planned", event_types)
                self.assertNotIn("home.execute", tool_names)
                self.assertEqual(tools.execute_calls, [])

    def test_negative_home_action_wording_does_not_execute(self) -> None:
        cases = (
            "電気をつけないで",
            "扇風機をつけなくていい",
            "エアコンを消さないで",
            "中扉を開けないで",
            "掃除機を動かさないで",
        )

        for text in cases:
            with self.subTest(text=text):
                tools = MockThoughtTools(light_on=False)
                events = ThoughtLoop(tools=tools, responder=StaticResponder()).run_dicts(
                    {
                        **TURN,
                        "text": text,
                        "turn_id": f"turn_negative_action_{len(text)}",
                    }
                )
                event_types = [event["type"] for event in events]
                tool_names = [
                    event["data"]["tool"]
                    for event in events
                    if event["type"] == "tool.started"
                ]

                self.assertNotIn("action.proposed", event_types)
                self.assertNotIn("command.planned", event_types)
                self.assertNotIn("home.execute", tool_names)
                self.assertEqual(tools.execute_calls, [])

    def test_home_control_http_tools_call_bridge_execute(self) -> None:
        calls: list[dict[str, object]] = []
        state = {"light": "on"}

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                calls.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                self._send_json(
                    {
                        "snapshot_id": "env_test",
                        "appliances": {
                            "light": {
                                "state": state["light"],
                                "updated_at": "2026-05-08T00:00:00+00:00",
                                "source": "home_assistant",
                            }
                        },
                        "last_home_assistant_events": [],
                        "state_queries": {},
                    }
                )

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                if self.path == "/actions/light_off/preview":
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "light_off",
                            "executed": False,
                            "status": "preview",
                            "confirmation_required": False,
                            "message": "preview",
                            "speak": "preview",
                            "expected_state": "off",
                            "expected_effect": {"expected_state": "off"},
                        }
                    )
                    return
                if self.path == "/actions/light_off/execute":
                    state["light"] = "off"
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "light_off",
                            "executed": True,
                            "status": "submitted",
                            "confirmation_required": False,
                            "message": "done",
                            "speak": "done",
                            "execution_id": "exec_test",
                            "issued_at": "2026-05-08T00:00:00+00:00",
                            "expected_state": "off",
                            "expected_effect": {"expected_state": "off"},
                        }
                    )
                    return
                self._send_json({"ok": False, "error": "not_found"}, status=404)

            def log_message(self, format, *args):  # type: ignore[no-untyped-def]  # noqa: A002
                return

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            tools = HomeControlHttpTools(
                HomeControlToolConfig(
                    bridge_base_url=base_url,
                    api_token="bridge-token",
                    environment_state_url=f"{base_url}/environment/current",
                    environment_api_token="environment-token",
                    timeout_s=2,
                )
            )
            loop = ThoughtLoop(tools=tools)
            events = loop.run_dicts(
                {
                    **TURN,
                    "text": "リビングの電気を消して",
                    "turn_id": "turn_bridge_light_off",
                }
            )
            review_events = loop.run_dicts(
                {
                    **TURN,
                    "text": "確認して",
                    "turn_id": "turn_bridge_light_off_review",
                }
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(events[-1]["data"]["status"], "verification_pending")
        self.assertIn("action.review_pending", [event["type"] for event in events])
        self.assertEqual(review_events[-1]["data"]["status"], "success")
        post_paths = [call["path"] for call in calls if call["method"] == "POST"]
        self.assertEqual(post_paths, ["/actions/light_off/preview", "/actions/light_off/execute"])
        get_paths = [str(call["path"]) for call in calls if call["method"] == "GET"]
        self.assertEqual(get_paths[0], "/environment/current")
        wait_paths = [
            path for path in get_paths if path.startswith("/environment/current?")
        ]
        self.assertEqual(len(wait_paths), 1)
        query = parse_qs(urlsplit(wait_paths[0]).query)
        self.assertEqual(query["wait_for"], ["room_light"])
        self.assertEqual(query["after"], ["2026-05-08T00:00:02+00:00"])
        self.assertGreaterEqual(
            int(query["timeout_ms"][0]),
            1500,
        )
        execute_call = calls[2]
        self.assertEqual(execute_call["authorization"], "Bearer bridge-token")
        self.assertEqual(execute_call["body"]["source"], "thought-core")
        self.assertEqual(execute_call["body"]["request_id"], "turn_bridge_light_off-attempt-1")

    def test_home_control_http_tools_sanitizes_non_finite_feedback_payload(self) -> None:
        calls: list[dict[str, object]] = []

        class FeedbackHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                self._send_json(
                    {
                        "ok": True,
                        "feedback_id": "sqf_test_non_finite",
                        "received_snapshot_id": "env_test",
                        "duplicate": False,
                        "status": "accepted",
                        "warnings": [],
                    }
                )

            def log_message(self, format, *args):  # type: ignore[no-untyped-def]  # noqa: A002
                return

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), FeedbackHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/feedback/state-query"
            tools = HomeControlHttpTools(
                HomeControlToolConfig(
                    bridge_base_url="http://127.0.0.1:1",
                    api_token="bridge-token",
                    environment_feedback_url=url,
                    environment_api_token="environment-token",
                    timeout_s=2,
                )
            )
            result = tools.state_query_feedback(
                TurnInput.from_mapping({**TURN, "turn_id": "turn_non_finite_feedback"}),
                {
                    "target": "room_light",
                    "state_query_id": "room_light",
                    "user_label": "on",
                    "pending": {
                        "evidence": {
                            "electric_on_probability": float("nan"),
                            "confidence": float("inf"),
                        }
                    },
                },
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(result["status"], "accepted")
        body = calls[0]["body"]
        evidence = body["pending"]["evidence"]  # type: ignore[index]
        self.assertIsNone(evidence["electric_on_probability"])
        self.assertIsNone(evidence["confidence"])
        self.assertEqual(calls[0]["authorization"], "Bearer environment-token")

    def test_action_review_checkpoints_use_two_and_five_second_snapshots(self) -> None:
        tools = HomeControlHttpTools(
            HomeControlToolConfig(
                bridge_base_url="http://127.0.0.1:1",
                api_token="bridge-token",
                environment_state_url="http://127.0.0.1:1/environment/current",
                environment_api_token="environment-token",
            )
        )
        loop = ThoughtLoop(tools=tools)
        turn = TurnInput.from_mapping({**TURN, "turn_id": "turn_checkpoint"})
        policy = loop._action_review_policy({"action_id": "light_on"})

        first = loop._prepare_action_review_checkpoint(
            turn,
            {"issued_at": "2026-05-08T00:00:00+00:00"},
            policy=policy,
            observations_done=0,
        )
        second = loop._prepare_action_review_checkpoint(
            turn,
            {"issued_at": "2026-05-08T00:00:00+00:00"},
            policy=policy,
            observations_done=1,
        )

        self.assertEqual(policy["checkpoint_ms"], [2000, 5000])
        self.assertEqual(first["checkpoint_ms"], 2000)
        self.assertEqual(first["wait_after"], "2026-05-08T00:00:02+00:00")
        self.assertEqual(second["checkpoint_ms"], 5000)
        self.assertEqual(second["wait_after"], "2026-05-08T00:00:05+00:00")
        self.assertEqual(
            tools.room_light_wait_after_by_turn["turn_checkpoint"],
            "2026-05-08T00:00:05+00:00",
        )

    def test_confirm_required_action_waits_for_user_confirmation(self) -> None:
        calls: list[dict[str, object]] = []
        state = {"door": "open"}

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                calls.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                self._send_json(
                    {
                        "snapshot_id": "env_test",
                        "appliances": {
                            "door": {
                                "state": state["door"],
                                "updated_at": "2026-05-08T00:00:00+00:00",
                                "source": "home_assistant",
                            }
                        },
                        "last_home_assistant_events": [],
                        "state_queries": {},
                    }
                )

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                calls.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                    }
                )
                if self.path == "/actions/door_close/preview":
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "door_close",
                            "executed": False,
                            "status": "preview",
                            "confirmation_required": True,
                            "confirmation_token": "confirm-token-1",
                            "message": "中扉を閉めるを実行します。よろしいですか？",
                            "speak": "中扉を閉めるを実行します。よろしいですか？",
                            "expected_state": "closed",
                            "expected_effect": {"expected_state": "closed"},
                        }
                    )
                    return
                if self.path == "/actions/door_close/execute":
                    if (
                        body.get("confirmed") is True
                        and body.get("confirmation_token") == "confirm-token-1"
                    ):
                        state["door"] = "closed"
                        self._send_json(
                            {
                                "ok": True,
                                "action_id": "door_close",
                                "executed": True,
                                "status": "submitted",
                                "confirmation_required": True,
                                "message": "中扉を閉めました。",
                                "speak": "中扉を閉めました。",
                                "execution_id": "exec_door_close",
                                "expected_state": "closed",
                                "expected_effect": {"expected_state": "closed"},
                            }
                        )
                        return
                    self._send_json(
                        {
                            "ok": True,
                            "action_id": "door_close",
                            "executed": False,
                            "status": "confirmation_required",
                            "confirmation_required": True,
                            "confirmation_token": "confirm-token-2",
                            "message": "確認が必要です。",
                            "speak": "確認が必要です。",
                        }
                    )
                    return
                self._send_json({"ok": False, "error": "not_found"}, status=404)

            def log_message(self, format, *args):  # type: ignore[no-untyped-def]  # noqa: A002
                return

            def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), BridgeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            loop = ThoughtLoop(
                tools=HomeControlHttpTools(
                    HomeControlToolConfig(
                        bridge_base_url=base_url,
                        api_token="bridge-token",
                        environment_state_url=f"{base_url}/environment/current",
                        environment_api_token="environment-token",
                        timeout_s=2,
                    )
                )
            )
            first_events = loop.run_dicts(
                {
                    **TURN,
                    "text": "中扉を閉めて",
                    "turn_id": "turn_door_close_preview",
                }
            )
            second_events = loop.run_dicts(
                {
                    **TURN,
                    "text": "お願い",
                    "turn_id": "turn_door_close_confirm",
                }
            )
            third_events = loop.run_dicts(
                {
                    **TURN,
                    "text": "お願い",
                    "turn_id": "turn_door_close_duplicate_confirm",
                }
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        first_messages = [
            event["data"]["speech"]
            for event in first_events
            if event["type"] == "assistant.message"
        ]
        second_messages = [
            event["data"]["speech"]
            for event in second_events
            if event["type"] == "assistant.message"
        ]
        second_stages = [
            event["data"]["stage"]
            for event in second_events
            if event["type"] == "thought.stage"
        ]
        post_calls = [call for call in calls if call["method"] == "POST"]
        third_tool_names = [
            event["data"]["tool"]
            for event in third_events
            if event["type"] == "tool.started"
        ]

        self.assertEqual(first_events[-1]["data"]["status"], "confirmation_required")
        self.assertIn("まだ実行していません", first_messages[-1])
        self.assertEqual([call["path"] for call in post_calls], [
            "/actions/door_close/preview",
            "/actions/door_close/execute",
        ])
        self.assertNotIn("action.confirmed", [event["type"] for event in third_events])
        self.assertNotIn("home.execute", third_tool_names)
        execute_body = post_calls[1]["body"]
        self.assertEqual(execute_body["confirmed"], True)
        self.assertEqual(execute_body["confirmation_token"], "confirm-token-1")
        self.assertEqual(second_events[-1]["data"]["status"], "success")
        self.assertIn("中扉を閉める操作を送信しました。反映後の状態を確認します。", second_messages)
        self.assertIn("中扉を閉めました。", second_messages[-1])
        self.assertIn("environment.observe.after_action", second_stages)
        self.assertIn("action.review", second_stages)
        serialized = json.dumps(first_events + second_events, ensure_ascii=False)
        self.assertNotIn("confirm-token-1", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_action_waits_for_multiple_observations_before_giving_up(self) -> None:
        class UnobservableDoorTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": "cmd_unobservable",
                    "attempt": len(self.execute_calls),
                }

        tools = UnobservableDoorTools()
        loop = ThoughtLoop(tools=tools)
        events = loop.run_dicts(
            {
                **TURN,
                "text": "中扉を閉めて",
                "turn_id": "turn_unobservable_door_1",
            }
        )

        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertIn("action.review_pending", [event["type"] for event in events])
        self.assertIn("feedback.requested", [event["type"] for event in events])
        short_memory_statuses = [
            item["retry_budget"]["status"] for item in tools.short_memory_write_calls
        ]
        self.assertIn("review_budget_opened", short_memory_statuses)
        self.assertIn("review_retry_exhausted", short_memory_statuses)
        self.assertEqual(tools.short_memory_write_calls[0]["type"], "short_memory")
        self.assertEqual(
            tools.short_memory_write_calls[0]["retry_budget"]["observation_attempts"],
            2,
        )

    def test_low_risk_action_does_not_retry_after_review_exhausted(self) -> None:
        class RetryableLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                state = "on" if len(self.execute_calls) >= 2 else "off"
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{len(self.execute_calls)}_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {
                        "devices": [
                            {
                                "id": "living_room_light",
                                "kind": "light",
                                "name": "リビングの電気",
                                "state": state,
                            }
                        ],
                        "state_queries": {},
                    },
                    "environment": {
                        "appliances": {"light": {"state": state}},
                        "state_queries": {},
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                }

        tools = RetryableLightTools()
        loop = ThoughtLoop(tools=tools)
        events = loop.run_dicts(
            {
                **TURN,
                "text": "電気をつけて",
                "turn_id": "turn_retry_review_1",
            }
        )

        event_types = [event["type"] for event in events]

        self.assertNotIn("action.retrying", event_types)
        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 1)
        retry_budget_items = [
            item
            for item in tools.short_memory_write_calls
            if item["retry_budget"]["status"] == "review_retry_scheduled"
        ]
        self.assertEqual(len(retry_budget_items), 0)

    def test_uncertain_room_light_review_does_not_retry_light_off(self) -> None:
        class UncertainRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                room_light = {
                    "available": True,
                    "stale": False,
                    "state": "unknown",
                    "confidence_label": "low",
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": "映像推定では断定できない。",
                    "observed_at": "2026-05-08T00:00:00+00:00",
                    "updated_at": "2026-05-08T00:00:00+00:00",
                    "evidence": {
                        "source": "mock",
                        "topic": "/vision/room_light/state",
                        "lighting_type": "daylight",
                    },
                }
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{len(self.execute_calls)}_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {
                        "devices": [],
                        "state_queries": {"room_light": room_light},
                    },
                    "environment": {
                        "appliances": {},
                        "state_queries": {"room_light": room_light},
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                    "expected_state": action.get("expected_state"),
                }

        tools = UncertainRoomLightTools(light_on=True)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気を消して",
                "turn_id": "turn_light_off_uncertain_review",
            }
        )
        event_types = [event["type"] for event in events]
        retry_budget_items = [
            item
            for item in tools.short_memory_write_calls
            if item["retry_budget"]["status"] == "review_budget_opened"
        ]
        feedback_event = next(
            event for event in events if event["type"] == "feedback.requested"
        )

        self.assertNotIn("action.retrying", event_types)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(tools.execute_calls[0]["action_id"], "light_off")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(retry_budget_items[0]["retry_budget"]["auto_retries"], 0)
        self.assertEqual(
            feedback_event["data"]["last_review"]["reason"],
            "target_state_unverified",
        )

    def test_legacy_effective_room_light_fields_do_not_satisfy_review(self) -> None:
        class LegacyEffectiveRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                executed = bool(self.execute_calls)
                raw_state = "unknown" if executed else "on"
                room_light = {
                    "available": True,
                    "stale": False,
                    "state": raw_state,
                    "confidence_label": "low" if executed else "medium",
                    "effective_state": "off" if executed else "on",
                    "effective_confidence_label": "high" if executed else "medium",
                    "effective_authority": "environment_state_server.calibration.home_assistant",
                    "effective_answer_hint": "学習済みの操作履歴と Home Assistant の直近状態で補正しています。",
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": "映像推定では断定できない。",
                }
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{len(self.execute_calls)}_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {
                        "devices": [],
                        "state_queries": {"room_light": room_light},
                    },
                    "environment": {
                        "appliances": {},
                        "state_queries": {"room_light": room_light},
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                    "expected_state": action.get("expected_state"),
                }

        tools = LegacyEffectiveRoomLightTools(light_on=True)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気を消して",
                "turn_id": "turn_light_off_legacy_effective_review",
            }
        )
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertNotIn("action.retrying", event_types)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(tools.execute_calls[0]["action_id"], "light_off")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")

    def test_legacy_effective_room_light_fields_do_not_drive_state_query_reply(self) -> None:
        class LegacyEffectiveRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                room_light = {
                    "available": True,
                    "stale": False,
                    "state": "unknown",
                    "confidence_label": "low",
                    "effective_state": "on",
                    "effective_confidence_label": "high",
                    "effective_authority": "environment_state_server.calibration.home_assistant",
                    "effective_answer_hint": "Home Assistant の直近状態で補正しています。",
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": "映像推定では断定できない。",
                }
                return {
                    "status": "ok",
                    "observation_ref": "obs_ha_calibrated_state_query",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {"room_light": room_light}},
                    "environment": {
                        "appliances": {},
                        "state_queries": {"room_light": room_light},
                    },
                }

        events = ThoughtLoop(tools=LegacyEffectiveRoomLightTools()).run_dicts(
            {
                **TURN,
                "text": "電気はついてる？",
                "turn_id": "turn_legacy_effective_room_light_reply",
            }
        )
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertTrue(any("判定がまだ弱い" in speech for speech in speeches))
        self.assertFalse(any("Home Assistant上では" in speech for speech in speeches))
        self.assertFalse(any("ついているように見えます" in speech for speech in speeches))
        self.assertFalse(any("補正込みでは" in speech for speech in speeches))

    def test_uncertain_target_state_review_does_not_retry_light_on(self) -> None:
        class UncertainRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                room_light = {
                    "available": True,
                    "stale": False,
                    "state": "unknown",
                    "confidence_label": "low",
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": "映像推定では断定できない。",
                    "observed_at": "2026-05-08T00:00:00+00:00",
                    "updated_at": "2026-05-08T00:00:00+00:00",
                }
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{len(self.execute_calls)}_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {
                        "devices": [],
                        "state_queries": {"room_light": room_light},
                    },
                    "environment": {
                        "appliances": {},
                        "state_queries": {"room_light": room_light},
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                    "expected_state": action.get("expected_state"),
                }

        tools = UncertainRoomLightTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気をつけて",
                "turn_id": "turn_light_on_uncertain_review",
            }
        )
        event_types = [event["type"] for event in events]

        self.assertNotIn("action.retrying", event_types)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(tools.execute_calls[0]["action_id"], "light_on")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")

    def test_internal_stage_progress_stays_out_of_user_speech(self) -> None:
        class UnobservableLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}_{len(self.execute_calls)}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                }

        events = ThoughtLoop(tools=UnobservableLightTools()).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_stream_progress_continuation",
            }
        )
        speech_stream = "".join(
            event["data"]["delta"]
            for event in events
            if event["type"] == "assistant.speech_delta"
        )
        stage_events = [
            event
            for event in events
            if event["type"] == "thought.stage"
        ]

        self.assertEqual(speech_stream.count("操作は送信しました"), 1)
        self.assertEqual(
            speech_stream.count("まだ環境で結果を確認しきれていない"),
            1,
        )
        self.assertTrue(stage_events)
        self.assertTrue(all(not event["data"].get("audible") for event in stage_events))
        self.assertFalse(
            any(
                event["type"] == "assistant.speech_delta" and event["data"].get("stage")
                for event in events
            )
        )
        self.assertNotIn("関連しそうな記憶を短く確認します", speech_stream)
        self.assertNotIn("いまの環境を短く見ています", speech_stream)
        self.assertNotIn("望む状態を組み立てます", speech_stream)
        self.assertIn("あと1回くらい見直します。", speech_stream)
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")

    def test_stream_progress_reuses_issue_context_across_repeated_attempts(self) -> None:
        class UnobservableLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}_{len(self.execute_calls)}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                }

        loop = ThoughtLoop(tools=UnobservableLightTools())
        first_events = loop.run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_stream_issue_context_1",
            }
        )
        second_events = loop.run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_stream_issue_context_2",
            }
        )

        first_speech = "".join(
            event["data"]["delta"]
            for event in first_events
            if event["type"] == "assistant.speech_delta"
        )
        second_speech = "".join(
            event["data"]["delta"]
            for event in second_events
            if event["type"] == "assistant.speech_delta"
        )
        issue_keys = [
            event["data"].get("speech_context", {}).get("issue_key")
            for event in second_events
            if event["type"] == "assistant.speech_delta"
        ]

        self.assertIn("操作は送信しました", first_speech)
        self.assertNotIn("操作は送信しました", second_speech)
        self.assertNotIn("状態を確認してもらえますか？何回か", second_speech)
        self.assertTrue(
            "続けて確認します。" in second_speech
            or "まだ確証が取れていません。" in second_speech
        )
        self.assertTrue(any(issue_keys))
        self.assertEqual(second_events[-1]["data"]["status"], "needs_feedback")

    def test_action_review_exposes_environment_recheck_markers(self) -> None:
        class UnobservableLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}_{len(self.execute_calls)}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                }

        tools = UnobservableLightTools()
        loop = ThoughtLoop(tools=tools)
        first_events = loop.run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_recheck_marker_pending",
            }
        )

        pending_event = next(
            event for event in first_events if event["type"] == "action.review_pending"
        )
        pending_marker = pending_event["data"]["environment_recheck"]
        self.assertEqual(pending_marker["status"], "pending")
        self.assertEqual(pending_marker["action_id"], "light_on")
        self.assertEqual(pending_marker["observations_done"], 1)
        self.assertEqual(pending_marker["observation_attempts"], 2)
        self.assertEqual(pending_marker["remaining_observations"], 1)
        reviewed_event = next(
            event
            for event in reversed(first_events)
            if event["type"] == "action.reviewed"
        )
        feedback_event = next(
            event for event in first_events if event["type"] == "feedback.requested"
        )
        failed_marker = first_events[-1]["data"]["environment_recheck"]

        self.assertEqual(reviewed_event["data"]["environment_recheck"]["status"], "failed")
        self.assertEqual(feedback_event["data"]["environment_recheck"]["status"], "failed")
        self.assertEqual(failed_marker["status"], "failed")
        self.assertEqual(failed_marker["observations_done"], 2)
        self.assertEqual(failed_marker["observation_attempts"], 2)
        self.assertEqual(failed_marker["remaining_observations"], 0)
        self.assertEqual(len(tools.execute_calls), 1)

    def test_external_required_action_does_not_schedule_auto_review(self) -> None:
        class ExternalRequiredLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_preview(self, turn, observation):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "action": {
                        "action_id": "light_on",
                        "target": "light",
                        "target_name": "リビングの電気",
                        "expected_state": "on",
                        "pre_action_phrase": "リビングの電気をつける",
                        "confirm_required": False,
                        "control_type": "stateless_toggle",
                        "state_authority": "open_loop",
                        "verification_mode": "external_observation",
                        "state_tracking": "external_required",
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": "cmd_external_required_light",
                    "attempt": len(self.execute_calls),
                    "issued_at": "2026-05-08T00:00:00+00:00",
                    "control_type": "stateless_toggle",
                    "state_authority": "open_loop",
                    "verification_mode": "external_observation",
                    "state_tracking": "external_required",
                    "message": "リビングの電気をつける操作を送信しました。",
                    "speak": "リビングの電気をつける操作を送信しました。",
                }

        tools = ExternalRequiredLightTools()
        loop = ThoughtLoop(tools=tools)
        events = loop.run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_external_required_light",
            }
        )
        event_types = [event["type"] for event in events]
        assistant_text = "\n".join(
            str(event["data"].get("speech") or event["data"].get("display") or "")
            for event in events
            if event["type"] in {"assistant.message", "feedback.requested"}
        )

        self.assertNotIn("action.review_pending", event_types)
        self.assertNotIn("action.reviewed", event_types)
        self.assertNotIn("feedback.requested", event_types)
        self.assertNotIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertEqual(tools.execute_calls[0]["state_tracking"], "external_required")
        self.assertIn("操作を送信しました", assistant_text)
        self.assertIn("物理状態を確認できない", assistant_text)
        self.assertNotIn("少し待ってから環境を見直します", assistant_text)
        self.assertEqual(events[-1]["data"]["status"], "submitted_external_observation_required")
        self.assertFalse(events[-1]["data"]["automatic_review_scheduled"])
        self.assertFalse(events[-1]["data"]["physical_state_confirmed"])

    def test_new_home_command_supersedes_pending_review_before_observing(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools)
        previous_action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "pre_action_phrase": "リビングの電気をつける",
        }
        loop.pending_action_reviews[TURN["session_id"]] = {
            "action": previous_action,
            "execute_result": {"status": "accepted", "executed": True},
            "last_review": {"status": "pending"},
            "observations_done": 1,
            "execute_attempts": 1,
            "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
        }
        old_issue_key = loop._action_speech_issue_key(  # noqa: SLF001
            "living_room_main",
            previous_action,
        )
        loop.recent_speech_by_issue[old_issue_key] = [
            "電気がついたか確認できませんでした。",
        ]

        events = loop.run_dicts(
            {
                **TURN,
                "text": "リビングの電気を消してください",
                "turn_id": "turn_supersede_pending_review",
            }
        )
        event_types = [event["type"] for event in events]
        action_event = next(event for event in events if event["type"] == "action.proposed")
        supersede_items = [
            item
            for item in tools.short_memory_write_calls
            if item["retry_budget"]["status"] == "review_superseded_by_new_command"
        ]

        self.assertIn("action.review_superseded", event_types)
        self.assertEqual(action_event["data"]["action"]["action_id"], "light_off")
        self.assertEqual(tools.execute_calls[-1]["action_id"], "light_off")
        self.assertNotIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertNotIn(old_issue_key, loop.recent_speech_by_issue)
        self.assertEqual(len(supersede_items), 1)
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_ac_stop_phrases_supersede_stale_inner_door_review(self) -> None:
        for text in ("エアコンを止めて", "エアコンを消して"):
            with self.subTest(text=text):
                tools = MockThoughtTools()
                loop = ThoughtLoop(tools=tools)
                loop.pending_action_reviews[TURN["session_id"]] = {
                    "action": {
                        "action_id": "door_stop",
                        "target": "door",
                        "target_name": "中扉",
                        "expected_state": "stopped",
                        "pre_action_phrase": "中扉を止める",
                    },
                    "execute_result": {"status": "accepted", "executed": True},
                    "last_review": {"status": "pending"},
                    "observations_done": 1,
                    "execute_attempts": 1,
                    "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 0},
                }

                events = loop.run_dicts(
                    {
                        **TURN,
                        "text": text,
                        "turn_id": f"turn_ac_stop_not_door_{len(text)}",
                    }
                )
                event_types = [event["type"] for event in events]
                action_event = next(
                    event for event in events if event["type"] == "action.proposed"
                )
                assistant_text = "\n".join(
                    str(event["data"].get("speech") or event["data"].get("display") or "")
                    for event in events
                    if event["type"] == "assistant.message"
                )

                self.assertIn("action.review_superseded", event_types)
                self.assertEqual(action_event["data"]["action"]["action_id"], "aircon_off")
                self.assertEqual(action_event["data"]["action"]["target"], "aircon")
                self.assertEqual(action_event["data"]["action"]["target_name"], "エアコン")
                self.assertEqual(len(tools.execute_calls), 1)
                self.assertEqual(tools.execute_calls[0]["action_id"], "aircon_off")
                self.assertEqual(tools.execute_calls[0]["target"], "aircon")
                self.assertNotIn("中扉", assistant_text)
                self.assertNotIn("door_stop", json.dumps(tools.execute_calls, ensure_ascii=False))
                self.assertNotIn(TURN["session_id"], loop.pending_action_reviews)

    def test_general_expression_does_not_continue_pending_action_review(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools, responder=StaticResponder())
        previous_action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "pre_action_phrase": "リビングの電気をつける",
        }
        loop.pending_action_reviews[TURN["session_id"]] = {
            "action": previous_action,
            "execute_result": {"status": "accepted", "executed": True},
            "last_review": {"status": "pending"},
            "observations_done": 1,
            "execute_attempts": 1,
            "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "笑ってみてください",
                "turn_id": "turn_expression_with_pending_review",
            }
        )
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        acknowledged = next(
            event for event in events if event["type"] == "input.acknowledged"
        )
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertEqual(understood["data"]["kind"], "general")
        self.assertIn("responder.started", event_types)
        self.assertNotIn("action.reviewed", event_types)
        self.assertNotIn("action.retrying", event_types)
        self.assertNotIn("feedback.requested", event_types)
        self.assertEqual(tool_names, ["memory.retrieve"])
        self.assertEqual(acknowledged["data"]["speech"], "うん、聞いたよ。")
        self.assertIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_dance_request_does_not_continue_pending_action_review(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools, responder=StaticResponder())
        previous_action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "pre_action_phrase": "リビングの電気をつける",
        }
        loop.pending_action_reviews[TURN["session_id"]] = {
            "action": previous_action,
            "execute_result": {"status": "accepted", "executed": True},
            "last_review": {"status": "pending"},
            "observations_done": 1,
            "execute_attempts": 1,
            "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "踊ってください",
                "turn_id": "turn_dance_with_pending_review",
            }
        )
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertEqual(understood["data"]["kind"], "motion_request")
        self.assertIn("responder.started", event_types)
        self.assertIn("motion.requested", event_types)
        self.assertNotIn("action.proposed", event_types)
        self.assertNotIn("action.reviewed", event_types)
        self.assertNotIn("feedback.requested", event_types)
        self.assertEqual(tool_names, ["memory.retrieve"])
        self.assertEqual(tools.execute_calls, [])
        self.assertIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_audio_check_does_not_continue_pending_action_review(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools, responder=StaticResponder())
        previous_action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "pre_action_phrase": "リビングの電気をつける",
        }
        loop.pending_action_reviews[TURN["session_id"]] = {
            "action": previous_action,
            "execute_result": {"status": "accepted", "executed": True},
            "last_review": {"status": "pending"},
            "observations_done": 1,
            "execute_attempts": 1,
            "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "音声が聞こえたか確認してください",
                "turn_id": "turn_audio_check_with_pending_review",
            }
        )
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")
        route = next(
            event
            for event in events
            if event["type"] == "thought_core.response_route_classified"
        )
        final_message = [
            event for event in events if event["type"] == "assistant.message"
        ][-1]
        serialized_route = json.dumps(route["data"], ensure_ascii=False)

        self.assertEqual(understood["data"]["kind"], "audio_check")
        self.assertIn("audio.status_checked", event_types)
        self.assertNotIn("action.reviewed", event_types)
        self.assertNotIn("feedback.requested", event_types)
        self.assertNotIn("responder.started", event_types)
        self.assertEqual(route["data"]["schema_version"], "thought_core_response_route.v0")
        self.assertEqual(route["data"]["response_route"], "audio_status_check")
        self.assertEqual(route["data"]["intent_kind"], "audio_check")
        self.assertEqual(route["data"]["responder_status"], "audio_status_check")
        self.assertFalse(route["data"]["fallback_used"])
        self.assertNotIn("raw_prompt", serialized_route)
        self.assertNotIn("raw_transcript", serialized_route)
        self.assertNotIn("provider_payload", serialized_route)
        self.assertEqual(tool_names, ["memory.retrieve"])
        self.assertIn("テキストとして受け取れています", final_message["data"]["speech"])
        self.assertIn("確認できません", final_message["data"]["speech"])
        self.assertNotIn("電気", final_message["data"]["speech"])
        self.assertIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertEqual(events[-1]["data"]["status"], "audio_status_check")

    def test_ambiguous_brightness_wording_does_not_execute_home_action(self) -> None:
        tools = MockThoughtTools(light_on=False)
        loop = ThoughtLoop(tools=tools, responder=StaticResponder())

        events = loop.run_dicts(
            {
                **TURN,
                "text": "部屋をちょっと明るくできるかな？",
                "turn_id": "turn_ambiguous_brightness_request",
            }
        )
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertNotEqual(understood["data"]["kind"], "home_command")
        self.assertNotIn("command.planned", event_types)
        self.assertNotIn("action.proposed", event_types)
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tools.execute_calls, [])

    def test_expression_request_without_pending_review_does_not_execute_home_action(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools, responder=StaticResponder())

        events = loop.run_dicts(
            {
                **TURN,
                "text": "笑って見せて",
                "turn_id": "turn_smile_request_without_pending_review",
            }
        )
        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")

        self.assertNotEqual(understood["data"]["kind"], "home_command")
        self.assertNotIn("command.planned", event_types)
        self.assertNotIn("action.proposed", event_types)
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tools.execute_calls, [])

    def test_explicit_review_turn_continues_pending_action_review(self) -> None:
        tools = MockThoughtTools(light_on=True)
        loop = ThoughtLoop(tools=tools)
        previous_action = {
            "action_id": "light_on",
            "target": "light",
            "target_name": "リビングの電気",
            "expected_state": "on",
            "pre_action_phrase": "リビングの電気をつける",
        }
        loop.pending_action_reviews[TURN["session_id"]] = {
            "action": previous_action,
            "execute_result": {"status": "accepted", "executed": True},
            "last_review": {"status": "pending"},
            "observations_done": 1,
            "execute_attempts": 1,
            "policy": {"settle_ms": 1500, "observation_attempts": 2, "auto_retries": 1},
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "確認して",
                "turn_id": "turn_explicit_pending_review",
            }
        )
        event_types = [event["type"] for event in events]

        self.assertIn("action.reviewed", event_types)
        self.assertNotIn("responder.started", event_types)
        self.assertNotIn(TURN["session_id"], loop.pending_action_reviews)
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_post_action_user_feedback_mismatch_holds_same_issue(self) -> None:
        class PostActionFeedbackTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                execute_count = len(self.execute_calls)
                device_state = "on" if execute_count else "off"
                room_light_state = "on" if execute_count >= 2 else "off"
                confidence = "high"
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{execute_count}_{reason}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {
                        "devices": [
                            {
                                "id": "living_room_light",
                                "kind": "light",
                                "name": "リビングの電気",
                                "state": device_state,
                            }
                        ],
                        "state_queries": {
                            "room_light": {
                                "available": True,
                                "state": room_light_state,
                                "confidence_label": confidence,
                                "authority": "vision.mock",
                            }
                        },
                    },
                    "environment": {
                        "appliances": {"light": {"state": device_state}},
                        "state_queries": {
                            "room_light": {
                                "available": True,
                                "state": room_light_state,
                                "confidence_label": confidence,
                                "authority": "vision.mock",
                            }
                        },
                        "wait_result": {
                            "wait_for": "room_light",
                            "matched": True,
                            "timeout_ms": 1500,
                        },
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                    "issued_at": f"issued_{len(self.execute_calls)}",
                }

        tools = PostActionFeedbackTools()
        loop = ThoughtLoop(tools=tools)
        first_events = loop.run_dicts(
            {
                **TURN,
                "text": "電気をつけて",
                "turn_id": "turn_post_action_feedback_1",
            }
        )
        second_events = loop.run_dicts(
            {
                **TURN,
                "text": "今電気は消えています",
                "turn_id": "turn_post_action_feedback_2",
            }
        )

        self.assertEqual(first_events[-1]["data"]["status"], "success")
        self.assertTrue(first_events[-1]["data"]["post_action_feedback_pending"])
        self.assertIn("state_query.feedback_pending", [event["type"] for event in first_events])
        self.assertIn("state_query.feedback_saved", [event["type"] for event in second_events])
        self.assertIn("action.feedback_resolved", [event["type"] for event in second_events])
        self.assertNotIn("action.retrying", [event["type"] for event in second_events])
        self.assertEqual(second_events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 1)

    def test_general_turn_uses_responder_boundary(self) -> None:
        events = ThoughtLoop(responder=StaticResponder()).run_dicts(GENERAL_TURN)
        event_types = [event["type"] for event in events]

        self.assertEqual(
            event_types,
            [
                "input.acknowledged",
                "assistant.speech_delta",
                "assistant.message",
                "input.understood",
                "thought.stage",
                "tool.started",
                "tool.result",
                "memory.retrieved",
                "responder.started",
                "responder.completed",
                "thought_core.response_route_classified",
                "assistant.speech_delta",
                "assistant.message",
                "turn.completed",
            ],
        )
        started = next(event for event in events if event["type"] == "responder.started")
        completed = next(event for event in events if event["type"] == "responder.completed")
        route = next(
            event
            for event in events
            if event["type"] == "thought_core.response_route_classified"
        )
        final_message = [
            event for event in events if event["type"] == "assistant.message"
        ][-1]
        self.assertEqual(started["data"]["boundary"], "thought-core.turn_responder.v0")
        self.assertEqual(completed["data"]["adapter_kind"], "test_responder")
        self.assertTrue(completed["data"]["used_llm"])
        self.assertEqual(
            completed["data"]["response_context"]["current_stage"],
            "general_responder",
        )
        self.assertEqual(route["data"]["schema_version"], "thought_core_response_route.v0")
        self.assertEqual(route["data"]["response_route"], "ordinary_conversation")
        self.assertEqual(route["data"]["intent_kind"], "general")
        self.assertEqual(route["data"]["responder_status"], "llm_response")
        self.assertFalse(route["data"]["fallback_used"])
        self.assertNotIn("raw_prompt", route["data"])
        self.assertNotIn("raw_transcript", route["data"])
        self.assertNotIn("provider_payload", route["data"])
        self.assertEqual(final_message["data"]["speech"], "聞こえています。応答境界も動いています。")
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

    def test_general_turn_without_llm_marks_fallback_route_not_audio_check(self) -> None:
        events = ThoughtLoop().run_dicts(
            {
                **GENERAL_TURN,
                "text": "今日は作業の合間に軽く雑談したい",
                "turn_id": "turn_general_fallback_route",
            }
        )
        event_types = [event["type"] for event in events]
        route = next(
            event
            for event in events
            if event["type"] == "thought_core.response_route_classified"
        )
        completed = next(
            event for event in events if event["type"] == "responder.completed"
        )
        final_message = [
            event for event in events if event["type"] == "assistant.message"
        ][-1]
        serialized_route = json.dumps(route["data"], ensure_ascii=False)
        visible_speech = str(final_message["data"].get("speech") or "")
        visible_display = str(final_message["data"].get("display") or "")
        visible_text = f"{visible_speech}\n{visible_display}"

        self.assertNotIn("audio.status_checked", event_types)
        self.assertIn("responder.started", event_types)
        self.assertEqual(route["data"]["schema_version"], "thought_core_response_route.v0")
        self.assertEqual(route["data"]["response_route"], "ordinary_conversation")
        self.assertEqual(
            route["data"]["responder_status"],
            "local_fallback_no_llm_adapter",
        )
        self.assertTrue(route["data"]["fallback_used"])
        self.assertFalse(route["data"]["used_llm"])
        self.assertEqual(completed["data"]["adapter_kind"], "local_fallback")
        self.assertEqual(completed["data"]["provider"], "thought-core")
        self.assertEqual(completed["data"]["model"], "local-rule-v0")
        self.assertIn("microphone_quality_proven", route["data"]["non_claims"])
        self.assertIn("speaker_output_heard", route["data"]["non_claims"])
        self.assertNotIn("raw_prompt", serialized_route)
        self.assertNotIn("raw_transcript", serialized_route)
        self.assertNotIn("provider_payload", serialized_route)
        self.assertNotIn("raw provider", serialized_route)
        self.assertIn("入力は受け取りました", visible_text)
        self.assertIn("通常会話用LLMが未接続", visible_text)
        self.assertIn("簡易応答", visible_text)
        for user_visible_internal_term in (
            "応答アダプター",
            "アダプターの設定後",
            "アダプター",
            "adapter",
            "設定後",
        ):
            self.assertNotIn(user_visible_internal_term, visible_text)
        self.assertNotIn("音声入力", visible_text)
        self.assertNotIn("マイク", visible_text)
        self.assertNotIn("スピーカー", visible_text)

    def test_general_local_fallback_question_matrix_stays_user_facing(self) -> None:
        cases = [
            ("greeting", "こんにちは、元気？"),
            ("capability_question", "今なにができる？"),
            ("planning_question", "今日の作業をどう進めればいい？"),
            ("follow_up_question", "さっきの話の続きで相談したい"),
            ("creative_question", "短い冗談を言って"),
            ("ambiguous_help", "ちょっと困ってるんだけど"),
            ("test_question", "これはテストです"),
        ]
        forbidden_visible_terms = (
            "応答アダプター",
            "アダプターの設定後",
            "アダプター",
            "adapter",
            "local_fallback",
            "provider_payload",
            "raw_prompt",
        )

        for case_name, text in cases:
            with self.subTest(case=case_name):
                events = ThoughtLoop().run_dicts(
                    {
                        **GENERAL_TURN,
                        "text": text,
                        "turn_id": f"turn_general_fallback_matrix_{case_name}",
                    }
                )
                route = next(
                    event
                    for event in events
                    if event["type"] == "thought_core.response_route_classified"
                )
                completed = next(
                    event for event in events if event["type"] == "responder.completed"
                )
                final_message = [
                    event for event in events if event["type"] == "assistant.message"
                ][-1]
                visible_speech = str(final_message["data"].get("speech") or "")
                visible_display = str(final_message["data"].get("display") or "")
                visible_text = f"{visible_speech}\n{visible_display}"

                self.assertTrue(visible_speech.strip())
                self.assertTrue(visible_display.strip())
                self.assertEqual(route["data"]["response_route"], "ordinary_conversation")
                self.assertTrue(route["data"]["fallback_used"])
                self.assertFalse(route["data"]["used_llm"])
                self.assertEqual(completed["data"]["adapter_kind"], "local_fallback")
                self.assertEqual(completed["data"]["provider"], "thought-core")
                self.assertEqual(completed["data"]["model"], "local-rule-v0")
                self.assertIn("入力", visible_text)
                if "テスト" in text:
                    self.assertIn("出力や機器状態の確認は別扱い", visible_text)
                else:
                    self.assertIn("通常会話用LLMが未接続", visible_text)
                    self.assertIn("簡易応答", visible_text)
                for forbidden_visible_term in forbidden_visible_terms:
                    self.assertNotIn(forbidden_visible_term, visible_text)

    def test_home_action_visible_phrases_can_require_llm_boundary(self) -> None:
        responder = VisiblePhraseResponder()
        events = ThoughtLoop(
            tools=MockThoughtTools(light_on=False),
            responder=responder,
            llm_visible_speech=True,
            require_llm_visible_speech=True,
        ).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_llm_visible_phrase_light_on",
            }
        )
        messages = [event for event in events if event["type"] == "assistant.message"]
        generated_messages = [
            event
            for event in messages
            if event["data"].get("phrase_generation", {}).get("enabled")
        ]

        self.assertGreaterEqual(len(generated_messages), 2)
        self.assertTrue(
            all(
                event["data"]["phrase_generation"]["used_llm"]
                for event in generated_messages
            )
        )
        self.assertTrue(
            all(
                event["data"]["phrase_generation"]["adapter_kind"]
                == "visible_phrase_test_responder"
                for event in generated_messages
            )
        )
        self.assertNotIn(
            "了解、リビングの電気をつけるね",
            "\n".join(event["data"]["speech"] for event in generated_messages),
        )
        self.assertIn(
            "semantic_draft",
            responder.response_contexts[-1],
        )
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_required_llm_visible_phrase_fails_closed_without_llm(self) -> None:
        events = ThoughtLoop(
            tools=MockThoughtTools(light_on=False),
            responder=StaticResponder(used_llm=False),
            llm_visible_speech=True,
            require_llm_visible_speech=True,
        ).run_dicts(
            {
                **TURN,
                "text": "リビングの電気をつけて",
                "turn_id": "turn_llm_visible_phrase_required_failure",
            }
        )
        event_types = [event["type"] for event in events]
        visible_speech = "\n".join(
            str(event["data"].get("speech") or "")
            for event in events
            if event["type"] == "assistant.message"
        )

        self.assertIn("phrase.generation_failed", event_types)
        self.assertNotIn("了解、リビングの電気をつけるね", visible_speech)

    def test_responder_receives_compact_previous_phrase_context(self) -> None:
        tools = MockThoughtTools()
        responder = RecordingContextResponder()
        loop = ThoughtLoop(tools=tools, responder=responder)

        loop.run_dicts(
            {
                **GENERAL_TURN,
                "turn_id": "turn_general_context_first",
            }
        )
        second_events = loop.run_dicts(
            {
                **GENERAL_TURN,
                "text": "さっきの続きで雑談しよう",
                "turn_id": "turn_general_context_second",
            }
        )

        event_types = [event["type"] for event in second_events]
        tool_names = [
            event["data"]["tool"]
            for event in second_events
            if event["type"] == "tool.started"
        ]
        context = responder.response_contexts[-1]
        completed = next(
            event for event in second_events if event["type"] == "responder.completed"
        )

        self.assertEqual(context["current_stage"], "general_responder")
        self.assertEqual(context["previous_fragment"], "うん、聞いたよ。")
        self.assertIn("issue_key", context)
        self.assertTrue(
            any(
                "応答境界の確認 1" in str(fragment)
                for fragment in context["recent_fragments"]
            )
        )
        self.assertEqual(completed["data"]["response_context"], context)
        self.assertNotIn("action.proposed", event_types)
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tools.execute_calls, [])

    def test_response_context_prompt_carries_phrase_feedback_constraints(self) -> None:
        prompt = _response_context_prompt(
            {
                "previous_fragment": "エアコンを確認しています。",
                "recent_fragments": [
                    "エアコンを確認しています。",
                    "応答境界の確認 1 です。",
                ],
                "current_stage": "general_responder",
                "action_id": "aircon_off",
                "target": "aircon",
                "expected_state": "off",
            }
        )

        self.assertIn("wording continuity", prompt)
        self.assertIn("avoid repetitive phrasing", prompt)
        self.assertIn("permission to execute actions", prompt)
        self.assertIn("エアコンを確認しています。", prompt)
        self.assertIn("応答境界の確認 1 です。", prompt)
        self.assertIn("aircon_off", prompt)
        self.assertNotIn("expected_state", prompt)

    def test_phrase_feedback_expected_response_example_connects_aircon_recheck(self) -> None:
        example = {
            "previous_public_phrase": "エアコンを確認しています。",
            "candidate_phrase": "エアコンの状態はまだ断定できません。",
            "required_caveat": "物理状態はまだ確認できていません。",
            "expected_public_phrase": (
                "その結果、状態まではまだ断定できません。"
                "物理状態はまだ確認できていません。"
            ),
        }

        expected = example["expected_public_phrase"]

        self.assertTrue(expected.startswith("その結果、"))
        self.assertIn("状態まではまだ断定できません", expected)
        self.assertIn(example["required_caveat"], expected)
        self.assertLess(expected.count("エアコン"), 2)
        self.assertNotEqual(expected, example["candidate_phrase"])

    def test_confirmed_action_review_failure_speech_is_not_surface_duplicated(self) -> None:
        class UnobservableActionTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                return {
                    "status": "ok",
                    "observation_ref": f"obs_{reason}_{len(self.execute_calls)}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [], "state_queries": {}},
                    "environment": {"appliances": {}, "state_queries": {}},
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": f"cmd_{len(self.execute_calls)}",
                    "attempt": len(self.execute_calls),
                }

        tools = UnobservableActionTools()
        loop = ThoughtLoop(tools=tools)
        loop.pending_confirmations[TURN["session_id"]] = {
            "action": {
                "action_id": "aircon_off",
                "target": "aircon",
                "target_name": "エアコン",
                "expected_state": "off",
                "pre_action_phrase": "エアコンを消す",
            },
            "confirmation_token": "",
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "OK お願いします",
                "turn_id": "turn_aircon_confirm_recheck_surface_dedupe",
            }
        )
        event_types = [event["type"] for event in events]
        projected_speech = "".join(
            str(event["data"].get("delta") or event["data"].get("speech") or "")
            for event in events
            if event["type"] in {"assistant.speech_delta", "feedback.requested"}
        )

        self.assertIn("action.review_pending", event_types)
        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertIn("反映後の状態を確認", projected_speech)
        self.assertEqual(projected_speech.count("操作を送信"), 1)
        self.assertEqual(projected_speech.count("環境側で確証"), 1)
        self.assertNotIn("状態を確認してもらえますか", projected_speech)
        self.assertNotIn("何回か環境を見直しました", projected_speech)
        self.assertEqual(len(tools.execute_calls), 1)

    def test_confirmed_aircon_stale_expected_match_is_concise_uncertain_feedback(
        self,
    ) -> None:
        class StaleMatchedAirconTools(MockThoughtTools):
            def __init__(self) -> None:
                super().__init__()
                self.observe_calls = 0

            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                self.observe_calls += 1
                state = "on" if self.observe_calls == 1 else "off"
                stale = self.observe_calls > 1
                device = {
                    "id": "aircon",
                    "kind": "aircon",
                    "name": "エアコン",
                    "state": state,
                    "stale": stale,
                    "updated_at": "2026-05-08T00:00:00+00:00",
                }
                return {
                    "status": "ok",
                    "observation_ref": f"obs_aircon_{self.observe_calls}",
                    "observation_source": "environment-state-server.mock",
                    "facts": {"devices": [device], "state_queries": {}},
                    "environment": {
                        "appliances": {"aircon": dict(device)},
                        "state_queries": {},
                    },
                }

            def home_execute(self, turn, action):  # type: ignore[no-untyped-def]
                self.execute_calls.append(action)
                return {
                    "status": "accepted",
                    "executed": True,
                    "retryable": False,
                    "command_id": "cmd_aircon_off",
                    "attempt": len(self.execute_calls),
                    "issued_at": "2026-05-08T00:00:00+00:00",
                }

        tools = StaleMatchedAirconTools()
        loop = ThoughtLoop(tools=tools)
        loop.pending_confirmations[TURN["session_id"]] = {
            "action": {
                "action_id": "aircon_off",
                "target": "aircon",
                "target_name": "エアコン",
                "expected_state": "off",
                "pre_action_phrase": "エアコンを消す",
            },
            "confirmation_token": "",
        }

        events = loop.run_dicts(
            {
                **TURN,
                "text": "OK お願いします",
                "turn_id": "turn_aircon_stale_expected_match",
            }
        )
        feedback_event = next(
            event for event in events if event["type"] == "feedback.requested"
        )
        assistant_speech = "".join(
            str(event["data"].get("delta") or "")
            for event in events
            if event["type"] == "assistant.speech_delta"
        )
        feedback_speech = str(feedback_event["data"]["speech"])
        reviewed_events = [event for event in events if event["type"] == "action.reviewed"]

        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(reviewed_events), 3)
        self.assertEqual(reviewed_events[-1]["data"]["reason"], "target_state_unverified")
        self.assertEqual(tools.observe_calls, 3)
        self.assertEqual(tools.execute_calls[0]["action_id"], "aircon_off")
        self.assertNotIn("反映には", assistant_speech)
        self.assertNotIn("何回か環境を見直しました", feedback_speech)
        self.assertIn("表示上は消えているように見えます", feedback_speech)
        self.assertIn("物理状態はまだ断定できません", feedback_speech)

    def test_general_turn_can_fall_back_without_llm(self) -> None:
        events = ThoughtLoop(responder=StaticResponder(used_llm=False)).run_dicts(
            GENERAL_TURN
        )

        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "local_fallback")
        self.assertFalse(events[-1]["data"]["used_llm"])

    def test_tool_started_and_result_share_tool_call_id(self) -> None:
        events = ThoughtLoop().run_dicts(TURN)
        pending: dict[str, str] = {}
        paired = 0

        for event in events:
            if event["type"] not in {"tool.started", "tool.result"}:
                continue
            data = event["data"]
            tool_call_id = data["tool_call_id"]
            if event["type"] == "tool.started":
                pending[tool_call_id] = data["tool"]
            else:
                self.assertEqual(pending.pop(tool_call_id), data["tool"])
                paired += 1

        self.assertEqual(pending, {})
        self.assertGreaterEqual(paired, 1)

    def test_tool_failure_is_not_retried_by_thought_loop(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=1)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)

        execute_results = [
            event
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        ]
        self.assertEqual(len(execute_results), 1)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertNotIn(
            "execute_retry_scheduled",
            [item["retry_budget"]["status"] for item in tools.short_memory_write_calls],
        )

    def test_tool_failure_requests_feedback_when_retry_exhausted(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=3)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertIn(
            "execute_retry_exhausted",
            [item["retry_budget"]["status"] for item in tools.short_memory_write_calls],
        )

    def test_retry_exhaustion_records_failure_pattern_candidate(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=3)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)
        event_types = [event["type"] for event in events]
        candidates = [
            item
            for item in tools.memory_write_calls
            if item.get("memory_type") == "failure_pattern"
        ]

        self.assertIn("memory.candidate_recorded", event_types)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["scope"], "failure_patterns")
        self.assertEqual(candidate["content"]["action_id"], "light_on")
        self.assertEqual(candidate["content"]["target"], "light")
        self.assertEqual(candidate["content"]["retry_status"], "execute_retry_exhausted")

    def test_mock_context_ref_failure_requests_feedback_without_retry(self) -> None:
        tools = MockThoughtTools()
        turn = {
            **TURN,
            "turn_id": "turn_retry_demo",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "1",
            },
        }

        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(turn)
        execute_results = [
            event
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        ]

        self.assertEqual(len(execute_results), 1)
        self.assertEqual(execute_results[0]["data"]["status"], "failed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")

    def test_mock_context_ref_can_demo_feedback_request(self) -> None:
        tools = MockThoughtTools()
        turn = {
            **TURN,
            "turn_id": "turn_feedback_demo",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "3",
            },
        }

        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(turn)
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(tools.execute_attempts_by_turn["turn_feedback_demo"], 1)

    def test_mock_failure_context_is_scoped_to_turn(self) -> None:
        tools = MockThoughtTools()
        failing_turn = {
            **TURN,
            "turn_id": "turn_failure_scoped",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
                "mock_execute_failures_before_success": "3",
            },
        }
        success_turn = {
            **TURN,
            "turn_id": "turn_success_scoped",
            "context_refs": {
                **TURN["context_refs"],
                "mock_initial_light_state": "off",
            },
        }

        failing_events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(
            failing_turn
        )
        success_events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(
            success_turn
        )

        self.assertEqual(failing_events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(success_events[-1]["data"]["status"], "success")
        self.assertEqual(tools.execute_attempts_by_turn["turn_failure_scoped"], 1)
        self.assertEqual(tools.execute_attempts_by_turn["turn_success_scoped"], 1)

    def test_secret_values_are_redacted_from_events(self) -> None:
        tools = MockThoughtTools(include_secret_in_execute_result=True)
        events = ThoughtLoop(tools=tools).run_dicts(TURN)
        serialized = json.dumps(events, ensure_ascii=False)

        self.assertNotIn("mock-token-that-must-not-leak", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_get_root_returns_api_index(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]

            with request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "application/json; charset=utf-8")
            self.assertEqual(payload["service"], "thought-core")
            self.assertEqual(payload["kind"], "api")
            self.assertIn("POST /turn", payload["endpoints"]["turn_json"])
            self.assertNotIn("eventsource_demo", payload["endpoints"])
            self.assertIn("sword-console", payload["console_command"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_run_dicts_streams_events_to_sink(self) -> None:
        streamed_events = []

        events = ThoughtLoop().run_dicts(TURN, event_sink=streamed_events.append)

        self.assertGreater(len(streamed_events), 0)
        self.assertEqual(
            [event["event_id"] for event in streamed_events],
            [event["event_id"] for event in events],
        )

    def test_post_turn_stream_returns_sse_events(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(TURN).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}/turn?stream=true",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                method="POST",
            )

            with request.urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "text/event-stream; charset=utf-8")
            self.assertIn("event: assistant.message", payload)
            self.assertIn("event: turn.completed", payload)
            self.assertIn('"turn_id":"turn_test_001"', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_get_turn_stream_is_method_not_allowed(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]

            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(
                    "http://127.0.0.1:"
                    f"{port}/turn/stream?text=%E9%9B%BB%E6%B0%97%E3%81%A4%E3%81%91%E3%81%A6"
                    "&turn_id=turn_get_test&session_id=living_room_main",
                    timeout=5,
                )

            self.assertEqual(caught.exception.code, 405)
            self.assertEqual(caught.exception.headers["Allow"], "POST")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_post_turn_rejects_oversized_json_body(self) -> None:
        with patch.dict("os.environ", {"THOUGHT_CORE_MAX_BODY_BYTES": "8"}, clear=False):
            server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(TURN).encode("utf-8")
            req = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req, timeout=5)

            self.assertEqual(caught.exception.code, 413)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_post_turn_rejects_malformed_and_schema_invalid_json(self) -> None:
        cases = (
            (b"{not-json", "application/json", 400),
            (json.dumps({**TURN, "text": ""}).encode("utf-8"), "application/json", 400),
            (json.dumps(["not", "object"]).encode("utf-8"), "application/json", 400),
        )

        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            for body, content_type, expected_code in cases:
                with self.subTest(body=body[:24]):
                    req = request.Request(
                        f"http://127.0.0.1:{port}/turn",
                        data=body,
                        headers={"Content-Type": content_type},
                        method="POST",
                    )

                    with self.assertRaises(error.HTTPError) as caught:
                        request.urlopen(req, timeout=5)

                    self.assertEqual(caught.exception.code, expected_code)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_post_turn_requires_token_when_configured(self) -> None:
        env = {
            "THOUGHT_CORE_REQUIRE_API_TOKEN": "1",
            "THOUGHT_CORE_API_TOKEN": "secret-token",
        }
        with patch.dict("os.environ", env, clear=False):
            server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            body = json.dumps(GENERAL_TURN).encode("utf-8")
            unauthenticated = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(unauthenticated, timeout=5)
            self.assertEqual(caught.exception.code, 401)

            authenticated = request.Request(
                f"http://127.0.0.1:{port}/turn",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer secret-token",
                },
                method="POST",
            )
            with request.urlopen(authenticated, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertIn("events", payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
