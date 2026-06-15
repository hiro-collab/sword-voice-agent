import json
import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core" / "src"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.schema import TurnInput  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


TURN = {
    "text": "今の状況を教えて",
    "turn_id": "turn_environment_grounding_001",
    "session_id": "living_room_main",
    "locale": "ja-JP",
    "context_refs": {
        "environment_snapshot": "env_abc123",
        "voice_turn": "voice_789",
    },
}


class CatalogStatusTools(MockThoughtTools):
    def environment_observe(self, turn: TurnInput, *, reason: str) -> dict[str, object]:
        observation = super().environment_observe(turn, reason=reason)
        environment = observation.setdefault("environment", {})
        assert isinstance(environment, dict)
        environment["actions"] = [
            {
                "action_id": "light_on",
                "appliance_id": "light",
                "target_label": "synthetic light",
                "expected_state": "on",
            },
            {
                "action_id": "fan_off",
                "appliance_id": "fan",
                "target_label": "synthetic fan",
                "expected_state": "off",
            },
        ]
        return observation


class EnvironmentStateGroundingTest(TestCase):
    def test_current_status_question_uses_environment_grounding_not_fallback(self) -> None:
        tools = MockThoughtTools(light_on=True)
        events = ThoughtLoop(tools=tools).run_dicts(TURN)

        event_types = [event["type"] for event in events]
        tool_names = [
            event["data"]["tool"]
            for event in events
            if event["type"] == "tool.started"
        ]
        understood = next(event for event in events if event["type"] == "input.understood")
        message = self._last_message(events)
        grounding = self._grounding(events)

        self.assertEqual(understood["data"]["kind"], "environment_status_query")
        self.assertEqual(grounding["query_class"], "current_environment_status")
        self.assertIn("environment.grounding_summary", event_types)
        self.assertNotIn("responder.started", event_types)
        self.assertNotIn("home.preview", tool_names)
        self.assertNotIn("home.execute", tool_names)
        self.assertEqual(tool_names, ["memory.retrieve", "environment.observe"])
        self.assertIn("Environment State", message["speech"])
        self.assertIn("物理状態", message["speech"])
        self.assertNotIn("応答アダプター", message["speech"])
        self.assertNotIn("通常会話用LLM", message["speech"])
        self.assertEqual(events[-1]["data"]["status"], "environment_status_answer")

    def test_home_control_availability_summarizes_safe_action_families(self) -> None:
        tools = CatalogStatusTools(light_on=False)
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "今どんなホームアシスタントサーバーが使える?",
                "turn_id": "turn_home_control_availability_grounding",
            }
        )

        grounding = self._grounding(events)
        message = self._last_message(events)
        serialized_grounding = json.dumps(grounding, ensure_ascii=False, sort_keys=True)

        self.assertEqual(grounding["query_class"], "home_control_availability")
        self.assertEqual(grounding["home_control_bridge_class"], "bridge_available")
        self.assertEqual(grounding["ha_readiness_class"], "ha_state_surface_readable")
        self.assertEqual(grounding["available_action_families"], ["fan", "light"])
        self.assertIn("操作カタログ", message["speech"])
        self.assertIn("リビングの電気", message["speech"])
        self.assertIn("扇風機", message["speech"])
        self.assertNotIn('"action_id"', serialized_grounding)
        self.assertNotIn('"light_on"', serialized_grounding)
        self.assertNotIn('"fan_off"', serialized_grounding)
        self.assertNotIn("living_room_light", serialized_grounding)

    def test_appliance_state_question_keeps_ha_visible_proof_ceiling(self) -> None:
        tools = MockThoughtTools(
            light_on=True,
            appliance_states={"fan": "on", "door": "closed"},
        )
        events = ThoughtLoop(tools=tools).run_dicts(
            {
                **TURN,
                "text": "家電の状態はどうなっている?",
                "turn_id": "turn_appliance_state_grounding",
            }
        )

        grounding = self._grounding(events)
        message = self._last_message(events)

        self.assertEqual(grounding["query_class"], "appliance_state")
        self.assertEqual(grounding["proof_ceiling"], "HA_visible_state_only")
        self.assertGreaterEqual(grounding["readable_device_count"], 3)
        self.assertIn("読める状態", message["speech"])
        self.assertIn("HAやEnvironment State上の要約", message["speech"])
        self.assertIn("物理状態", message["speech"])
        self.assertNotIn("物理的に確認済み", message["speech"])
        self.assertIn("current_physical_appliance_state", grounding["does_not_prove"])

    def test_memory_dependent_status_uses_memory_as_reference_only(self) -> None:
        turn = {
            **TURN,
            "text": "前回の作業状況を踏まえて今の状況を教えて",
            "turn_id": "turn_memory_grounded_environment_status",
            "context_refs": {
                **TURN["context_refs"],
                "mock_memory_items": [
                    {
                        "scope": "session",
                        "memory_type": "work_continuity",
                        "status": "candidate",
                        "content": {
                            "summary": "synthetic prior work context",
                            "note": "X:/synthetic-private/raw-note.txt",
                        },
                    }
                ],
            },
        }

        events = ThoughtLoop(tools=MockThoughtTools(light_on=True)).run_dicts(turn)
        grounding = self._grounding(events)
        message = self._last_message(events)
        serialized_grounding = json.dumps(grounding, ensure_ascii=False, sort_keys=True)

        self.assertEqual(grounding["query_class"], "memory_grounded_status")
        self.assertEqual(grounding["memory_context_used_class"], "reference_only")
        self.assertEqual(grounding["current_authority_ordering_result"], "current_environment_before_memory")
        self.assertEqual(grounding["memory_context"]["current_authority"], False)
        self.assertTrue(grounding["memory_context"]["must_revalidate_current_state"])
        self.assertIn("関連メモリは1件", message["speech"])
        self.assertIn("最新のEnvironment Stateを優先", message["speech"])
        self.assertNotIn("synthetic prior work context", message["speech"])
        self.assertNotIn("synthetic-private", serialized_grounding)

    def test_stale_environment_state_is_worded_as_last_known_not_current_truth(self) -> None:
        events = ThoughtLoop(tools=MockThoughtTools(light_on=False)).run_dicts(
            {
                **TURN,
                "text": "今見えているものを踏まえて教えて",
                "turn_id": "turn_stale_environment_grounding",
                "context_refs": {
                    **TURN["context_refs"],
                    "mock_room_light_stale": "true",
                },
            }
        )

        grounding = self._grounding(events)
        message = self._last_message(events)

        self.assertEqual(grounding["query_class"], "current_environment_status")
        self.assertEqual(grounding["freshness_class"], "stale")
        self.assertEqual(grounding["proof_ceiling"], "external_observation_required")
        self.assertTrue(grounding["must_revalidate_current_state"])
        self.assertIn("最後に分かっている範囲", message["speech"])
        self.assertIn("現在状態としては断定", message["speech"])
        self.assertNotIn("現在の物理状態は確認済み", message["speech"])

    def _grounding(self, events: list[dict[str, object]]) -> dict[str, object]:
        return next(
            event["data"]
            for event in events
            if event["type"] == "environment.grounding_summary"
        )

    def _last_message(self, events: list[dict[str, object]]) -> dict[str, object]:
        return [
            event["data"]
            for event in events
            if event["type"] == "assistant.message"
        ][-1]


if __name__ == "__main__":
    import unittest

    unittest.main()
