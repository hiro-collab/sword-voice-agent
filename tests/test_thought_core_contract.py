import json
import sys
import threading
from pathlib import Path
from urllib import request

from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
THOUGHT_CORE_ROOT = REPO_ROOT / "services" / "thought-core"
sys.path.insert(0, str(THOUGHT_CORE_ROOT))

from thought_core.loop import ThoughtLoop  # noqa: E402
from thought_core.server import create_server  # noqa: E402
from thought_core.tools import MockThoughtTools  # noqa: E402


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
                "tool.started",
                "tool.result",
                "observation.received",
                "tool.started",
                "tool.result",
                "action.proposed",
                "assistant.speech_delta",
                "assistant.message",
                "tool.started",
                "tool.result",
                "tool.started",
                "tool.result",
                "observation.received",
                "assistant.speech_delta",
                "assistant.message",
                "turn.completed",
            ],
        )

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

    def test_tool_failure_requests_feedback_when_retry_exhausted(self) -> None:
        tools = MockThoughtTools(execute_failures_before_success=3)
        events = ThoughtLoop(tools=tools, max_execute_attempts=2).run_dicts(TURN)
        event_types = [event["type"] for event in events]

        self.assertIn("feedback.requested", event_types)
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["data"]["status"], "needs_feedback")
        self.assertEqual(len(tools.execute_calls), 2)

    def test_secret_values_are_redacted_from_events(self) -> None:
        tools = MockThoughtTools(include_secret_in_execute_result=True)
        events = ThoughtLoop(tools=tools).run_dicts(TURN)
        serialized = json.dumps(events, ensure_ascii=False)

        self.assertNotIn("mock-token-that-must-not-leak", serialized)
        self.assertIn("[REDACTED]", serialized)

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

