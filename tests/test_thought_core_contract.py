import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import request
from urllib.parse import parse_qs, urlsplit

from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.reasoning import LocalActionReasoner  # noqa: E402
from thought_core.responders import ResponderResult  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.server import create_server  # noqa: E402
from thought_core.tools import HomeControlHttpTools, HomeControlToolConfig, MockThoughtTools  # noqa: E402


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
    "text": "マイクテストです。聞こえていますか？",
    "turn_id": "turn_general_001",
}


class StaticResponder:
    adapter_kind = "test_responder"
    provider = "test"
    model = "test-model"

    def __init__(self, *, used_llm: bool = True) -> None:
        self.used_llm = used_llm

    def respond(self, turn):  # type: ignore[no-untyped-def]
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
        self.assertIn("リビングの電気を消したよ。", speeches)
        self.assertEqual(events[-1]["data"]["status"], "success")

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
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertFalse(saved["data"]["ok"])
        self.assertEqual(saved["data"]["error"], "environment_feedback_unconfigured")
        self.assertTrue(any("学習ログへの保存に失敗" in speech for speech in speeches))

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

    def test_vacuum_return_uses_dify_action_id_dictionary(self) -> None:
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
        post_calls = [call for call in calls if call["method"] == "POST"]

        self.assertEqual(first_events[-1]["data"]["status"], "confirmation_required")
        self.assertIn("まだ実行していません", first_messages[-1])
        self.assertEqual([call["path"] for call in post_calls], [
            "/actions/door_close/preview",
            "/actions/door_close/execute",
        ])
        execute_body = post_calls[1]["body"]
        self.assertEqual(execute_body["confirmed"], True)
        self.assertEqual(execute_body["confirmation_token"], "confirm-token-1")
        self.assertEqual(second_events[-1]["data"]["status"], "success")
        self.assertIn("中扉を閉めました。", second_messages[-1])
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
        self.assertIn("review_observation_pending", short_memory_statuses)
        self.assertIn("review_retry_exhausted", short_memory_statuses)
        self.assertEqual(tools.short_memory_write_calls[0]["type"], "short_memory")
        self.assertEqual(
            tools.short_memory_write_calls[0]["retry_budget"]["observation_attempts"],
            3,
        )

    def test_low_risk_action_can_retry_after_review_exhausted(self) -> None:
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

        self.assertIn("action.retrying", [event["type"] for event in events])
        self.assertEqual(events[-1]["data"]["status"], "success")
        self.assertEqual(len(tools.execute_calls), 2)
        retry_budget_items = [
            item
            for item in tools.short_memory_write_calls
            if item["retry_budget"]["status"] == "review_retry_scheduled"
        ]
        self.assertEqual(len(retry_budget_items), 1)
        self.assertEqual(retry_budget_items[0]["retry_budget"]["auto_retries"], 1)

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

    def test_calibrated_room_light_review_succeeds_when_effective_state_matches(self) -> None:
        class CalibratedRoomLightTools(MockThoughtTools):
            def environment_observe(self, turn, *, reason):  # type: ignore[no-untyped-def]
                executed = bool(self.execute_calls)
                raw_state = "unknown" if executed else "on"
                effective_state = "off" if executed else "on"
                confidence = "high" if executed else "medium"
                room_light = {
                    "available": True,
                    "stale": False,
                    "state": raw_state,
                    "confidence_label": "low" if executed else "medium",
                    "effective_state": effective_state,
                    "effective_confidence_label": confidence,
                    "effective_authority": "environment_state_server.calibration.home_assistant",
                    "effective_answer_hint": "学習済みの操作履歴と Home Assistant の直近状態で補正しています。",
                    "authority": "vision_snapshot_processor.mock",
                    "projected_by": "environment_state_server.mock",
                    "answer_hint": "映像推定では断定できない。",
                    "calibration": {
                        "applied": True,
                        "state": effective_state,
                        "confidence_label": confidence,
                        "reason": "fresh_home_assistant_light_state",
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

        tools = CalibratedRoomLightTools(light_on=True)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "電気を消して",
                "turn_id": "turn_light_off_calibrated_review",
            }
        )
        event_types = [event["type"] for event in events]

        self.assertNotIn("feedback.requested", event_types)
        self.assertNotIn("action.retrying", event_types)
        self.assertEqual(len(tools.execute_calls), 1)
        self.assertEqual(tools.execute_calls[0]["action_id"], "light_off")
        self.assertEqual(events[-1]["data"]["status"], "success")

    def test_home_assistant_calibrated_room_light_reply_names_authority(self) -> None:
        class HomeAssistantCalibratedTools(MockThoughtTools):
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

        events = ThoughtLoop(tools=HomeAssistantCalibratedTools()).run_dicts(
            {
                **TURN,
                "text": "電気はついてる？",
                "turn_id": "turn_home_assistant_calibrated_room_light_reply",
            }
        )
        speeches = [
            event["data"]["speech"]
            for event in events
            if event["type"] == "assistant.message"
        ]

        self.assertTrue(any("Home Assistant上では" in speech for speech in speeches))
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

    def test_post_action_user_feedback_mismatch_retries_same_issue(self) -> None:
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
        self.assertIn("action.retrying", [event["type"] for event in second_events])
        self.assertEqual(second_events[-1]["data"]["status"], "success")
        self.assertEqual(len(tools.execute_calls), 2)

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
                "assistant.speech_delta",
                "assistant.message",
                "turn.completed",
            ],
        )
        started = next(event for event in events if event["type"] == "responder.started")
        completed = next(event for event in events if event["type"] == "responder.completed")
        final_message = [
            event for event in events if event["type"] == "assistant.message"
        ][-1]
        self.assertEqual(started["data"]["boundary"], "thought-core.turn_responder.v0")
        self.assertEqual(completed["data"]["adapter_kind"], "test_responder")
        self.assertTrue(completed["data"]["used_llm"])
        self.assertEqual(final_message["data"]["speech"], "聞こえています。応答境界も動いています。")
        self.assertEqual(events[-1]["data"]["status"], "llm_response")

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

    def test_tool_failure_can_be_retried_by_thought_loop(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=1)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)

        execute_results = [
            event
            for event in events
            if event["type"] == "tool.result" and event["data"]["tool"] == "home.execute"
        ]
        self.assertEqual(len(execute_results), 2)
        self.assertEqual(len(tools.execute_calls), 2)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "success")
        self.assertIn(
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
        self.assertEqual(len(tools.execute_calls), 2)
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

    def test_mock_context_ref_can_demo_retry_success(self) -> None:
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

        self.assertEqual(len(execute_results), 2)
        self.assertEqual(execute_results[0]["data"]["status"], "failed")
        self.assertEqual(execute_results[1]["data"]["status"], "accepted")
        self.assertEqual(events[-1]["data"]["status"], "success")

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
        self.assertEqual(tools.execute_attempts_by_turn["turn_feedback_demo"], 2)

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
        self.assertEqual(tools.execute_attempts_by_turn["turn_failure_scoped"], 2)
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

    def test_get_turn_stream_returns_sse_events(self) -> None:
        server = create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]

            with request.urlopen(
                "http://127.0.0.1:"
                f"{port}/turn/stream?text=%E9%9B%BB%E6%B0%97%E3%81%A4%E3%81%91%E3%81%A6"
                "&turn_id=turn_get_test&session_id=living_room_main",
                timeout=5,
            ) as response:
                payload = response.read().decode("utf-8")
                content_type = response.headers["Content-Type"]

            self.assertEqual(content_type, "text/event-stream; charset=utf-8")
            self.assertIn("event: assistant.message", payload)
            self.assertIn("event: turn.completed", payload)
            self.assertIn('"turn_id":"turn_get_test"', payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
